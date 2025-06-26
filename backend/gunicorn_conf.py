# backend/gunicorn_conf.py
import multiprocessing

bind = "0.0.0.0:10000"   # matches Dockerfile ENV PORT
workers = (multiprocessing.cpu_count() * 2) + 1
worker_class = "uvicorn.workers.UvicornWorker"
keepalive = 30
timeout = 120
