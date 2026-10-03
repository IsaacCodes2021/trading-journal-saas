"""Request body size limits: the app's layer of "limits at the edge, the route and the streaming reader" (doc 04, T3).

The edge (Cloudflare) is the outermost cap and routes enforce their own exact limits (a screenshot is read in a
stream that aborts at 10 MB). This is the ceiling in between, so no route can be handed an unbounded body by accident:

- a declared `Content-Length` over the limit is refused before the app runs;
- a body with no `Content-Length` (chunked) is counted as the route reads it and refused when it passes the limit.

JSON bodies are small (the biggest is a notes body, a few KB), multipart uploads are not, so the two get different limits.
"""

from __future__ import annotations

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import problem_response

MULTIPART = b"multipart/form-data"


class BodyTooLarge(HTTPException):
    """An HTTPException on purpose: FastAPI re-raises those from body parsing, but turns any other exception raised
    while reading the body into a 400."""

    def __init__(self, limit: int) -> None:
        super().__init__(413, f"The request body is larger than the {limit} byte limit.")


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, json_limit: int, upload_limit: int) -> None:
        self.app = app
        self.json_limit = json_limit
        self.upload_limit = upload_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        limit = (
            self.upload_limit if headers.get(b"content-type", b"").lower().startswith(MULTIPART) else self.json_limit
        )

        declared = headers.get(b"content-length")
        if declared is not None:
            if not declared.isdigit():
                await problem_response(400, "The Content-Length header is not a number.")(scope, receive, send)
                return
            if int(declared) > limit:
                await problem_response(413, f"The request body is larger than the {limit} byte limit.")(
                    scope, receive, send
                )
                return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(limit)
            return message

        await self.app(scope, counting_receive, send)
