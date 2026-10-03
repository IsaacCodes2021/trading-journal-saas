"""Serve the built single-page app from the API container, on the same origin as the API (ADR-011)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

# First path segments that belong to the server. A GET to an unknown path under one of these is a real 404, never
# the SPA's index.html, so a mistyped API URL cannot come back as a 200 page of HTML.
RESERVED_PREFIXES = frozenset({"api", "auth", "files", "webhooks", "healthz", "readyz"})
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


class ImmutableStaticFiles(StaticFiles):
    """Vite writes content-hashed names into assets/, so a given URL never changes and can be cached for a year."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = IMMUTABLE
        return response


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Call last: the catch-all route below must never shadow an API route registered before it."""
    root = dist.resolve()
    if (root / "assets").is_dir():
        app.mount("/assets", ImmutableStaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request) -> Response:
        if full_path.split("/", 1)[0] in RESERVED_PREFIXES:
            raise HTTPException(404)
        # Top-level files in dist (favicon, robots.txt). `resolve` then `is_relative_to` is the traversal guard:
        # it also rejects `..` segments, symlinks that leave dist, and Windows drive paths.
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate, headers={"Cache-Control": REVALIDATE})
        index = root / "index.html"
        # History fallback: only for navigations (the browser asks for HTML), not for a missing script or image.
        if "text/html" in request.headers.get("accept", "") and index.is_file():
            return FileResponse(index, headers={"Cache-Control": REVALIDATE})
        raise HTTPException(404)
