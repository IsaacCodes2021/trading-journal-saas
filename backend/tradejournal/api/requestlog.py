"""One log line per request, and a request id that ties it to everything else logged while it ran.

The line has the method, the route, the status and how long it took, and nothing else about the request (see `logs`).
The route is the template (`/api/admin/v1/invites/{invite_id}/revoke`) when there is one, so ids do not make every line
different; a path nothing matched is written as it came, cut to 200 characters and scrubbed.

The request id is `X-Request-ID`: the caller's if it is a short plain token (so a support request can quote it and the
edge can supply one), otherwise a fresh random one. It is returned on the response so a person can read it out.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .logs import request_id_var, scrub

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
MAX_LOGGED_PATH = 200

# Polled every few seconds and asked for on every page: written only when they fail.
_QUIET_PATHS = ("/healthz", "/readyz")
_QUIET_PREFIXES = ("/assets/",)

log = logging.getLogger("tj.request")


def _inbound_id(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            text = value.decode("latin-1")
            return text if _SAFE_ID.fullmatch(text) else None
    return None


def _level(path: str, status: int) -> int:
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    if path in _QUIET_PATHS or path.startswith(_QUIET_PREFIXES):
        return logging.DEBUG
    return logging.INFO


class RequestLogMiddleware:
    """Pure ASGI, so streaming responses are untouched. Outermost, so it sees the refusals the layers inside make."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _inbound_id(scope) or str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id  # for the 500 handler, which runs outside this layer
        token = request_id_var.set(request_id)
        status = 500  # what it was if the app failed before it answered
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        failure: Exception | None = None
        try:
            await self.app(scope, receive, send_with_id)
        except Exception as error:
            failure = error
            raise
        finally:
            try:
                template = getattr(scope.get("route"), "path", None)
                raw = scope.get("path", "")
                # Scrubbed before it is cut: cutting first could leave half an address, which looks like nothing.
                path = template if template and not template.startswith("/{") else scrub(raw)[:MAX_LOGGED_PATH]
                log.log(
                    logging.ERROR if failure else _level(raw, status),
                    "request",
                    exc_info=failure,
                    extra={
                        "fields": {
                            "method": scope.get("method", ""),
                            "path": path,
                            "status": status,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                        }
                    },
                )
            finally:
                request_id_var.reset(token)
