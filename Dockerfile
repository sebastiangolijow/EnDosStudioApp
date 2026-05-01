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

# Non-root runtime user
RUN groupadd -r app && useradd -r -g app -d /app app

COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
