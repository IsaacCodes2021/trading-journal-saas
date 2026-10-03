"""Persisting fill pairs and trades on Postgres, and regrouping trades when fill pairs change.

The port of ``tradejournal/store.py`` (milestone M2). The result record is shared with the SQLite module; the database
code is rewritten with two changes the SQL audit (doc 03 section 9.1) called for:

* **One lock per user.** Regrouping reads a user's stored trades, decides what to merge, then writes. Two requests doing
  that at once for the same user (two browser tabs importing overlapping files) could each miss the other's rows and
  leave duplicated or split trades. Everything that regroups starts with ``lock_user``, an advisory lock held until the
  transaction ends. Other users are not affected.
* **Batches, not loops.** SQLite made a statement per row for free; over a network a statement costs a round trip. Fill
  pairs and trades are written with one batched statement per kind, and reads use ``= ANY(array)``.

Every function takes a connection from ``Database.tenant_session``, so row-level security already limits it to one
user's rows. A fill pair's ``buy_ref`` and ``sell_ref`` are the export's fill ids as text (doc 03 ADR-009).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from decimal import Decimal

import psycopg

from ..finance import plain, to_money
from ..grouping import group_fill_pairs, row_order
from ..models import FillPair, TradeMetrics
from ..store import RebuildResult
from ..trades import build_trade
from . import settings, usage
from .accounts import fee_schedule

SOURCE = "tradovate_performance"  # the only import adapter so far (matches the column default)
_LOCK_CLASS = 7283  # advisory-lock namespace for per-user work; the migration runner uses a single bigint key


def lock_user(conn: psycopg.Connection) -> None:
    """Take this user's regrouping lock, held until the transaction ends. Safe to call again."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(app_user_id()::text))", (_LOCK_CLASS,))


def insert_fill_pairs(conn: psycopg.Connection, account_id: int, pairs: Sequence[FillPair]) -> list[int]:
    """Store fill pairs in one batch and return their ids in the same order."""
    if not pairs:
        return []
    with conn.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO fill_pairs (account_id, source, buy_ref, sell_ref, symbol, tick_size, price_format,
                   price_format_type, qty, buy_price, sell_price, pnl, bought_at, sold_at, duration_raw,
                   duration_seconds, direction)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            [
                (
                    account_id,
                    SOURCE,
                    str(pair.buy_fill_id),
                    str(pair.sell_fill_id),
                    pair.symbol,
                    _num(pair.tick_size),
                    pair.price_format,
                    pair.price_format_type,
                    pair.qty,
                    _num(pair.buy_price),
                    _num(pair.sell_price),
                    to_money(pair.pnl),
                    pair.bought_at,
                    pair.sold_at,
                    pair.duration_raw,
                    pair.duration_seconds,
                    pair.direction,
                )
                for pair in pairs
            ],
            returning=True,
        )
        return _returned_ids(cursor)


def insert_fill_pair(conn: psycopg.Connection, account_id: int, pair: FillPair) -> int:
    return insert_fill_pairs(conn, account_id, [pair])[0]


def pair_from_row(row: dict) -> FillPair:
    return FillPair(
        id=row["id"],
        symbol=row["symbol"],
        tick_size=row["tick_size"],
        price_format=row["price_format"],
        price_format_type=row["price_format_type"],
        buy_fill_id=int(row["buy_ref"]),
        sell_fill_id=int(row["sell_ref"]),
        qty=row["qty"],
        buy_price=row["buy_price"],
        sell_price=row["sell_price"],
        pnl=row["pnl"],
        bought_at=row["bought_at"],
        sold_at=row["sold_at"],
        duration_raw=row["duration_raw"],
        duration_seconds=row["duration_seconds"],
        direction=row["direction"],
    )


def trade_fill_pairs(conn: psycopg.Connection, trade_id: int) -> list[FillPair]:
    rows = conn.execute("SELECT * FROM fill_pairs WHERE trade_id = %s", (trade_id,))
    return sorted((pair_from_row(r) for r in rows), key=row_order)


def insert_trades(conn: psycopg.Connection, account_id: int, trades: Sequence[TradeMetrics]) -> list[int]:
    """Store new trades in one batch and return their ids in the same order."""
    if not trades:
        return []
    with conn.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO trades (account_id, symbol, symbol_root, contract_month, direction, entry_at, exit_at,
                   trade_date, hold_seconds, total_qty, max_position, fill_pair_count, tick_size, avg_entry, avg_exit,
                   points, ticks, gross_pnl, fees, net_pnl)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            [(account_id, *_trade_values(metrics)) for metrics in trades],
            returning=True,
        )
        return _returned_ids(cursor)


def update_trades(conn: psycopg.Connection, trades: Sequence[tuple[int, TradeMetrics]]) -> None:
    """Overwrite the derived columns of existing trades in one batch. Journal columns (notes) are never touched."""
    if not trades:
        return
    with conn.cursor() as cursor:
        cursor.executemany(
            """UPDATE trades SET symbol = %s, symbol_root = %s, contract_month = %s, direction = %s, entry_at = %s,
                   exit_at = %s, trade_date = %s, hold_seconds = %s, total_qty = %s, max_position = %s,
                   fill_pair_count = %s, tick_size = %s, avg_entry = %s, avg_exit = %s, points = %s, ticks = %s,
                   gross_pnl = %s, fees = %s, net_pnl = %s
               WHERE id = %s""",
            [(*_trade_values(metrics), trade_id) for trade_id, metrics in trades],
        )


