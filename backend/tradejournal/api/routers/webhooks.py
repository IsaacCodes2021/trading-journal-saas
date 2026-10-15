"""`POST /webhooks/billing`: the only way the payment provider changes anything here (the billing design, "Webhook").

No session cookie and no CSRF token: the signature over the raw bytes is the lock. Checks in order, the first that applies
decides: switched off (404); not JSON (415) or over 64 KB (413); signature missing or wrong (401, nothing stored, no detail);
then the event is stored once and applied in order (`pg/billing.apply_event`), and every outcome after the signature is a
200, so the provider stops retrying an event that was received, even one we chose not to apply. A database that is away is a
503, which the provider retries. Hidden from the API document: it is the provider's, not the app's.
"""

from __future__ import annotations

import logging

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ...billing.types import BadSignature

logger = logging.getLogger("tj.billing")

MAX_BODY = 64 * 1024

router = APIRouter(include_in_schema=False)


@router.post("/webhooks/billing")
async def billing_webhook(request: Request) -> Response:
    config = request.app.state.config
    services = request.app.state.services
    billing = getattr(services, "billing", None) if services is not None else None
    if not config.billing_enabled or billing is None:
        return Response(status_code=404)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return Response(status_code=415)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY:
        return Response(status_code=413)
    body = b""
    async for chunk in request.stream():  # read at most 64 KB and one byte, whatever the header said
        body += chunk
        if len(body) > MAX_BODY:
            return Response(status_code=413)
    try:
        applied = await run_in_threadpool(billing.handle_webhook, dict(request.headers), body)
    except BadSignature:
        return Response(status_code=401)
    except LookupError:  # no webhook database role configured on this server
        logger.error("billing webhook received, but TJ_WEBHOOK_DATABASE_URL is not set")
        return Response(status_code=503)
    except psycopg.OperationalError:  # the database is away: the provider retries later
        logger.error("billing webhook received while the database is unreachable")
        return Response(status_code=503)
    return JSONResponse({"received": True, "outcome": applied.outcome})
