"""A trade's screenshots: add one, delete one, and the route that serves the image (doc 03 section 6).

One image per upload request: a pasted screenshot is one file, and a drop of several is sent one after another by the page,
so each request stays inside the multipart body limit (12 MB) with its own check, limit and answer.

**Serving.** `GET /files/{id}` is a plain GET (the session cookie rides along on an `<img>`, and a safe method needs no
CSRF token). It loads the row under row-level security, so another person's id is the same 404 as a missing one, and
answers with the file's bytes under the type recorded when it was accepted, `X-Content-Type-Options: nosniff` (set for every
response) and `no-store` (every `/files/` response is per person, `api/security.py`). The file is never given a name chosen
by its uploader in a header.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response

from ...filestore import FileMissing
from ...journal import clean_filename
from ...pg import screenshots as pg_screenshots
from ..deps import ERROR_RESPONSES, FilesDep, TenantDep, WriteTenantDep, limited_per_person
from ..refusals import refusals, require_row
from ..schemas import Problem
from ..schemas.journal import PathId
from ..schemas.trades import ScreenshotOut

log = logging.getLogger("tj.files")

router = APIRouter(
    tags=["screenshots"],
    responses={**ERROR_RESPONSES, 404: {"model": Problem}, 413: {"model": Problem}, 503: {"model": Problem}},
)

MAX_BYTES = pg_screenshots.MAX_FILE_BYTES
IMAGE_TYPES = {
    "image/png": {},
    "image/jpeg": {},
    "image/gif": {},
    "image/webp": {},
}


def file_url(screenshot_id: int) -> str:
    return f"/files/{screenshot_id}"


def screenshot_out(shot: pg_screenshots.Shot) -> ScreenshotOut:
    return ScreenshotOut(
        id=shot.id, original_name=shot.original_name, created_at=shot.created_at, src=file_url(shot.id)
    )


def _read(file: UploadFile) -> tuple[str, bytes]:
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"That image is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    return clean_filename(file.filename), data


@router.post(
    "/api/v1/trades/{trade_id}/screenshots",
    response_model=ScreenshotOut,
    status_code=201,
    dependencies=[limited_per_person(lambda limits: limits.screenshot_upload)],
)
def add_screenshot(trade_id: PathId, file: UploadFile, tenant: WriteTenantDep, files: FilesDep) -> ScreenshotOut:
    """Add an image (PNG, JPEG, GIF or WebP, up to 10 MB and 50 million pixels) to a trade."""
    name, data = _read(file)
    with refusals(what="trade"), tenant.session() as conn:
        shot = pg_screenshots.add(conn, files, tenant.user_id, trade_id, name, data)
    return screenshot_out(shot)


@router.delete("/api/v1/trades/{trade_id}/screenshots/{screenshot_id}", response_model=list[ScreenshotOut])
def delete_screenshot(
    trade_id: PathId, screenshot_id: PathId, tenant: WriteTenantDep, files: FilesDep
) -> list[ScreenshotOut]:
    """Delete one screenshot of a trade. Answers with the ones that are left."""
    with refusals(what="screenshot"), tenant.session() as conn:
        require_row(conn, "trades", trade_id, "trade")
        shot = pg_screenshots.get(conn, screenshot_id)
        if shot is None or shot.trade_id != trade_id:
            raise LookupError(f"no screenshot {screenshot_id} on trade {trade_id}")
        pg_screenshots.delete(conn, screenshot_id)
        left = pg_screenshots.for_trade(conn, trade_id)
    try:
        files.delete(
            shot.key
        )  # only now that the delete has committed: a rolled-back delete must not have lost the file
    except OSError as problem:
        # The screenshot is gone from the person's journal, which is what they asked for; a store that is down must not
        # turn that into an error they would retry into a 404. The file is left for the orphan report.
        log.warning("a deleted screenshot's file could not be removed", exc_info=problem)
    return [screenshot_out(s) for s in left]


@router.get(
    "/files/{screenshot_id}",
    response_class=Response,
    responses={200: {"description": "The image itself", "content": IMAGE_TYPES}},
)
def get_file(screenshot_id: PathId, tenant: TenantDep, files: FilesDep) -> Response:
    """The bytes of one of the signed-in person's screenshots."""
    with tenant.session() as conn:
        shot = pg_screenshots.get(conn, screenshot_id)
    if shot is None:
        raise HTTPException(404, "That screenshot does not exist.")
    try:
        data = files.get(shot.key)
    except FileMissing:
        raise HTTPException(404, "That screenshot's file is missing.") from None
    extension = shot.key.rsplit(".", 1)[-1] if "." in shot.key else "img"
    return Response(
        content=data,
        media_type=shot.content_type,
        headers={"Content-Disposition": f'inline; filename="screenshot-{shot.id}.{extension}"'},
    )
