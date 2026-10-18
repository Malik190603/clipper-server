import os, re, time, uuid, threading, subprocess

import yt_dlp
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

TOKEN = os.environ.get("APP_TOKEN", "").strip()
WORK = "/tmp/clips"
os.makedirs(WORK, exist_ok=True)

COOKIES = None
if os.environ.get("YT_COOKIES", "").strip():
    COOKIES = "/tmp/cookies.txt"
    with open(COOKIES, "w") as f:
        f.write(os.environ["YT_COOKIES"].strip() + "\n")

app = FastAPI(title="Clipper Server")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

jobs = {}
encode_lock = threading.Semaphore(1)  # CPU gratis: satu potongan sekaligus


class ClipReq(BaseModel):
    url: str
    start: float
    end: float
    height: int = 720


def check(token):
    if TOKEN and token != TOKEN:
        raise HTTPException(401, "Kunci server salah.")


def friendly(err: str) -> str:
    e = err or ""
    if re.search(r"Sign in to confirm|not a bot|cookies", e, re.I):
        return "YouTube memblokir server. Tambahkan cookies akun YouTube cadangan (YT_COOKIES) di pengaturan server."
    if re.search(r"Private video|members-only|age", e, re.I):
        return "Video privat, khusus member, atau dibatasi umur. Coba video publik lain."
    if re.search(r"unavailable|removed|not available", e, re.I):
        return "Video tidak tersedia di YouTube."
    return "Gagal mengambil video: " + e[-300:]


def work(jid: str, req: ClipReq):
    job = jobs[jid]
    out = os.path.join(WORK, jid + ".mp4")
    try:
        job["status"] = "membaca"
        h = max(240, min(1080, req.height))
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "format": f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b",
        }
        if COOKIES:
            opts["cookiefile"] = COOKIES
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(req.url, download=False)
        fmts = info.get("requested_formats") or [info]
        dur = req.end - req.start

        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for f in fmts:
            hdr = "".join(f"{k}: {v}\r\n" for k, v in (f.get("http_headers") or {}).items())
            cmd += ["-ss", f"{req.start:.3f}"]
            if hdr:
                cmd += ["-headers", hdr]
            cmd += ["-i", f["url"]]
        if len(fmts) >= 2:
            cmd += ["-map", "0:v:0", "-map", "1:a:0?"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        cmd += ["-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]

        job["status"] = "antre"
        with encode_lock:
            job["status"] = "memotong"
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if p.returncode != 0 or not os.path.exists(out):
            raise RuntimeError("ffmpeg: " + (p.stderr or "")[-300:])
        job.update(status="selesai", size=os.path.getsize(out), title=info.get("title"))
    except yt_dlp.utils.DownloadError as e:
        job.update(status="gagal", error=friendly(str(e)))
    except subprocess.TimeoutExpired:
        job.update(status="gagal", error="Waktu habis saat memotong video. Coba klip lebih pendek.")
    except Exception as e:
        job.update(status="gagal", error=friendly(str(e)) if "ffmpeg" not in str(e) else str(e)[-300:])


def janitor():
    while True:
        now = time.time()
        for name in os.listdir(WORK):
            p = os.path.join(WORK, name)
            if now - os.path.getmtime(p) > 3600:
                try:
                    os.remove(p)
                except OSError:
                    pass
        for jid in [k for k, v in jobs.items() if now - v["t"] > 3600]:
            jobs.pop(jid, None)
        time.sleep(300)


threading.Thread(target=janitor, daemon=True).start()


@app.get("/")
def home():
    return {"ok": True, "yt_dlp": yt_dlp.version.__version__, "cookies": bool(COOKIES), "token": bool(TOKEN)}


@app.post("/clip")
def clip(req: ClipReq, x_token: str = Header(default="")):
    check(x_token)
    if req.start < 0 or req.end <= req.start:
        raise HTTPException(400, "Waktu klip tidak valid.")
    if req.end - req.start > 600:
        raise HTTPException(400, "Klip maksimal 10 menit.")
    jid = uuid.uuid4().hex[:12]
    jobs[jid] = {"status": "baru", "t": time.time()}
    threading.Thread(target=work, args=(jid, req), daemon=True).start()
    return {"id": jid}


@app.get("/job/{jid}")
def job(jid: str, x_token: str = Header(default="")):
    check(x_token)
    if jid not in jobs:
        raise HTTPException(404, "Pekerjaan tidak ditemukan (server mungkin baru menyala ulang).")
    return jobs[jid]


@app.get("/file/{jid}")
def file(jid: str, x_token: str = Header(default=""), token: str = Query(default="")):
    check(x_token or token)
    p = os.path.join(WORK, re.sub(r"[^a-f0-9]", "", jid) + ".mp4")
    if not os.path.exists(p):
        raise HTTPException(404, "File tidak ditemukan.")
    return FileResponse(p, media_type="video/mp4")
