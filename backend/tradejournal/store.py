"""Persisting fill pairs and trades, and regrouping trades when fill pairs change.

Functions here expect the caller to hold a transaction (db.transaction).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from .accounts import fee_schedule
from .finance import money_str, plain
from .grouping import group_fill_pairs, row_order
from .models import FillPair, TradeMetrics
from .settings import trading_session
from .timeutil import from_utc_iso, to_utc_iso
from .trades import build_trade

_PAIR_COLUMNS = (
    "symbol", "tick_size", "price_format", "price_format_type", "buy_fill_id", "sell_fill_id",
    "qty", "buy_price", "sell_price", "pnl", "bought_at", "sold_at", "duration_raw",
    "duration_seconds", "direction",
)  # fmt: skip

_TRADE_COLUMNS = (
    "symbol", "symbol_root", "contract_month", "direction", "entry_at", "exit_at", "trade_date",
    "hold_seconds", "total_qty", "max_position", "fill_pair_count", "tick_size", "avg_entry",
    "avg_exit", "points", "ticks", "gross_pnl", "fees", "net_pnl",
)  # fmt: skip


@dataclass
class RebuildResult:
    created: set[int] = field(default_factory=set)
    updated: set[int] = field(default_factory=set)  # existing trades whose fill pairs changed
    removed: set[int] = field(default_factory=set)  # merged into another trade

    def absorb(self, other: RebuildResult) -> None:
        self.created |= other.created
        self.updated |= other.updated
        self.removed |= other.removed


def insert_fill_pair(conn: sqlite3.Connection, account_id: int, pair: FillPair) -> int:
    values = (
        pair.symbol, plain(pair.tick_size), pair.price_format, pair.price_format_type,
        pair.buy_fill_id, pair.sell_fill_id, pair.qty, plain(pair.buy_price),
        plain(pair.sell_price), money_str(pair.pnl), to_utc_iso(pair.bought_at),
        to_utc_iso(pair.sold_at), pair.duration_raw, pair.duration_seconds, pair.direction,
    )  # fmt: skip
    cursor = conn.execute(
        f"INSERT INTO fill_pairs (account_id, {', '.join(_PAIR_COLUMNS)}) "
        f"VALUES (?, {', '.join('?' * len(_PAIR_COLUMNS))})",
        (account_id, *values),
    )
    return cursor.lastrowid


def pair_from_row(row: sqlite3.Row) -> FillPair:
    return FillPair(
        id=row["id"],
        symbol=row["symbol"],
        tick_size=Decimal(row["tick_size"]),
        price_format=row["price_format"],
        price_format_type=row["price_format_type"],
        buy_fill_id=row["buy_fill_id"],
        sell_fill_id=row["sell_fill_id"],
        qty=row["qty"],
        buy_price=Decimal(row["buy_price"]),
        sell_price=Decimal(row["sell_price"]),
        pnl=Decimal(row["pnl"]),
        bought_at=from_utc_iso(row["bought_at"]),
        sold_at=from_utc_iso(row["sold_at"]),
        duration_raw=row["duration_raw"],
        duration_seconds=row["duration_seconds"],
        direction=row["direction"],
    )


def trade_fill_pairs(conn: sqlite3.Connection, trade_id: int) -> list[FillPair]:
    rows = conn.execute("SELECT * FROM fill_pairs WHERE trade_id = ?", (trade_id,))
    return sorted((pair_from_row(r) for r in rows), key=row_order)


def write_trade(
    conn: sqlite3.Connection, account_id: int, metrics: TradeMetrics, trade_id: int | None = None
) -> int:
    """Insert a trade, or overwrite the derived columns of an existing one.

    Journal columns (notes) are never touched here.
    """
    values = (
        metrics.symbol, metrics.symbol_root, metrics.contract_month, metrics.direction,
        to_utc_iso(metrics.entry_at), to_utc_iso(metrics.exit_at), metrics.trade_date.isoformat(),
        metrics.hold_seconds, metrics.total_qty, metrics.max_position, metrics.fill_pair_count,
        plain(metrics.tick_size), plain(metrics.avg_entry), plain(metrics.avg_exit),
        plain(metrics.points), plain(metrics.ticks), money_str(metrics.gross_pnl),
        money_str(metrics.fees), money_str(metrics.net_pnl),
    )  # fmt: skip
    if trade_id is None:
        cursor = conn.execute(
            f"INSERT INTO trades (account_id, {', '.join(_TRADE_COLUMNS)}) "
            f"VALUES (?, {', '.join('?' * len(_TRADE_COLUMNS))})",
            (account_id, *values),
        )
        return cursor.lastrowid
    assignments = ", ".join(f"{column} = ?" for column in _TRADE_COLUMNS)
    conn.execute(f"UPDATE trades SET {assignments} WHERE id = ?", (*values, trade_id))
    return trade_id


def rebuild_trades(
    conn: sqlite3.Connection, account_id: int, symbol: str, seed_pair_ids: Iterable[int]
) -> RebuildResult:
    """Regroup the given fill pairs together with every stored trade they could touch.

    A fill pair can only join trades whose time span overlaps its own, so the search
    widens from the seed pairs' span until no further trade overlaps. Trades keep
    their ids (and with them their notes, tags and screenshots) wherever possible:
    each new group reuses the earliest old trade it contains, and any other old trade
    merged into it hands over its journal entries before being deleted.
    """
    loaded: dict[int, tuple[FillPair, int | None]] = {}  # pair id -> (pair, old trade id)
    for pair_id in seed_pair_ids:
        row = conn.execute("SELECT * FROM fill_pairs WHERE id = ?", (pair_id,)).fetchone()
        loaded[pair_id] = (pair_from_row(row), row["trade_id"])
    if not loaded:
        return RebuildResult()

    old_trades: set[int] = set()
    while True:
        start = to_utc_iso(min(pair.entry_at for pair, _ in loaded.values()))
        end = to_utc_iso(max(pair.exit_at for pair, _ in loaded.values()))
        overlapping = {
            row["id"]
            for row in conn.execute(
                "SELECT id FROM trades WHERE account_id = ? AND symbol = ? "
                "AND entry_at <= ? AND exit_at >= ?",
                (account_id, symbol, end, start),
            )
        }
        overlapping |= {trade_id for _, trade_id in loaded.values() if trade_id is not None}
        newly_found = overlapping - old_trades
        if not newly_found:
            break
        old_trades |= newly_found
        for trade_id in newly_found:
            for row in conn.execute("SELECT * FROM fill_pairs WHERE trade_id = ?", (trade_id,)):
                loaded[row["id"]] = (pair_from_row(row), trade_id)

    old_members: dict[int, set[int]] = defaultdict(set)
    for pair_id, (_, trade_id) in loaded.items():
        if trade_id is not None:
            old_members[trade_id].add(pair_id)

    pairs = sorted((pair for pair, _ in loaded.values()), key=row_order)
    schedule = fee_schedule(conn, account_id)
    session = trading_session(conn)
    result = RebuildResult()
    claimed: set[int] = set()
    absorbed: list[tuple[int, int]] = []  # (old trade, trade it merged into)

    for group in group_fill_pairs(pairs):
        members = [pairs[i] for i in group]
        member_ids = {pair.id for pair in members}
        previous = list(dict.fromkeys(loaded[p.id][1] for p in members if loaded[p.id][1] is not None))
        keep = next((trade_id for trade_id in previous if trade_id not in claimed), None)

        metrics = build_trade(members, fee_schedule=schedule, session=session)
        trade_id = write_trade(conn, account_id, metrics, keep)
        claimed.add(trade_id)
        if keep is None:
            result.created.add(trade_id)
        elif member_ids != old_members[keep]:
            result.updated.add(trade_id)
        for other in previous:
            if other not in claimed:
                claimed.add(other)
                absorbed.append((other, trade_id))
        conn.executemany(
            "UPDATE fill_pairs SET trade_id = ? WHERE id = ?",
            [(trade_id, pair_id) for pair_id in member_ids],
        )

    for source, target in absorbed:
        merge_journal(conn, source, target)
        conn.execute("DELETE FROM trades WHERE id = ?", (source,))
        result.removed.add(source)
    return result


def rebuild_account(conn: sqlite3.Connection, account_id: int) -> RebuildResult:
    """Regroup every fill pair of an account from scratch (repair tool)."""
    result = RebuildResult()
    by_symbol: dict[str, list[int]] = defaultdict(list)
    for row in conn.execute("SELECT id, symbol FROM fill_pairs WHERE account_id = ?", (account_id,)):
        by_symbol[row["symbol"]].append(row["id"])
    for symbol, pair_ids in by_symbol.items():
        result.absorb(rebuild_trades(conn, account_id, symbol, pair_ids))
    return result


def recalculate_trades(conn: sqlite3.Connection, account_id: int | None = None) -> int:
    """Recompute stored trade metrics (fees, net P&L, trading day) without regrouping.

    Run after changing an account's fee schedule or the trading day settings.
    """
    query = "SELECT id, account_id FROM trades"
    params: tuple = ()
    if account_id is not None:
        query += " WHERE account_id = ?"
        params = (account_id,)
    trades = conn.execute(query, params).fetchall()
    session = trading_session(conn)
    schedules: dict[int, dict[str, Decimal]] = {}
    for trade in trades:
        schedule = schedules.setdefault(trade["account_id"], fee_schedule(conn, trade["account_id"]))
        metrics = build_trade(trade_fill_pairs(conn, trade["id"]), fee_schedule=schedule, session=session)
        write_trade(conn, trade["account_id"], metrics, trade["id"])
    return len(trades)


def has_journal(conn: sqlite3.Connection, trade_id: int) -> bool:
    row = conn.execute(
        """SELECT (SELECT notes != '' FROM trades WHERE id = :id)
                  OR EXISTS (SELECT 1 FROM trade_tags WHERE trade_id = :id)
                  OR EXISTS (SELECT 1 FROM screenshots WHERE trade_id = :id)""",
        {"id": trade_id},
    ).fetchone()
    return bool(row[0])


def merge_journal(conn: sqlite3.Connection, source_id: int, target_id: int) -> None:
    """Move notes, tags and screenshots from one trade onto another."""
    source_notes = conn.execute("SELECT notes FROM trades WHERE id = ?", (source_id,)).fetchone()[0]
    if source_notes.strip():
        target_notes = conn.execute("SELECT notes FROM trades WHERE id = ?", (target_id,)).fetchone()[0]
        # Notes are HTML (rich text), so they're joined with a line break, not newlines.
        merged = f"{target_notes}<br>{source_notes}" if target_notes.strip() else source_notes
        conn.execute("UPDATE trades SET notes = ? WHERE id = ?", (merged, target_id))
    conn.execute(
        "INSERT OR IGNORE INTO trade_tags (trade_id, tag_id) SELECT ?, tag_id FROM trade_tags WHERE trade_id = ?",
        (target_id, source_id),
    )
    conn.execute("UPDATE screenshots SET trade_id = ? WHERE trade_id = ?", (target_id, source_id))
