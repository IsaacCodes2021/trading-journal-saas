"""Importing Tradovate Performance exports: preview, import, history and undo."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile

from ...auth.iphash import hash_ip
from ...importer import UndoReport
from ...journal import clean_filename
from ...pg import importer as pg_importer
from ...timeutil import zone
from ..config import ApiConfig
from ..deps import (
    ERROR_RESPONSES,
    ConfigDep,
    ImportSlot,
    ServicesDep,
    TenantDep,
    WriteTenantDep,
    limited_per_person,
)
from ..refusals import refusals, sentence
from ..schemas import Problem
from ..schemas.imports import (
    ImportIssueOut,
    ImportPreviewOut,
    ImportRecordOut,
    PreviewRowOut,
    UndoPreviewOut,
)
from ..schemas.journal import MAX_ID, AccountRefOut, PathId

router = APIRouter(
    prefix="/api/v1",
    tags=["imports"],
    responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}, 413: {"model": Problem}},
)

MAX_IMPORT_BYTES = 10 * 1024 * 1024  # a Performance export is a few hundred KB; the request-level cap sits just above
PREVIEW_ROWS_SHOWN = 100
CSV_TIME = "%m/%d/%Y %H:%M:%S"  # how a Tradovate export writes a time

AccountField = Annotated[int, Form(ge=1, le=MAX_ID)]


def _read(file: UploadFile) -> tuple[str, bytes]:
    data = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "That file is too large to import (10 MB at most).")
    return clean_filename(file.filename)[:200] or "export.csv", data


def _ip(request: Request, config: ApiConfig) -> str | None:
    return hash_ip(config.session_secret, request.client.host if request.client else None)


def _record(row: dict) -> ImportRecordOut:
    return ImportRecordOut(
        id=row["id"],
        imported_at=row["imported_at"],
        file_name=row["filename"],
        account=AccountRefOut(
            id=row["account_id"], name=row["account_name"], firm=row["account_firm"], account_type=row["account_type"]
        ),
        rows_added=row["rows_added"],
        trades_built=row["trades_affected"],
        undone_at=row["undone_at"],
    )


def _undo_out(report: UndoReport) -> UndoPreviewOut:
    return UndoPreviewOut(
        import_id=report.import_id,
        rows_removed=report.rows_removed,
        rows_kept=report.rows_kept,
        trades_removed=report.trades_removed,
        trades_with_journal_removed=report.trades_with_journal_removed,
        trades_changed=report.trades_changed,
    )


@router.post(
    "/imports/preview",
    response_model=ImportPreviewOut,
    dependencies=[limited_per_person(lambda limits: limits.import_preview), ImportSlot],
)
def preview_import(account_id: AccountField, file: UploadFile, tenant: WriteTenantDep) -> ImportPreviewOut:
    """What importing this file into this account would do: counts, problems, and the first rows with a status each.
    Nothing is saved (the import runs inside a transaction that is rolled back), so the preview is exactly what the
    real import would do."""
    name, data = _read(file)
    with refusals(what="account"), tenant.session() as conn:
        rows, tz_name = pg_importer.preview_rows(conn, account_id, data, PREVIEW_ROWS_SHOWN)
        report = pg_importer.import_files(conn, account_id, [(name, data)], dry_run=True).files[0]
    file_zone = zone(tz_name)
    return ImportPreviewOut(
        file_name=name,
        file_bytes=len(data),
        fatal=report.fatal,
        rows_parsed=report.rows_parsed,
        rows_added=report.rows_added,
        duplicates=report.duplicates,
        duplicates_in_file=report.duplicates_in_file,
        duplicates_other_accounts=report.duplicates_other_accounts,
        errors=[ImportIssueOut(line=e.line, message=e.message) for e in report.errors],
        warnings=[ImportIssueOut(line=w.line, message=w.message) for w in report.warnings],
        trades_built=report.trades_built,
        rows=[
            PreviewRowOut(
                line=r.pair.line,
                status=r.status,  # type: ignore[arg-type]  # one of the three PAIR_* words
                account=r.account_name,
                symbol=r.pair.symbol,
                bought_at=r.pair.bought_at.astimezone(file_zone).strftime(CSV_TIME),
                sold_at=r.pair.sold_at.astimezone(file_zone).strftime(CSV_TIME),
                qty=r.pair.qty,
                buy_price=r.pair.buy_price,
                sell_price=r.pair.sell_price,
                pnl=r.pair.pnl,
            )
            for r in rows
        ],
        rows_shown_limit=PREVIEW_ROWS_SHOWN,
    )


@router.post(
    "/imports",
    response_model=ImportRecordOut,
    status_code=201,
    dependencies=[limited_per_person(lambda limits: limits.import_commit), ImportSlot],
)
def import_file(
    account_id: AccountField,
    file: UploadFile,
    request: Request,
    tenant: WriteTenantDep,
    services: ServicesDep,
    config: ConfigDep,
) -> ImportRecordOut:
    """Import the file into the account. Rows already stored are left alone, so importing a file twice is safe."""
    name, data = _read(file)
    with refusals(what="account"), tenant.session() as conn:
        report = pg_importer.import_files(conn, account_id, [(name, data)]).files[0]
        if report.fatal:
            raise HTTPException(422, sentence(report.fatal))  # nothing was written for a refused file
        row = next(r for r in pg_importer.list_imports(conn, account_id) if r["id"] == report.import_id)
    # What is recorded is counts only, never the file's name or anything in it (the log holds no one's content).
    services.audit.record(
        tenant.user_id,
        "import",
        ip_hash=_ip(request, config),
        meta={"import_id": row["id"], "rows_added": row["rows_added"], "trades_built": row["trades_affected"]},
    )
    return _record(row)


@router.get("/imports", response_model=list[ImportRecordOut])
def list_imports(tenant: TenantDep) -> list[ImportRecordOut]:
    """Every import, newest first (undone ones included, marked)."""
    with tenant.session() as conn:
        rows = pg_importer.list_imports(conn)
    return [_record(r) for r in rows]


def _undo(import_id: int, tenant: WriteTenantDep, *, dry_run: bool) -> UndoPreviewOut:
    with refusals(what="import"), tenant.session() as conn:
        try:
            report = pg_importer.undo_import(conn, import_id, dry_run=dry_run)
        except ValueError as problem:  # already undone
            raise HTTPException(409, sentence(problem)) from None
    return _undo_out(report)


@router.post("/imports/{import_id}/undo-preview", response_model=UndoPreviewOut, dependencies=[ImportSlot])
def undo_preview(import_id: PathId, tenant: WriteTenantDep) -> UndoPreviewOut:
    """What undoing the import would remove, without removing it."""
    return _undo(import_id, tenant, dry_run=True)


@router.post("/imports/{import_id}/undo", response_model=UndoPreviewOut, dependencies=[ImportSlot])
def undo_import(
    import_id: PathId, request: Request, tenant: WriteTenantDep, services: ServicesDep, config: ConfigDep
) -> UndoPreviewOut:
    """Remove what the import brought in (rows another import also holds stay) and regroup what is left."""
    done = _undo(import_id, tenant, dry_run=False)
    services.audit.record(
        tenant.user_id,
        "import_undone",
        ip_hash=_ip(request, config),
        meta={"import_id": import_id, "rows_removed": done.rows_removed, "trades_removed": done.trades_removed},
    )
    return done
