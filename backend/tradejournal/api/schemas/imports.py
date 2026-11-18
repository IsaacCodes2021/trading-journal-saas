"""Importing exports (Tradovate's Performance report, TradeSea's Orders pages): what the files would do, what an import
did, and what undoing one would remove."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .base import ApiModel
from .journal import AccountRefOut, RowId
from .money import Money

PairStatus = Literal["new", "duplicate", "other_account"]
Platform = Literal["tradovate", "tradesea"]


class ImportIssueOut(ApiModel):
    """A line of the file that could not be used, numbered the way a spreadsheet numbers it."""

    line: int
    message: str


class PreviewRowOut(ApiModel):
    """One fill pair as the file states it: times are written the way the file writes them, in the CSV timezone."""

    line: int
    status: PairStatus
    account: str | None  # for `other_account`: the account the pair is already stored under
    symbol: str
    bought_at: str
    sold_at: str
    qty: int
    buy_price: Money
    sell_price: Money
    pnl: Money


class OpenPositionOut(ApiModel):
    """Contracts still held after the last order of a contract: held overnight, or an order the export left out."""

    symbol: str
    direction: Literal["long", "short"]
    qty: int
    since: str  # when the oldest was opened, written like the rows' times


class CommissionCheckOut(ApiModel):
    """One contract's commission in the files, beside what the account's own fee setting charges for the same
    contracts. Roundturn uses the setting, so the two should match."""

    root: str  # "NQ"
    round_trips: Money  # contracts bought plus contracts sold, halved (a position still open leaves a half)
    file_total: Money
    file_rate: Money  # the files' commission per contract, round trip
    schedule_rate: Money  # the account's setting for this contract (or its rate for any other contract)
    schedule_total: Money  # what the setting charges for the same round trips


class ImportPreviewOut(ApiModel):
    """What importing the files would do, worked out without saving anything. Mirrors `FileImportReport`.

    For Tradovate, a row is a fill pair (a buy matched to a sell). For TradeSea, a row is a filled order; the table
    still shows the fill pairs the orders make."""

    platform: Platform
    recognised: bool = False  # the platform was told from the files' header row (`platform=auto`)
    suggested_platform: Platform | None = None  # refused because the files look like this other platform's
    file_name: str  # the files' names, joined
    file_count: int = 1
    file_bytes: int
    fatal: str | None  # the whole import was refused (wrong kind of export, empty): a reason in words
    rows_parsed: int
    rows_added: int
    duplicates: int  # already stored in this account
    duplicates_in_file: int  # repeated within the file (TradeSea: on more than one page)
    duplicates_other_accounts: dict[str, int]  # stored under another account, by account name (left alone)
    matches_other_accounts: dict[str, int] = {}  # TradeSea: the same orders in another account (copy trading), stored
    skipped_by_status: dict[str, int] = {}  # TradeSea: orders that did not fill, by status ("cancelled": 4)
    one_page: bool = False  # TradeSea: every file is exactly one page of 10 orders, so a page may be missing
    open_positions: list[OpenPositionOut] = []  # TradeSea: contracts still held after the last order
    file_commission: Money | None = None  # TradeSea: the files' own commission total
    commissions: list[CommissionCheckOut] = []  # TradeSea: the same, by contract, beside the account's fee setting
    time_zone: str = ""  # the zone the times were read in: the file's own ("PST") or the CSV time zone setting
    errors: list[ImportIssueOut]
    warnings: list[ImportIssueOut]
    trades_built: int  # trades the new rows would create or extend
    trades_changed: int = 0  # trades already stored that would change (extended, or merged into another)
    trades_removed: int = 0  # TradeSea: trades already stored that would be left with no fill pair, and deleted
    rows: list[PreviewRowOut]  # the first rows, for the table
    rows_shown_limit: int


class ImportRecordOut(ApiModel):
    id: RowId
    platform: Platform
    imported_at: datetime
    file_name: str
    account: AccountRefOut
    rows_added: int
    trades_built: int
    undone_at: datetime | None


class UndoPreviewOut(ApiModel):
    """What undoing an import removes (or, after the undo, removed). Mirrors `UndoReport`."""

    import_id: RowId
    rows_removed: int
    rows_kept: int  # still contained in another import
    trades_removed: int
    trades_with_journal_removed: int  # removed trades that had notes, tags or screenshots
    trades_changed: int
