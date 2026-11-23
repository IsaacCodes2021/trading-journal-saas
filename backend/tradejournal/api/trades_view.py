"""From stored trades to the trade log's rows, search and order. Pure: no database, no clock.

The log is built in memory from the filtered trades (a few thousand rows at most per user, measured in doc 05 section
8): filtering by search, ordering and paging are cheap next to loading, and the page total and net result follow every
filter without a second query.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from .. import finance
from ..accounts import Account
from ..journal import Tag
from ..pg.copy_groups import CopyGroup
from ..queries import TradeRecord
from .cents import cents
from .schemas.copy_groups import CopyGroupRefOut
from .schemas.journal import AccountRefOut
from .schemas.trades import NeighborsOut, TagOut, TradeOut, TradeSort, TradesPageOut


def tag_out(tag: Tag) -> TagOut:
    return TagOut(id=tag.id, group_id=tag.group_id, name=tag.name, color=tag.color)


def one_per_trade(
    records: Iterable[TradeRecord], groups: Sequence[CopyGroup], accounts: Mapping[int, Account]
) -> tuple[list[TradeRecord], dict[int, CopyGroupRefOut]]:
    """The trade log shows a copied trade once, on its leader (decision 13), whatever account or group the filter named:
    one record per trade id, in the order given, a follower's copy turned back into the leader's trade. Also, for each
    trade that was copied to at least one follower on its trading day, its group (the row's label)."""
    kept: dict[int, TradeRecord] = {}
    for record in records:
        if record.id not in kept or (record.copy_group_id is None and kept[record.id].copy_group_id is not None):
            kept[record.id] = record
    out: list[TradeRecord] = []
    for record in kept.values():
        if record.copy_group_id is not None and record.leader_account_id in accounts:
            leader = accounts[record.leader_account_id]
            record = replace(
                record,
                account_id=leader.id,
                account_name=leader.name,
                account_type=leader.account_type,
                firm=leader.firm,
                copy_group_id=None,
                leader_account_id=None,
            )
        out.append(record)
    led = {group.leader_account_id: group for group in groups}
    labels: dict[int, CopyGroupRefOut] = {}
    for record in out:
        group = led.get(record.account_id)
        if group is not None and copied_on(group, record):
            labels[record.id] = CopyGroupRefOut(id=group.id, name=group.name)
    return out, labels


def copied_on(group: CopyGroup, record: TradeRecord) -> bool:
    """Whether any follower was in the group on the trade's trading day."""
    return any(w.covers(record.trade_date) for a in group.follower_ids() for w in group.windows(a))


def trade_out(
    record: TradeRecord,
    tags: Sequence[Tag],
    *,
    has_notes: bool,
    screenshot_count: int,
    copy_group: CopyGroupRefOut | None = None,
) -> TradeOut:
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
        copy_group=copy_group,
    )


def rows_for(
    records: Iterable[TradeRecord],
    tags: Mapping[int, Sequence[Tag]],
    journal: Mapping[int, tuple[bool, int]],
    labels: Mapping[int, CopyGroupRefOut] | None = None,
) -> list[TradeOut]:
    """`journal` maps a trade id to (has notes, number of screenshots) for the trades that have either; `labels` a
    copied trade's id to its group."""
    labels = labels or {}
    return [
        trade_out(
            record,
            tags.get(record.id, ()),
            has_notes=journal.get(record.id, (False, 0))[0],
            screenshot_count=journal.get(record.id, (False, 0))[1],
            copy_group=labels.get(record.id),
        )
        for record in records
    ]


def matches(row: TradeOut, q: str) -> bool:
    """A case-insensitive substring of the contract, its root, the account's name, its copy group's or any tag's name."""
    needle = q.strip().casefold()
    if not needle:
        return True
    group = (row.copy_group.name,) if row.copy_group else ()
    texts = (row.symbol, row.symbol_root, row.account.name, *group, *(tag.name for tag in row.tags))
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
