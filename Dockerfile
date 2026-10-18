FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
COPY --chown=user app.py .

EXPOSE 7860
# yt-dlp diperbarui tiap server menyala agar tetap cocok dengan perubahan YouTube
CMD ["sh", "-c", "pip install --no-cache-dir --user -U 'yt-dlp[default]' >/dev/null 2>&1; uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
