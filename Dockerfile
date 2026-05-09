# syntax=docker/dockerfile:1.6

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for psycopg2 + Pillow. Slim removes them after install.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libjpeg-dev \
        zlib1g-dev \
        curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pick which requirements file to install. dev for local, prod for production.
ARG REQUIREMENTS=dev
COPY requirements/ requirements/
RUN pip install -r requirements/${REQUIREMENTS}.txt

# Bake the rembg ONNX model into the image so cold-starts in prod don't
# block 5-10 s downloading from GitHub Releases. Runs as root, then we
# move the cache to a path the non-root `app` user can read. Adds
# ~170 MB to the image; controlled, deterministic, no rate-limit risk.
RUN python -c "from rembg import new_session; new_session('isnet-general-use')" \
 && mkdir -p /app/.u2net \
 && cp -r /root/.u2net/. /app/.u2net/
ENV U2NET_HOME=/app/.u2net

# Non-root runtime user
RUN groupadd -r app && useradd -r -g app -d /app app \
 && chown -R app:app /app/.u2net

COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
