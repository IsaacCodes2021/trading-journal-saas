"""`GET /billing/pay`: the one page where Paddle's script runs (docs/design/paddle-integration.md section 6).

Paddle.js has to come from Paddle's own address, so it never runs inside the app, where a trader's notes, trades and
pictures are: this small page loads it under a Content-Security-Policy of its own (`security.PAY_CSP`), and every other page
keeps the strict one. The page is the front end's second entry (`frontend/pay.html`, built into `dist/pay.html`); the
server fills its two placeholders, the client-side token and the environment, both public by design. No sign-in: Paddle's
emails link here too (with `?_ptxn=txn_...` to update a card), and the page shows nothing of anyone's.

404 unless billing is on with the Paddle connector. A server with no built page answers 503 rather than a broken page.
"""

from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from ...billing.paddle import PaddleBilling

router = APIRouter(include_in_schema=False)

TOKEN_SLOT = "__TJ_PADDLE_TOKEN__"  # noqa: S105 - where the public client-side token goes, not a secret
ENV_SLOT = "__TJ_PADDLE_ENV__"


@lru_cache(maxsize=4)
def _page(path: Path, modified: float) -> str | None:  # `modified` keys the cache: a new build is read again
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if TOKEN_SLOT in text and ENV_SLOT in text else None


@router.api_route("/billing/pay", methods=["GET", "HEAD"])
def pay_page(request: Request) -> Response:
    config = request.app.state.config
    services = request.app.state.services
    billing = getattr(services, "billing", None) if services is not None else None
    if not config.billing_enabled or billing is None or not isinstance(billing.provider, PaddleBilling):
        return Response(status_code=404)
    settings = billing.provider.settings
    path = Path(config.web_dist) / "pay.html"
    try:
        template = _page(path, path.stat().st_mtime)
    except OSError:
        template = None
    if template is None or settings.client_token is None:
        return Response("The payment page is not available on this server.", status_code=503, media_type="text/plain")
    page = template.replace(TOKEN_SLOT, html.escape(settings.client_token, quote=True)).replace(
        ENV_SLOT, html.escape(settings.environment, quote=True)
    )
    return HTMLResponse(page)
