"""Importing exports, preview, import, history and undo: Tradovate's Performance report (one file, fill pairs),
TradeSea's Orders export (one file per page of its list, read together as one import of filled orders), the Round
Turn Monitor's log from NinjaTrader (one account's round turns, stored as orders), and Roundturn's own import file, which
an AI chatbot writes from any other platform's export (round turns, stored as orders)."""

from __future__ import annotations

from datetime import tzinfo
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ... import finance
from ...auth.iphash import hash_ip
from ...importer import FileImportReport, UndoReport
from ...journal import clean_filename
from ...parser import RowIssue
from ...pg import accounts as pg_accounts
from ...pg import importer as pg_importer
from ...pg import settings as pg_settings
from ...platforms import detect, other, tradesea
from ...timeutil import zone
from ..config import ApiConfig
from ..deps import (
    ERROR_RESPONSES,
    HEAVY,
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
    CommissionCheckOut,
    ImportIssueOut,
    ImportPreviewOut,
    ImportRecordOut,
    LogAccountOut,
    OpenPositionOut,
    PreviewRowOut,
    UndoPreviewOut,
)
from ..schemas.journal import MAX_ID, AccountRefOut, PathId
from .accounts import fee_rate_shown

router = APIRouter(
    prefix="/api/v1",
    tags=["imports"],
    responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}, 413: {"model": Problem}},
)

MAX_IMPORT_BYTES = 10 * 1024 * 1024  # a Performance export is a few hundred KB; the request-level cap sits just above
MAX_FILES = 50  # TradeSea's pages hold 10 orders each: 50 pages is a very busy day
PREVIEW_ROWS_SHOWN = 100
CSV_TIME = "%m/%d/%Y %H:%M:%S"  # how a Tradovate export writes a time
ONE_TRADOVATE_FILE = "Tradovate's Performance report is one file. Choose one file at a time."
RATE_PLACES = Decimal("0.0001")  # a fee rate can be finer than a cent ($0.625 per contract)

AccountField = Annotated[int, Form(ge=1, le=MAX_ID)]
# Several parts named `file`: TradeSea's pages of one day. A single part, as before, is a list of one.
FilesField = Annotated[list[UploadFile], File(description="The export; for TradeSea, one file per page of its list")]
# Where the files come from: Tradovate (the default, as before the choice existed), TradeSea, or `auto`, told from the
# files' header row (files dropped onto the page).
# `other` is Roundturn's own import file, from any other platform.
PlatformField = Annotated[Literal["tradovate", "tradesea", "round_turn_monitor", "other", "auto"], Form()]
# The Round Turn Monitor's log holds every account NinjaTrader tracks: the one to import (none: the preview's pick).
LogAccountField = Annotated[str | None, Form(min_length=1, max_length=200)]


