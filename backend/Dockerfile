# backend/Dockerfile
FROM python:3.12-slim

# 1) OS packages ────────────────────────────────────────────────
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        build-essential \
        # (ffmpeg only if you really need it; drop it to save ~45 MB)
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 2) Python deps ────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Requirements trimmed: supabase and requests removed

# 3) App code ───────────────────────────────────────────────────
COPY . /app

# 4) Gunicorn entrypoint ────────────────────────────────────────
ENV PYTHONUNBUFFERED=1
# Let Render/Cloud Run inject $PORT; default to 8080 for local runs
CMD gunicorn -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:${PORT:-8080} \
    -w ${WEB_CONCURRENCY:-1} \
    app.main:app
