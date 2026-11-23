"""Read-side queries used by the user interface, on Postgres.

The port of ``tradejournal/queries.py`` (milestone M2). The filter and record types are shared with the SQLite
module. Rows already come back typed (``Decimal``, ``date``, aware ``datetime``), so nothing is parsed from text.
Every function takes a connection from ``Database.tenant_session``: row-level security limits it to one user's rows.
"""

from __future__ import annotations

from datetime import date

import psycopg

from ..queries import TradeFilter, TradeRecord

_SELECT = """
    SELECT trades.*, accounts.name AS account_name, accounts.account_type AS account_type,
           accounts.firm AS firm
    FROM trades JOIN accounts ON accounts.id = trades.account_id
"""

_DEFAULT_FILTER = TradeFilter()

# Every trade as it is stored, then every follower's copy of a leader trade (copy-trading groups, migration 0026). A
# copy is not stored: it is the leader's trade placed on the follower for each window the follower was in the group.
# A window starts on an `added` event's day (none: from the start) and ends on the next event's day (a `removed`), so a
# follower counts a trade whose trading day is on or after the day it was added and before the day it was removed.
# Copies are not chained: only stored trades are copied (decision 26).
_PLACED = """
    WITH windows AS (
        SELECT group_id, account_id, event_type, effective_date AS starts,
               lead(effective_date) OVER (
                   PARTITION BY group_id, account_id ORDER BY effective_date NULLS FIRST, id
               ) AS ends
        FROM copy_group_membership_events
    ),
    placed AS (
        SELECT trades.id AS trade_id, trades.account_id AS shown_account_id,
               NULL::bigint AS copy_group_id, NULL::bigint AS leader_account_id
        FROM trades
        UNION ALL
        SELECT trades.id, windows.account_id, copy_groups.id, copy_groups.leader_account_id
        FROM trades
        JOIN copy_groups ON copy_groups.leader_account_id = trades.account_id
        JOIN windows ON windows.group_id = copy_groups.id AND windows.event_type = 'added'
             AND (windows.starts IS NULL OR trades.trade_date >= windows.starts)
             AND (windows.ends IS NULL OR trades.trade_date < windows.ends)
    )
    SELECT trades.*, placed.shown_account_id, placed.copy_group_id, placed.leader_account_id,
           accounts.name AS account_name, accounts.account_type AS account_type, accounts.firm AS firm
    FROM placed
    JOIN trades ON trades.id = placed.trade_id
    JOIN accounts ON accounts.id = placed.shown_account_id
"""


def load_trades(conn: psycopg.Connection, trade_filter: TradeFilter = _DEFAULT_FILTER) -> list[TradeRecord]:
    """Trades matching the filter, in the order they were closed, each follower's copies included as trades of the
    follower (decision 12 of `docs/new-feature-copy-trading/decisions.csv`). A copy has the leader trade's id, so a
    caller that shows each trade once keeps one record per id.

    The account filters (accounts, type, firm, archived) are about the account a record is shown on: a follower's own
    fields decide whether its copies count. A group stands for its leader's stored trades and the copies it gave."""
    clauses: list[str] = []
    params: list = []
    if trade_filter.account_ids or trade_filter.copy_group_ids:
        clauses.append(
            """(placed.shown_account_id = ANY(%s::bigint[])
                OR placed.copy_group_id = ANY(%s::bigint[])
                OR (placed.copy_group_id IS NULL AND trades.account_id IN (
                    SELECT leader_account_id FROM copy_groups WHERE id = ANY(%s::bigint[]))))"""
        )
        params.extend(
            [list(trade_filter.account_ids), list(trade_filter.copy_group_ids), list(trade_filter.copy_group_ids)]
        )
    if trade_filter.account_type:
        clauses.append("accounts.account_type = %s")
        params.append(trade_filter.account_type)
    if not trade_filter.include_archived:
        clauses.append("accounts.outcome IS NULL")
    if trade_filter.firms:
        clauses.append("accounts.firm = ANY(%s::text[])")
        params.append(list(trade_filter.firms))
    if trade_filter.start:
        clauses.append("trades.trade_date >= %s")
        params.append(trade_filter.start)
    if trade_filter.end:
        clauses.append("trades.trade_date <= %s")
        params.append(trade_filter.end)
    if trade_filter.symbol_roots:
        clauses.append("trades.symbol_root = ANY(%s::text[])")
        params.append(list(trade_filter.symbol_roots))
    if trade_filter.direction:
        clauses.append("trades.direction = %s")
        params.append(trade_filter.direction)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    order = " ORDER BY trades.exit_at, trades.entry_at, trades.id, placed.copy_group_id NULLS FIRST, accounts.name, accounts.id"
    rows = conn.execute(_PLACED + where + order, params)
    return [_record(row) for row in rows]


