# Optical modem suite — capture station + dataset pipeline + training + S2C.
#
# One image, one process, the whole pipeline:
#   /             dashboard        /dataset  dataset creation (incl. the capture tool)
#   /train        YOLO26-pose training as background jobs
#   /evaluate     corner error / decode rate / loopback verification
#   /models       model registry ("promote to active")
#   /s2c          the S2C app: send bytes over a screen, read them with a camera
#   /projects     projects in development / finished (database-backed)
#
# State: Postgres (metadata) + Redis (job events, locks) per docker-compose.yml.
# Without them the app degrades to SQLite in /data and an in-process bus, so a
# bare `docker run` still works.
#
#   docker build -t optical-modem-suite .
#   docker run -p 8000:8000 -e APP_PASSWORD=secret -v suite-data:/data optical-modem-suite
#
# CPU-only on purpose (the target is a 4-core x64 VPS with 8 GB RAM). torch comes
# from the CPU wheel index: the CUDA build would add ~2.5 GB and buy nothing
# without a GPU. Drop in `--device 0` on the Train page if one appears.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=4 \
    MKL_NUM_THREADS=4 \
    YOLO_OFFLINE=1 \
    YOLO_CONFIG_DIR=/data/.ultralytics

# OpenCV needs libGL/libglib; ultralytics/torch need libgomp; curl for healthchecks
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libglib2.0-0 libgomp1 libgl1 curl procps \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# dependencies first: this layer is the expensive one and rarely changes
COPY requirements-suite.txt ./
RUN pip install --no-cache-dir -r requirements-suite.txt

COPY optical_modem ./optical_modem
COPY scripts ./scripts
COPY tests ./tests
COPY docs ./docs
# the bundled baseline detector (and any other shipped checkpoints)
COPY models ./models
COPY README.md ./

RUN find /app -name '__pycache__' -type d -prune -exec rm -rf {} + || true

ENV PYTHONPATH=/app \
    DATA_DIR=/data
RUN mkdir -p /data/datasets /data/runs /data/models /data/jobs /data/sessions

# unprivileged; /data is created world-writable so a bind-mounted host directory
# stays readable by the mapped user
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin suite \
 && chown -R suite:suite /app /data
USER suite

EXPOSE 8889

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8889/healthz', timeout=4).status==200 else 1)"

# ONE worker: sessions, jobs and transfers live in this process
CMD ["python", "-m", "uvicorn", "optical_modem.suite.app:app", \
     "--host", "127.0.0.1", "--port", "8889", "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
