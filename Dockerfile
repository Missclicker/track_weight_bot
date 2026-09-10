# Multi-arch (amd64 / arm64): all dependencies ship pure-python or aarch64 wheels.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Kept deliberately simple: the whole project is installed in one step (the image rebuilds
# dependencies on every code change - a few seconds on a tiny bot, not worth a two-stage dance).
COPY pyproject.toml README.md ./
COPY bot ./bot
RUN pip install --no-cache-dir .

# Run as an unprivileged user; secrets are mounted read-only by docker-compose.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/secrets \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "bot"]