def load_trade(conn: psycopg.Connection, trade_id: int) -> TradeRecord | None:
    """One stored trade, on the account that holds it (a leader's, for a copied trade)."""
    row = conn.execute(_SELECT + " WHERE trades.id = %s", (trade_id,)).fetchone()
    return _record(row) if row else None


def trade_number(conn: psycopg.Connection, trade_id: int) -> int:
    """The trade's place among the person's own trades in the order they were added: their first trade is 1. Trade ids
    are one counter shared by everyone, so the count, not the id, is the number on a shared card. Row-level security is
    what keeps other people's trades out of the count. It is counted live, so deleting an earlier trade (undoing an
    import, or two trades merging) moves the later numbers down."""
    row = conn.execute("SELECT count(*) AS number FROM trades WHERE id <= %s", (trade_id,)).fetchone()
    return int(row["number"])


def trading_day_number(conn: psycopg.Connection, day: date) -> int:
    """How many days the person traded on, up to and including `day`: the number on a day's shared card (their first
    trading day is 1). Every trade counts, whatever the page's filters; row-level security keeps other people's out."""
    row = conn.execute(
        "SELECT count(DISTINCT trade_date) AS number FROM trades WHERE trade_date <= %s", (day,)
    ).fetchone()
    return int(row["number"])


def trading_week_number(conn: psycopg.Connection, saturday: date) -> int:
    """How many weeks (Sunday to Saturday) the person traded in, up to and including the one that ends on `saturday`:
    the number on a week's shared card. A trading day's week starts on the Sunday before it (`dow` is 0 on a Sunday)."""
    row = conn.execute(
        """SELECT count(DISTINCT trade_date - extract(dow FROM trade_date)::int) AS number
           FROM trades WHERE trade_date <= %s""",
        (saturday,),
    ).fetchone()
    return int(row["number"])


def symbol_roots(conn: psycopg.Connection) -> list[str]:
    # COLLATE "C": byte order, as SQLite sorted, instead of whatever locale the server was created with.
    rows = conn.execute(
        'SELECT symbol_root FROM (SELECT DISTINCT symbol_root FROM trades) AS roots ORDER BY symbol_root COLLATE "C"'
    )
    return [row["symbol_root"] for row in rows]


def _record(row: dict) -> TradeRecord:
    return TradeRecord(
        id=row["id"],
        account_id=row.get("shown_account_id", row["account_id"]),
        account_name=row["account_name"],
        account_type=row["account_type"],
        firm=row["firm"],
        symbol=row["symbol"],
        symbol_root=row["symbol_root"],
        contract_month=row["contract_month"],
        direction=row["direction"],
        entry_at=row["entry_at"],
        exit_at=row["exit_at"],
        trade_date=row["trade_date"],
        hold_seconds=row["hold_seconds"],
        total_qty=row["total_qty"],
        max_position=row["max_position"],
        fill_pair_count=row["fill_pair_count"],
        tick_size=row["tick_size"],
        avg_entry=row["avg_entry"],
        avg_exit=row["avg_exit"],
        points=row["points"],
        ticks=row["ticks"],
        gross_pnl=row["gross_pnl"],
        fees=row["fees"],
        net_pnl=row["net_pnl"],
        notes=row["notes"],
        copy_group_id=row.get("copy_group_id"),
        leader_account_id=row.get("leader_account_id"),
    )
