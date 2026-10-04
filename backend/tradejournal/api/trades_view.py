"""From stored trades to the trade log's rows, search and order. Pure: no database, no clock.

The log is built in memory from the filtered trades (a few thousand rows at most per user, measured in doc 05 section
8): filtering by search, ordering and paging are cheap next to loading, and the page total and net result follow every
filter without a second query.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from .. import finance
from ..journal import Tag
from ..queries import TradeRecord
from .cents import cents
from .schemas.journal import AccountRefOut
from .schemas.trades import NeighborsOut, TagOut, TradeOut, TradeSort, TradesPageOut


def tag_out(tag: Tag) -> TagOut:
    return TagOut(id=tag.id, group_id=tag.group_id, name=tag.name, color=tag.color)


def trade_out(record: TradeRecord, tags: Sequence[Tag], *, has_notes: bool, screenshot_count: int) -> TradeOut:
    return TradeOut(
        id=record.id,
        account=AccountRefOut(
            id=record.account_id, name=record.account_name, firm=record.firm, account_type=record.account_type
        ),
        symbol=record.symbol,
        symbol_root=record.symbol_root,
        direction=record.direction,
        entry_at=record.entry_at,
        exit_at=record.exit_at,
        trade_day=record.trade_date,
        hold_seconds=record.hold_seconds,
        total_qty=record.total_qty,
        avg_entry=record.avg_entry,
        avg_exit=record.avg_exit,
        gross_pnl=cents(record.gross_pnl),
        fees=cents(record.fees),
        net_pnl=cents(record.net_pnl),
        tags=[tag_out(tag) for tag in tags],
        has_notes=has_notes,
        screenshot_count=screenshot_count,
    )


def rows_for(
    records: Iterable[TradeRecord],
    tags: Mapping[int, Sequence[Tag]],
    journal: Mapping[int, tuple[bool, int]],
) -> list[TradeOut]:
    """`journal` maps a trade id to (has notes, number of screenshots) for the trades that have either."""
    return [
        trade_out(
            record,
            tags.get(record.id, ()),
            has_notes=journal.get(record.id, (False, 0))[0],
            screenshot_count=journal.get(record.id, (False, 0))[1],
        )
        for record in records
    ]


def matches(row: TradeOut, q: str) -> bool:
    """A case-insensitive substring of the contract, its root, the account's name or any tag's name."""
    needle = q.strip().casefold()
    if not needle:
        return True
    texts = (row.symbol, row.symbol_root, row.account.name, *(tag.name for tag in row.tags))
    return any(needle in text.casefold() for text in texts)


def _key(field: str, row: TradeOut):
    match field:
        case "account":
            return row.account.name.casefold()
        case "symbol":
            return row.symbol
        case "direction":
            return row.direction
        case "qty":
            return row.total_qty
        case "hold":
            return row.hold_seconds
        case "net_pnl":
            return row.net_pnl
        case _:  # "date": when the trade was entered
            return row.entry_at


def sort_rows(rows: Sequence[TradeOut], sort: TradeSort) -> list[TradeOut]:
    """Equal values keep a fixed order (by id), so paging never repeats or skips a row."""
    descending = sort.startswith("-")
    field = sort.removeprefix("-")
    return sorted(rows, key=lambda row: (_key(field, row), row.id), reverse=descending)


def page_of(rows: Sequence[TradeOut], *, limit: int, offset: int) -> TradesPageOut:
    """One page of an already filtered and ordered list, with the total and net result of the whole list."""
    return TradesPageOut(
        items=list(rows[offset : offset + limit]),
        total=len(rows),
        net_pnl=cents(sum((row.net_pnl for row in rows), finance.ZERO)),
    )


def neighbors_in(rows: Sequence[TradeOut], trade_id: int) -> NeighborsOut:
    ids = [row.id for row in rows]
    if trade_id not in ids:
        return NeighborsOut(previous=None, next=None)
    at = ids.index(trade_id)
    return NeighborsOut(previous=ids[at - 1] if at > 0 else None, next=ids[at + 1] if at + 1 < len(ids) else None)
