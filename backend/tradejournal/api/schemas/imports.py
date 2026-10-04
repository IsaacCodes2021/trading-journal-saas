"""Importing Tradovate exports: what a file would do, what an import did, and what undoing one would remove."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .base import ApiModel
from .journal import AccountRefOut, RowId
from .money import Money

PairStatus = Literal["new", "duplicate", "other_account"]


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


class ImportPreviewOut(ApiModel):
    """What importing a file would do, worked out without saving anything. Mirrors `FileImportReport`."""

    file_name: str
    file_bytes: int
    fatal: str | None  # the whole file was refused (wrong kind of export, empty): a reason in words
    rows_parsed: int
    rows_added: int
    duplicates: int  # already stored in this account
    duplicates_in_file: int  # repeated within the file
    duplicates_other_accounts: dict[str, int]  # stored under another account, by account name
    errors: list[ImportIssueOut]
    warnings: list[ImportIssueOut]
    trades_built: int  # trades the new pairs would create or extend
    rows: list[PreviewRowOut]  # the first rows, for the table
    rows_shown_limit: int


class ImportRecordOut(ApiModel):
    id: RowId
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