def write_trade(conn: psycopg.Connection, account_id: int, metrics: TradeMetrics, trade_id: int | None = None) -> int:
    """Insert a trade, or overwrite the derived columns of an existing one."""
    if trade_id is None:
        return insert_trades(conn, account_id, [metrics])[0]
    update_trades(conn, [(trade_id, metrics)])
    return trade_id


def rebuild_trades(
    conn: psycopg.Connection, account_id: int, symbol: str, seed_pair_ids: Iterable[int]
) -> RebuildResult:
    """Regroup the given fill pairs together with every stored trade they could touch.

    A fill pair can only join trades whose time span overlaps its own, so the search
    widens from the seed pairs' span until no further trade overlaps. Trades keep
    their ids (and with them their notes, tags and screenshots) wherever possible:
    each new group reuses the earliest old trade it contains, and any other old trade
    merged into it hands over its journal entries before being deleted.
    """
    lock_user(conn)
    seed_ids = sorted(set(seed_pair_ids))
    if not seed_ids:
        return RebuildResult()
    loaded: dict[int, tuple[FillPair, int | None]] = {}  # pair id -> (pair, old trade id)
    for row in conn.execute("SELECT * FROM fill_pairs WHERE id = ANY(%s::bigint[])", (seed_ids,)):
        loaded[row["id"]] = (pair_from_row(row), row["trade_id"])
    if missing := set(seed_ids) - loaded.keys():
        raise LookupError(f"no fill pairs with ids {sorted(missing)}")

    old_trades: set[int] = set()
    while True:
        start = min(pair.entry_at for pair, _ in loaded.values())
        end = max(pair.exit_at for pair, _ in loaded.values())
        overlapping = {
            row["id"]
            for row in conn.execute(
                "SELECT id FROM trades WHERE account_id = %s AND symbol = %s AND entry_at <= %s AND exit_at >= %s",
                (account_id, symbol, end, start),
            )
        }
        overlapping |= {trade_id for _, trade_id in loaded.values() if trade_id is not None}
        newly_found = overlapping - old_trades
        if not newly_found:
            break
        old_trades |= newly_found
        for row in conn.execute("SELECT * FROM fill_pairs WHERE trade_id = ANY(%s::bigint[])", (sorted(newly_found),)):
            loaded[row["id"]] = (pair_from_row(row), row["trade_id"])

    old_members: dict[int, set[int]] = defaultdict(set)
    for pair_id, (_, trade_id) in loaded.items():
        if trade_id is not None:
            old_members[trade_id].add(pair_id)

    pairs = sorted((pair for pair, _ in loaded.values()), key=row_order)
    schedule = fee_schedule(conn, account_id)
    session = settings.trading_session(conn)
    claimed: set[int] = set()
    absorbed: list[tuple[int, int]] = []  # (old trade, trade it merged into)
    planned: list[tuple[set[int], int | None, TradeMetrics]] = []  # (member pair ids, trade to reuse, metrics)

    for group in group_fill_pairs(pairs):
        members = [pairs[i] for i in group]
        previous = list(dict.fromkeys(loaded[p.id][1] for p in members if loaded[p.id][1] is not None))
        keep = next((trade_id for trade_id in previous if trade_id not in claimed), None)
        if keep is not None:
            claimed.add(keep)
            # Every other old trade not yet claimed merges into `keep`. When there is no `keep`, all of them were
            # claimed by earlier groups already.
            for other in previous:
                if other not in claimed:
                    claimed.add(other)
                    absorbed.append((other, keep))
        planned.append(({p.id for p in members}, keep, build_trade(members, fee_schedule=schedule, session=session)))

    result = RebuildResult()
    new_ids = iter(insert_trades(conn, account_id, [metrics for _, keep, metrics in planned if keep is None]))
    update_trades(conn, [(keep, metrics) for _, keep, metrics in planned if keep is not None])
    assignments: list[tuple[int, int]] = []  # (pair id, trade id)
    for member_ids, keep, _ in planned:
        trade_id = next(new_ids) if keep is None else keep
        if keep is None:
            result.created.add(trade_id)
        elif member_ids != old_members[keep]:
            result.updated.add(trade_id)
        assignments.extend((pair_id, trade_id) for pair_id in member_ids)
    conn.execute(
        """UPDATE fill_pairs SET trade_id = v.trade_id
           FROM unnest(%s::bigint[], %s::bigint[]) AS v(pair_id, trade_id)
           WHERE fill_pairs.id = v.pair_id""",
        ([pair_id for pair_id, _ in assignments], [trade_id for _, trade_id in assignments]),
    )

    for source, target in absorbed:
        merge_journal(conn, source, target)
        conn.execute("DELETE FROM trades WHERE id = %s", (source,))
        result.removed.add(source)
    return result


