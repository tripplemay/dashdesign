# Baseline cloud server image. Build context = repo root:
#   docker build -f deploy/Dockerfile -t baseline-cloud .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first (better layer caching). Server needs only the cloud stack
# plus the baseline-domain subset — no image-processing libs.
COPY requirements-cloud.txt deploy/requirements-server.txt ./reqs/
RUN pip install --upgrade pip \
    && pip install -r reqs/requirements-cloud.txt -r reqs/requirements-server.txt

# App code: the Qt-free baseline domain, the server, the runtime shim, and the
# JSON schema the validator loads. (No ui/, scripts/, or image assets.)
COPY app_runtime.py ./
COPY baseline/ ./baseline/
COPY cloud/ ./cloud/
COPY docs/baseline/ ./docs/baseline/

EXPOSE 8000

# 2 workers is plenty for a small team; bump for more seats. Background merge
# tasks run per-worker with their own DB session.
CMD ["uvicorn", "cloud.server.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
