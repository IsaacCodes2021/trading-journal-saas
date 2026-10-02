"""Read-side queries used by the user interface."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .models import Direction
from .timeutil import from_utc_iso


@dataclass(frozen=True)
class TradeFilter:
    """Empty tuples and None mean "no restriction"."""

    account_ids: tuple[int, ...] = ()
    account_type: str | None = None  # "evaluation" or "funded"
    include_archived: bool = True  # False hides archived accounts' trades
    firms: tuple[str, ...] = ()
    start: date | None = None  # trading day, inclusive
    end: date | None = None  # trading day, inclusive
    symbol_roots: tuple[str, ...] = ()
    direction: Direction | None = None


@dataclass(frozen=True)
class TradeRecord:
    id: int
    account_id: int
    account_name: str
    account_type: str
    firm: str
    symbol: str
    symbol_root: str
    contract_month: str | None
    direction: Direction
    entry_at: datetime  # UTC
    exit_at: datetime  # UTC
    trade_date: date
    hold_seconds: int
    total_qty: int
    max_position: int
    fill_pair_count: int
    tick_size: Decimal
    avg_entry: Decimal
    avg_exit: Decimal
    points: Decimal
    ticks: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    notes: str


_SELECT = """
    SELECT trades.*, accounts.name AS account_name, accounts.account_type AS account_type,
           accounts.firm AS firm
    FROM trades JOIN accounts ON accounts.id = trades.account_id
"""


def load_trades(conn: sqlite3.Connection, trade_filter: TradeFilter = TradeFilter()) -> list[TradeRecord]:
    """Trades matching the filter, in the order they were closed."""
    clauses: list[str] = []
    params: list = []
    if trade_filter.account_ids:
        clauses.append(f"trades.account_id IN ({', '.join('?' * len(trade_filter.account_ids))})")
        params.extend(trade_filter.account_ids)
    if trade_filter.account_type:
        clauses.append("accounts.account_type = ?")
        params.append(trade_filter.account_type)
    if not trade_filter.include_archived:
        clauses.append("accounts.outcome IS NULL")
    if trade_filter.firms:
        clauses.append(f"accounts.firm IN ({', '.join('?' * len(trade_filter.firms))})")
        params.extend(trade_filter.firms)
    if trade_filter.start:
        clauses.append("trades.trade_date >= ?")
        params.append(trade_filter.start.isoformat())
    if trade_filter.end:
        clauses.append("trades.trade_date <= ?")
        params.append(trade_filter.end.isoformat())
    if trade_filter.symbol_roots:
        clauses.append(f"trades.symbol_root IN ({', '.join('?' * len(trade_filter.symbol_roots))})")
        params.extend(trade_filter.symbol_roots)
    if trade_filter.direction:
        clauses.append("trades.direction = ?")
        params.append(trade_filter.direction)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(_SELECT + where + " ORDER BY trades.exit_at, trades.entry_at, trades.id", params)
    return [_record(row) for row in rows]


def load_trade(conn: sqlite3.Connection, trade_id: int) -> TradeRecord | None:
    row = conn.execute(_SELECT + " WHERE trades.id = ?", (trade_id,)).fetchone()
    return _record(row) if row else None


def symbol_roots(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT DISTINCT symbol_root FROM trades ORDER BY symbol_root")]


def _record(row: sqlite3.Row) -> TradeRecord:
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
        entry_at=from_utc_iso(row["entry_at"]),
        exit_at=from_utc_iso(row["exit_at"]),
        trade_date=date.fromisoformat(row["trade_date"]),
        hold_seconds=row["hold_seconds"],
        total_qty=row["total_qty"],
        max_position=row["max_position"],
        fill_pair_count=row["fill_pair_count"],
        tick_size=Decimal(row["tick_size"]),
        avg_entry=Decimal(row["avg_entry"]),
        avg_exit=Decimal(row["avg_exit"]),
        points=Decimal(row["points"]),
        ticks=Decimal(row["ticks"]),
        gross_pnl=Decimal(row["gross_pnl"]),
        fees=Decimal(row["fees"]),
        net_pnl=Decimal(row["net_pnl"]),
        notes=row["notes"],
    )