def rebuild_account(conn: psycopg.Connection, account_id: int) -> RebuildResult:
    """Regroup every fill pair of an account from scratch (repair tool).

    About 1.5 ms a fill pair on SQLite (16 s for 11,000), so hosted it must run as a background job, not in a request.
    """
    lock_user(conn)
    result = RebuildResult()
    by_symbol: dict[str, list[int]] = defaultdict(list)
    for row in conn.execute("SELECT id, symbol FROM fill_pairs WHERE account_id = %s ORDER BY id", (account_id,)):
        by_symbol[row["symbol"]].append(row["id"])
    for symbol, pair_ids in by_symbol.items():
        result.absorb(rebuild_trades(conn, account_id, symbol, pair_ids))
    usage.refresh(conn)  # regrouping can merge trades away
    return result


def recalculate_trades(conn: psycopg.Connection, account_id: int | None = None) -> int:
    """Recompute stored trade metrics (fees, net P&L, trading day) without regrouping.

    Run after changing an account's fee schedule or the trading day settings.
    """
    lock_user(conn)
    if account_id is None:
        trades = conn.execute("SELECT id, account_id FROM trades ORDER BY id").fetchall()
    else:
        trades = conn.execute("SELECT id, account_id FROM trades WHERE account_id = %s ORDER BY id", (account_id,))
        trades = trades.fetchall()
    if not trades:
        return 0
    session = settings.trading_session(conn)
    pairs_by_trade: dict[int, list[FillPair]] = defaultdict(list)
    for row in conn.execute(
        "SELECT * FROM fill_pairs WHERE trade_id = ANY(%s::bigint[])", ([trade["id"] for trade in trades],)
    ):
        pairs_by_trade[row["trade_id"]].append(pair_from_row(row))
    schedules: dict[int, dict[str, Decimal]] = {}
    updates = []
    for trade in trades:
        schedule = schedules.setdefault(trade["account_id"], fee_schedule(conn, trade["account_id"]))
        pairs = sorted(pairs_by_trade[trade["id"]], key=row_order)
        updates.append((trade["id"], build_trade(pairs, fee_schedule=schedule, session=session)))
    update_trades(conn, updates)
    return len(trades)


def journal_trade_ids(conn: psycopg.Connection, trade_ids: Sequence[int]) -> set[int]:
    """The given trades that have a note, a tag or a screenshot."""
    rows = conn.execute(
        """SELECT id FROM trades
           WHERE id = ANY(%s::bigint[])
             AND (notes <> ''
                  OR EXISTS (SELECT 1 FROM trade_tags WHERE trade_tags.trade_id = trades.id)
                  OR EXISTS (SELECT 1 FROM screenshots WHERE screenshots.trade_id = trades.id))""",
        (list(trade_ids),),
    )
    return {row["id"] for row in rows}


def has_journal(conn: psycopg.Connection, trade_id: int) -> bool:
    return trade_id in journal_trade_ids(conn, [trade_id])


def merge_journal(conn: psycopg.Connection, source_id: int, target_id: int) -> None:
    """Move notes, tags and screenshots from one trade onto another."""
    source_notes = conn.execute("SELECT notes FROM trades WHERE id = %s", (source_id,)).fetchone()["notes"]
    if source_notes.strip():
        target_notes = conn.execute("SELECT notes FROM trades WHERE id = %s", (target_id,)).fetchone()["notes"]
        # Notes are HTML (rich text), so they're joined with a line break, not newlines.
        merged = f"{target_notes}<br>{source_notes}" if target_notes.strip() else source_notes
        conn.execute("UPDATE trades SET notes = %s WHERE id = %s", (merged, target_id))
    conn.execute(
        "INSERT INTO trade_tags (trade_id, tag_id) SELECT %s, tag_id FROM trade_tags WHERE trade_id = %s "
        "ON CONFLICT DO NOTHING",
        (target_id, source_id),
    )
    conn.execute("UPDATE screenshots SET trade_id = %s WHERE trade_id = %s", (target_id, source_id))


def _returned_ids(cursor: psycopg.Cursor) -> list[int]:
    """The ids from an ``executemany(..., returning=True)``: one result set per statement."""
    ids = []
    while True:
        ids.append(cursor.fetchone()["id"])
        if not cursor.nextset():
            return ids


def _num(value: Decimal) -> Decimal:
    """A price or quantity as stored: no trailing zeros, so 16450.250 and 16450.25 read back identically."""
    return Decimal(plain(value))


def _trade_values(metrics: TradeMetrics) -> tuple:
    return (
        metrics.symbol,
        metrics.symbol_root,
        metrics.contract_month,
        metrics.direction,
        metrics.entry_at,
        metrics.exit_at,
        metrics.trade_date,
        metrics.hold_seconds,
        metrics.total_qty,
        metrics.max_position,
        metrics.fill_pair_count,
        _num(metrics.tick_size),
        _num(metrics.avg_entry),
        _num(metrics.avg_exit),
        _num(metrics.points),
        _num(metrics.ticks),
        to_money(metrics.gross_pnl),
        to_money(metrics.fees),
        to_money(metrics.net_pnl),
    )
