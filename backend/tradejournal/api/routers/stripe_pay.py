"""`GET /billing/stripe/pay?cs=<checkout>`: Roundturn's own address for a Stripe checkout (docs/design/stripe-plan.md, P4.6).

Stripe's checkout is a page on checkout.stripe.com, so no Stripe script runs on any Roundturn page and no Content-Security-
Policy changes: this route only sends the browser there (303), and only for the checkout of the person signed in, while it
is open. A checkout already paid sends them back to Plan & billing with `?checkout=done` (the page then waits for Stripe's
word); one that ran out, someone else's, or none, back to Plan & billing. Not signed in: to Plan & billing, which asks for a
sign-in.

404 unless billing is on with the Stripe connector, so Paddle's `/billing/pay` (routers/pay.py) and this one are never both
live. Hidden from the API document: a page for the browser, not part of the API.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from ...billing.stripe import StripeBilling
from ...billing.types import BillingError
from ..deps import SESSION_COOKIE

logger = logging.getLogger("tj.billing")

router = APIRouter(include_in_schema=False)

PLAN_PAGE = "/settings/plan"


@router.api_route("/billing/stripe/pay", methods=["GET", "HEAD"])
def stripe_pay(request: Request, cs: str = "") -> Response:
    config = request.app.state.config
    services = request.app.state.services
    billing = getattr(services, "billing", None) if services is not None else None
    if not config.billing_enabled or billing is None or not isinstance(billing.provider, StripeBilling):
        return Response(status_code=404)
    session = services.sessions.lookup(request.cookies.get(SESSION_COOKIE))
    if session is None or not cs:
        return _go(PLAN_PAGE)
    try:
        destination = billing.provider.checkout_destination(cs, session.user_id)
    except BillingError as problem:
        # A malformed id, Stripe's no, or Stripe away: back to the page, which can try again.
        logger.warning("stripe payment link not followed: %s", problem.reason)
        return _go(PLAN_PAGE)
    return _go(destination)


def _go(url: str) -> Response:
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
