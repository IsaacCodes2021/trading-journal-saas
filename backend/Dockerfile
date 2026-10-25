# syntax=docker/dockerfile:1
#
# One image: the API and the built single-page app, served from one origin (ADR-010, ADR-011).
# The build context is the REPOSITORY ROOT (it needs both frontend/ and backend/):
#
#     docker build -f backend/Dockerfile -t tradejournal .
#
# Base images are pinned by digest (the tag is for humans); Dependabot's docker updater moves both together.

# ---- stage 1: build the front end ------------------------------------------------------------------------------
FROM node:24-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS web

# Same pnpm as `packageManager` in frontend/package.json, installed from the registry rather than via Corepack.
RUN npm install --global pnpm@12.8.1
WORKDIR /web

# Manifests first, so the install layer is cached until a dependency changes. pnpm-workspace.yaml holds the
# supply-chain settings (24-hour release delay; dependency build scripts fail the install).
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# ---- stage 2: the API image --------------------------------------------------------------------------------------
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

# uv installs exactly what uv.lock says, and verifies the hashes recorded there.
COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first so this layer is cached until the lock file changes. --frozen refuses to change the lock;
# --no-dev leaves out pytest and ruff.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/tradejournal ./tradejournal
COPY --from=web /web/dist ./web

# Run as an unprivileged user. The app is stateless: nothing is written to disk.
RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

# Fail closed: with TJ_ENV=production (the default here) the API refuses to start without an https TJ_APP_ORIGIN and
# a TJ_SESSION_SECRET, so an unconfigured container cannot run in development mode by accident. See api/config.py.
ENV TJ_ENV=production \
    TJ_WEB_DIST=/app/web

EXPOSE 8000

# Render (and most hosts) pass the port to listen on in $PORT. `exec` makes uvicorn PID 1, so it receives SIGTERM.
CMD ["sh", "-c", "exec uvicorn tradejournal.api.app:create_app --factory --no-access-log --host 0.0.0.0 --port ${PORT:-8000}"]
