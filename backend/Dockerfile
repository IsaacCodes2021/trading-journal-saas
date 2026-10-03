# syntax=docker/dockerfile:1
FROM python:3.12-slim

# uv installs exactly what uv.lock says, and verifies the hashes recorded there. The image is pinned by
# digest (the tag is for humans); Dependabot's docker updater moves both together.
COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first so this layer is cached until the lock file changes. --frozen refuses to change
# the lock; --no-dev leaves out pytest and ruff.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

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
