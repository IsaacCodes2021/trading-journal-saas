"""Read-side queries used by the user interface, on Postgres.

The port of ``tradejournal/queries.py`` (milestone M2). The filter and record types are shared with the SQLite
module. Rows already come back typed (``Decimal``, ``date``, aware ``datetime``), so nothing is parsed from text.
Every function takes a connection from ``Database.tenant_session``: row-level security limits it to one user's rows.
"""

from __future__ import annotations

import psycopg

from ..queries import TradeFilter, TradeRecord

_SELECT = """
    SELECT trades.*, accounts.name AS account_name, accounts.account_type AS account_type,
           accounts.firm AS firm
    FROM trades JOIN accounts ON accounts.id = trades.account_id
"""

_DEFAULT_FILTER = TradeFilter()


def load_trades(conn: psycopg.Connection, trade_filter: TradeFilter = _DEFAULT_FILTER) -> list[TradeRecord]:
    """Trades matching the filter, in the order they were closed."""
    clauses: list[str] = []
    params: list = []
    if trade_filter.account_ids:
        clauses.append("trades.account_id = ANY(%s::bigint[])")
        params.append(list(trade_filter.account_ids))
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
    rows = conn.execute(_SELECT + where + " ORDER BY trades.exit_at, trades.entry_at, trades.id", params)
    return [_record(row) for row in rows]


def load_trade(conn: psycopg.Connection, trade_id: int) -> TradeRecord | None:
    row = conn.execute(_SELECT + " WHERE trades.id = %s", (trade_id,)).fetchone()
    return _record(row) if row else None


def symbol_roots(conn: psycopg.Connection) -> list[str]:
    # COLLATE "C": byte order, as SQLite sorted, instead of whatever locale the server was created with.
    rows = conn.execute(
        'SELECT symbol_root FROM (SELECT DISTINCT symbol_root FROM trades) AS roots ORDER BY symbol_root COLLATE "C"'
    )
    return [row["symbol_root"] for row in rows]


def _record(row: dict) -> TradeRecord:
    return TradeRecord(
        id=row["id"],
        account_id=row["account_id"],
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
    )
