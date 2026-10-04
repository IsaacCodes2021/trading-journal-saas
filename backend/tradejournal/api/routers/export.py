"""Download everything you have stored (requirement F-A5; the file's layout is in `pg/export.py`).

A synchronous download: the ZIP is built while the request waits, then streamed. Doc 03 imagines a background job, a
copy in object storage and an emailed link; those need the file store (M6) and an email provider, and a journal this
size does not need them yet. Nothing here prevents moving to them later: the ZIP is what a job would produce.

Two guards stand in for the design's "one an hour": only a few exports build at once on this process (the rest are told
to try again), and each one is written to the audit log. A per-person limit arrives with the rate limits (M6).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import IO

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...auth.iphash import hash_ip
from ...pg import export as pg_export
from ..deps import ERROR_RESPONSES, ClockDep, ConfigDep, ServicesDep, TenantDep, limited_per_person
from ..sanitize import sanitize_notes
from ..schemas import Problem

MAX_AT_ONCE = 2
CHUNK = 64 * 1024
_RUNNING = threading.BoundedSemaphore(MAX_AT_ONCE)

router = APIRouter(prefix="/api/v1", tags=["export"], responses=ERROR_RESPONSES)


def _chunks(file: IO[bytes]) -> Iterator[bytes]:
    try:
        while chunk := file.read(CHUNK):
            yield chunk
    finally:
        file.close()


@router.get(
    "/export",
    dependencies=[limited_per_person(lambda limits: limits.export)],
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "A ZIP of everything stored for the account (see manifest.json inside)",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        },
        429: {"model": Problem, "description": "Too many exports are being built right now; try again shortly"},
    },
)
def download_export(
    request: Request, tenant: TenantDep, services: ServicesDep, config: ConfigDep, clock: ClockDep
) -> StreamingResponse:
    """Everything stored for this account, as one ZIP: CSV tables, notes as HTML and text, and a manifest with checksums."""
    account = services.accounts.load(tenant.user_id)
    if account is None:
        raise HTTPException(401, "Not signed in.")
    if not _RUNNING.acquire(blocking=False):
        raise HTTPException(
            429,
            "Your data is being gathered for other downloads right now. Try again in a minute.",
            {"Retry-After": "30"},
        )
    now = clock()
    try:
        with tenant.session(snapshot=True) as conn:  # one moment across every table
            result = pg_export.build(
                conn,
                profile={
                    "account": {"email": account.user.email, "id": str(account.user.id)},
                    "settings": account.settings.model_dump(),
                },
                exported_at=now,
                app_version=config.app_version,
                clean_html=sanitize_notes,
            )
    finally:
        _RUNNING.release()
    services.audit.record(
        tenant.user_id,
        "export",
        ip_hash=hash_ip(config.session_secret, request.client.host if request.client else None),
        meta={"bytes": result.size, "trades": result.counts["trades.csv"], "notes": result.counts["notes"]},
    )
    return StreamingResponse(
        _chunks(result.file),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="roundturn-export-{now.date().isoformat()}.zip"',
            "Content-Length": str(result.size),
            "Cache-Control": "no-store",
        },
    )