def _read(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    if len(files) > MAX_FILES:
        raise HTTPException(422, f"Choose at most {MAX_FILES} files at a time.")
    read: list[tuple[str, bytes]] = []
    total = 0
    for file in files:
        data = file.file.read(MAX_IMPORT_BYTES + 1 - total)
        total += len(data)
        if total > MAX_IMPORT_BYTES:
            many = " together" if len(files) > 1 else ""
            raise HTTPException(413, f"That is too large to import{many} (10 MB at most).")
        read.append((clean_filename(file.filename)[:200] or "export.csv", data))
    return read


def _resolve(asked: str, files: list[tuple[str, bytes]]) -> tuple[str, bool, str | None, str | None]:
    """The platform to read the files as, whether it was told from them, the platform they look like when that is not
    the one asked for, and why they are refused before being read (None: not refused)."""
    found, problem = detect.platform_of_files([data for _, data in files])
    if asked == detect.AUTO:
        if found is None:
            return detect.TRADOVATE, True, None, problem
        platform, recognised = found, True
    else:
        if problem == detect.MIXED:
            return asked, False, None, problem
        if found is not None and found != asked:
            if len(files) > 1:
                looks = f"These look like files from {detect.NAMES[found]}"
            else:
                looks = f"This looks like a file from {detect.NAMES[found]}"
            return asked, False, found, f"{looks}, not {detect.NAMES[asked]}."
        platform, recognised = asked, False
    if platform == detect.TRADOVATE and len(files) > 1:
        return platform, recognised, None, ONE_TRADOVATE_FILE
    return platform, recognised, None, None


def _source_platform(source: str) -> str:
    return detect.SOURCES.get(source, detect.TRADOVATE)


def _ip(request: Request, config: ApiConfig) -> str | None:
    return hash_ip(config.session_secret, request.client.host if request.client else None)


def _record(row: dict) -> ImportRecordOut:
    return ImportRecordOut(
        id=row["id"],
        platform=_source_platform(row["source"]),  # type: ignore[arg-type]  # one of the Platform words
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


def _refused(platform: str, files: list[tuple[str, bytes]], fatal: str, **extra: object) -> ImportPreviewOut:
    return ImportPreviewOut(
        platform=platform,  # type: ignore[arg-type]  # one of the Platform words
        file_name=", ".join(name for name, _ in files)[:500],
        file_count=len(files),
        file_bytes=sum(len(data) for _, data in files),
        fatal=fatal,
        rows_parsed=0,
        rows_added=0,
        duplicates=0,
        duplicates_in_file=0,
        duplicates_other_accounts={},
        errors=[],
        warnings=[],
        trades_built=0,
        rows=[],
        rows_shown_limit=PREVIEW_ROWS_SHOWN,
        **extra,  # type: ignore[arg-type]
    )


def _shown_zone(report: FileImportReport, setting: str) -> tzinfo:
    """The zone the table shows times in, so they read as the file wrote them: TradeSea's own zone when every time had
    the same one ("PST"), or the one UTC offset every time of Roundturn's own import file had ("UTC-05:00"), else the
    CSV time zone setting (for example, files that cross a daylight saving change)."""
    zones = [name for name in report.time_zone.split(", ") if name]
    if report.platform == detect.TRADESEA and len(zones) == 1:
        return tradesea.display_zone(zones[0])
    if report.platform == detect.OTHER and len(zones) == 1:
        return other.display_zone(zones[0]) or zone(setting)
    return zone(setting)


def _zone_label(report: FileImportReport, setting: str) -> str:
    """The zone the preview says the times are in. Roundturn's own import file stores every offset it had, but shows
    times in the setting's zone when it had more than one."""
    if report.platform == detect.OTHER and len([name for name in report.time_zone.split(", ") if name]) != 1:
        return setting
    return report.time_zone or setting


def _commission_checks(report: FileImportReport, schedule: dict[str, Decimal]) -> list[CommissionCheckOut]:
    checks = []
    for line in report.commissions:
        round_trips = Decimal(line.sides) / 2
        rate = finance.fee_rate_for(schedule, line.root)
        checks.append(
            CommissionCheckOut(
                root=line.root,
                round_trips=round_trips.normalize(),
                file_total=finance.to_money(line.commission),
                file_rate=fee_rate_shown(
                    line.rate if line.rate is not None else (line.commission / round_trips).quantize(RATE_PLACES)
                ),
                schedule_rate=fee_rate_shown(rate),
                schedule_total=finance.to_money(rate * round_trips),
            )
        )
    return checks


@router.post(
    "/imports/preview",
    response_model=ImportPreviewOut,
    dependencies=[limited_per_person(lambda limits: limits.import_preview), ImportSlot],
)
def preview_import(
    account_id: AccountField,
    file: FilesField,
    tenant: WriteTenantDep,
    platform: PlatformField = "tradovate",
    log_account: LogAccountField = None,
) -> ImportPreviewOut:
    """What importing these files into this account would do: counts, problems, and the first rows with a status each.
    Nothing is saved (the import runs inside a transaction that is rolled back), so the preview is exactly what the
    real import would do."""
    files = _read(file)
    platform, recognised, suggested, problem = _resolve(platform, files)
    if problem is not None:
        with refusals(what="account"), tenant.session() as conn:
            pg_accounts.get_account(conn, account_id)  # someone else's account is a 404, whatever the files are
        return _refused(platform, files, problem, recognised=recognised, suggested_platform=suggested)
    with refusals(what="account"), tenant.session(statement_timeout_ms=HEAVY) as conn:
        if platform in pg_importer.ORDER_SOURCES:
            report, rows = pg_importer.preview_orders(
                conn, account_id, files, PREVIEW_ROWS_SHOWN, platform=platform, log_account=log_account
            )
            setting = pg_settings.effective_csv_timezone(conn)
            checks = _commission_checks(report, pg_accounts.fee_schedule(conn, account_id))
        else:
            rows, setting = pg_importer.preview_rows(conn, account_id, files[0][1], PREVIEW_ROWS_SHOWN)
            report = pg_importer.import_files(conn, account_id, files, dry_run=True).files[0]
            checks = []
    shown = _shown_zone(report, setting)
    return ImportPreviewOut(
        platform=platform,  # type: ignore[arg-type]  # one of the Platform words
        recognised=recognised,
        file_name=report.filename,
        file_count=len(files),
        file_bytes=sum(len(data) for _, data in files),
        fatal=report.fatal,
        rows_parsed=report.rows_parsed,
        rows_added=report.rows_added,
        duplicates=report.duplicates,
        duplicates_in_file=report.duplicates_in_file,
        duplicates_other_accounts=report.duplicates_other_accounts,
        matches_other_accounts=report.matches_other_accounts,
        skipped_by_status=report.skipped_by_status,
        one_page=any(_is_one_page(w) for w in report.warnings),
        open_positions=[
            OpenPositionOut(
                symbol=position.symbol,
                direction=position.direction,  # type: ignore[arg-type]  # long or short
                qty=position.qty,
                since=position.since.astimezone(shown).strftime(CSV_TIME),
            )
            for position in report.open_positions
        ],
        file_commission=None if report.file_commission is None else finance.to_money(report.file_commission),
        commissions=checks,
        time_zone=_zone_label(report, setting),
        log_accounts=[
            LogAccountOut(
                name=a.name, round_turns=a.round_turns, practice=a.practice, here=a.here, elsewhere=a.elsewhere
            )
            for a in report.log_accounts
        ],
        log_account=report.log_account,
        file_gross=None if report.file_gross is None else finance.to_money(report.file_gross),
        file_fees=None if report.file_fees is None else finance.to_money(report.file_fees),
        file_net=None if report.file_net is None else finance.to_money(report.file_net),
        first_day=report.first_day,
        last_day=report.last_day,
        errors=[ImportIssueOut(line=e.line, message=e.message) for e in report.errors],
        # The one-page warning is said by `one_page`, which the page shows with a button to add the other pages.
        warnings=[ImportIssueOut(line=w.line, message=w.message) for w in report.warnings if not _is_one_page(w)],
        trades_built=report.trades_built,
        trades_changed=report.trades_extended + report.trades_merged,
        trades_removed=report.trades_removed,
        rows=[
            PreviewRowOut(
                line=r.pair.line or number,  # pairs worked out from orders have no line: numbered in order
                status=r.status,  # type: ignore[arg-type]  # one of the three PAIR_* words
                account=r.account_name,
                symbol=r.pair.symbol,
                bought_at=r.pair.bought_at.astimezone(shown).strftime(CSV_TIME),
                sold_at=r.pair.sold_at.astimezone(shown).strftime(CSV_TIME),
                qty=r.pair.qty,
                buy_price=r.pair.buy_price,
                sell_price=r.pair.sell_price,
                pnl=r.pair.pnl,
            )
            for number, r in enumerate(rows, start=1)
        ],
        rows_shown_limit=PREVIEW_ROWS_SHOWN,
    )


def _is_one_page(issue: RowIssue) -> bool:
    return issue.line == 0 and issue.message == tradesea.ONE_PAGE


@router.post(
    "/imports",
    response_model=ImportRecordOut,
    status_code=201,
    dependencies=[limited_per_person(lambda limits: limits.import_commit), ImportSlot],
)
def import_file(
    account_id: AccountField,
    file: FilesField,
    request: Request,
    tenant: WriteTenantDep,
    services: ServicesDep,
    config: ConfigDep,
    platform: PlatformField = "tradovate",
    log_account: LogAccountField = None,
) -> ImportRecordOut:
    """Import the files into the account. Rows already stored are left alone, so importing a file twice is safe."""
    files = _read(file)
    platform, _, _, problem = _resolve(platform, files)
    with refusals(what="account"), tenant.session(statement_timeout_ms=HEAVY) as conn:
        pg_accounts.get_account(conn, account_id)
        if problem is not None:
            raise HTTPException(422, sentence(problem))
        report = pg_importer.import_files(conn, account_id, files, platform=platform, log_account=log_account).files[0]
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
    with refusals(what="import"), tenant.session(statement_timeout_ms=HEAVY) as conn:
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
