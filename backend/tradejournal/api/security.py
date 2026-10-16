"""Security headers for every response (ADR-011, doc 04).

The Content-Security-Policy is strict on purpose: no inline script, no inline style, no `eval`. Spike S1 showed the
chosen libraries live within it (a plain-text ECharts tooltip, Tiptap with `injectCSS: false`); anything that needs
`unsafe-inline` is a bug in the page, not a reason to loosen this.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import ApiConfig

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

# The payment page only (routers/pay.py): Paddle.js from Paddle's address, Paddle's checkout in a frame, and Paddle's API
# from the page. Nowhere else may load a script from another site. Each origin is to be confirmed in the browser's console
# during the sandbox tests (docs/design/paddle-integration.md section 6.2), and only what is reported added.
# Reported in the sandbox on 2026-10-16: Paddle.js loads its stylesheet from its CDN (`sandbox-cdn.paddle.com` in the
# sandbox, `cdn.paddle.com` live) and sets inline styles on the frame it opens; without both, the checkout is drawn as a
# small unstyled box and cannot be completed. Inline styles only: scripts stay 'self' and Paddle's script address.
PAY_PATH = "/billing/pay"
PAY_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.paddle.com",
        "style-src 'self' 'unsafe-inline' https://cdn.paddle.com https://sandbox-cdn.paddle.com",
        "img-src 'self' data: https://*.paddle.com",
        "font-src 'self'",
        "connect-src 'self' https://*.paddle.com",
        "frame-src https://buy.paddle.com https://sandbox-buy.paddle.com",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

_STATIC_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HSTS = "max-age=63072000; includeSubDomains"

# Responses under these paths are per-user and must never sit in a browser or proxy cache.
NO_STORE_PREFIXES = ("/api/", "/auth/", "/files/", "/webhooks/", "/billing/")


def security_headers(path: str, config: ApiConfig) -> dict[str, str]:
    headers = dict(_STATIC_HEADERS)
    headers["X-App-Version"] = config.app_version
    if config.production:
        headers["Strict-Transport-Security"] = HSTS
    if path.startswith(NO_STORE_PREFIXES):
        headers["Cache-Control"] = "no-store"
    if path == PAY_PATH:
        headers["Content-Security-Policy"] = PAY_CSP
        # A bank's 3-D Secure window or a wallet may open from Paddle's checkout as a popup; the page still cannot be
        # reached from a window someone else opened.
        headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    return headers


class SecurityHeadersMiddleware:
    """Pure ASGI, so streaming responses and background tasks are untouched (unlike `BaseHTTPMiddleware`)."""

    def __init__(self, app: ASGIApp, config: ApiConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = security_headers(scope["path"], self.config)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


def next_path(value: str | None) -> str:
    """Open-redirect defense for `?next=`: only same-origin absolute paths survive; anything else becomes `/`."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    return value
