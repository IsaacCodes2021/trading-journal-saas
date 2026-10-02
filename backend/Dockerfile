# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so this layer is cached until requirements.txt changes. The file is generated
# from uv.lock and carries hashes, so pip refuses anything that does not match.
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY tradejournal ./tradejournal

# Run as an unprivileged user; /data is the only writable location.
RUN useradd --system --uid 10001 --no-create-home appuser \
    && mkdir /data \
    && chown appuser /data
USER appuser
ENV TRADEJOURNAL_DATA_DIR=/data \
    NICEGUI_STORAGE_PATH=/data/.nicegui
VOLUME /data

EXPOSE 8765

# NOTE: ui/app.py still binds to 127.0.0.1 (a deliberate local-only safeguard), so a running container
# is not reachable from outside yet. Making the bind address configurable is part of M2 (config rewrite).
CMD ["python", "-m", "tradejournal.ui", "--no-browser", "--port", "8765"]
