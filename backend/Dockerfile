# backend/Dockerfile
FROM python:3.12-slim

# 1) system deps  ────────────────────────────────────────────────
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        build-essential ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 2) app code  ──────────────────────────────────────────────────
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend /app

# 3) uvicorn via gunicorn  ──────────────────────────────────────
ENV PYTHONUNBUFFERED=true \
    PORT=10000                           # Render exposes this automatically

CMD ["gunicorn", "-c", "gunicorn_conf.py", "app.main:app"]
