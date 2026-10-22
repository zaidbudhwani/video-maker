# Always-on hosting: build once, run on any small cloud server (Fly.io, Railway, a VPS...).
# NOT YET BUILT OR RUN anywhere (no Docker on the development computer): build it once, then run  docker run --rm <image> python check_setup.py
# and render one test video to see the captions use a sensible font (the caption style asks for Arial; fontconfig maps it to Liberation Sans).
# Never add AI-generated footage, voice or images to this tool.
# Needs ~4 GB RAM and 2+ CPUs for Whisper. Mount a disk at /data: videos, accounts and settings live there.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl libgl1 libglib2.0-0 libgomp1 fonts-liberation fontconfig \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Exactly the tested versions (requirements.lock.txt lists every package, so --no-deps is right: see AGENTS.md "Known sharp edges")
COPY requirements.lock.txt requirements-nodeps.txt ./
RUN pip install --no-cache-dir --no-deps -r requirements.lock.txt && pip install --no-cache-dir --no-deps -r requirements-nodeps.txt
# Background-noise remover (Linux build of the same program the Windows copy uses)
RUN mkdir -p assets/tools && curl -fsSL -o assets/tools/deep-filter \
    https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-unknown-linux-musl \
    && chmod +x assets/tools/deep-filter
# Face-detection model for talking videos (not in git: too big for it, and fetched here instead)
RUN mkdir -p assets/models && curl -fsSL -o assets/models/blaze_face_short_range.tflite \
    https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite
# Speech-to-text model baked into the image so the first video doesn't sit "stuck" downloading 500 MB
ENV HF_HOME=/opt/hf
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
COPY . .

# Run as a normal user, not root. /data is where videos, accounts and settings live: if you mount a disk there, make it writable by uid 1000.
RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown -R app:app /app /data /opt/hf
USER app

ENV ALEX_DATA_DIR=/data ALEX_HOSTED=1 PORT=8000
EXPOSE 8000
HEALTHCHECK CMD curl -fs http://localhost:8000/healthz || exit 1
# Set ADMIN_USER + ADMIN_PASSWORD (first account), ANTHROPIC_API_KEY and optionally APP_PASSWORD (creates "alex")
CMD ["sh", "-c", "uvicorn web.app:app --host 0.0.0.0 --port ${PORT} --no-proxy-headers"]
