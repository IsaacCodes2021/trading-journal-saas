"""Orders from platforms that do not pair their own fills (TradeSea), and the fill pairs worked out from them.

Tradovate's export gives fill pairs, so its rows go straight into `fill_pairs`. TradeSea's gives filled orders, which are
stored in `executions` (migration 0025). After every import or undo that changes an account's orders, that account's
fill pairs for each symbol touched are worked out again from **every** stored order, first in first out
(`pairing.pair_fifo`), so a position held from one file to the next is paired correctly whichever file came first.

What the journal keeps through that:

* **Unchanged pairs stay as they are.** Pairing restarts at every flat moment, so a stretch that gained no order gives
  exactly the pairs it had: same ids, same trade, same notes, tags and screenshots. Importing the next day, or the same
  day again, only adds.
* **Changed pairs keep their trade.** When an order lands inside a stored stretch (a page the first import missed), that
  stretch's pairs are replaced. Each new pair joins the trade that held one of its orders, and `store.rebuild_trades`
  then regroups as it does for any import: a trade keeps its id, and one merged into another hands over its journal.
* **A trade left with no pair** hands its notes, tags and screenshots to the trade that now holds its orders. Only when
  none does (its orders were removed by an undo, or are now an open position) is it deleted, journal and all, and the
  undo's report counts it, as Tradovate's undo does.

A fill pair worked out here has the orders' source (`tradesea_orders`) and the orders' database ids as its references, so
`store.pair_from_row` and `grouping.py` read it exactly like Tradovate's. Every function takes a connection from
`Database.tenant_session` and expects the caller to hold `store.lock_user`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import psycopg

from ..importer import OpenPosition
from ..models import Execution, FillPair
from ..pairing import pair_fifo
from ..store import RebuildResult
from . import store


@dataclass
class Stored:
    """What storing one import's orders did."""

    added_ids: list[int] = field(default_factory=list)  # new orders, in the order given
    duplicate_ids: list[int] = field(default_factory=list)  # already stored in this account
    other_accounts: Counter[str] = field(default_factory=Counter)  # also stored in another account, by its name

    @property
    def linked_ids(self) -> list[int]:
        return [*self.duplicate_ids, *self.added_ids]


@dataclass
class Rederived:
    """What working an account's fill pairs out again changed."""

    pairs_added: int = 0
    pairs_removed: int = 0
    trades: RebuildResult = field(default_factory=RebuildResult)  # created, updated, and merged into another trade
    journal_moved: set[int] = field(default_factory=set)  # trades left with no pair, whose journal moved on (removed)
    deleted: set[int] = field(default_factory=set)  # trades left with no pair whose orders no trade holds now
    journal_lost: int = 0  # how many of `deleted` had a note, a tag or a screenshot
    open_positions: list[OpenPosition] = field(default_factory=list)

    @property
    def removed(self) -> set[int]:
        """Every trade that no longer exists."""
        return self.trades.removed | self.journal_moved | self.deleted


def store_executions(conn: psycopg.Connection, account_id: int, source: str, executions: Sequence[Execution]) -> Stored:
    """Store the orders this account does not have yet, in the order given (time order, so the ids also order orders
    within one second). The same order in another account is stored here too: copy traders hold identical orders in
    several accounts, and nothing in the file tells them apart. Two statements for any number of orders."""
    result = Stored()
    if not executions:
        return result
    here: dict[str, int] = {}
    for row in conn.execute(
        """SELECT executions.id, executions.account_id, executions.ref, accounts.name AS account_name
           FROM executions JOIN accounts ON accounts.id = executions.account_id
           WHERE executions.source = %s AND executions.ref = ANY(%s::text[])""",
        (source, [e.ref for e in executions]),
    ):
        if row["account_id"] == account_id:
            here[row["ref"]] = row["id"]
        else:
            result.other_accounts[row["account_name"]] += 1
    new = [e for e in executions if e.ref not in here]
    result.duplicate_ids = [here[e.ref] for e in executions if e.ref in here]
    if new:
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO executions (account_id, source, ref, executed_at, symbol, side, qty, price, commission,
                       order_type, line)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                [
                    (
                        account_id,
                        source,
                        e.ref,
                        e.executed_at,
                        e.symbol,
                        e.side,
                        e.qty,
                        e.price,
                        e.commission,
                        e.order_type,
                        e.line,
                    )
                    for e in new
                ],
                returning=True,
            )
            result.added_ids = store._returned_ids(cursor)
    return result


def link_to_import(conn: psycopg.Connection, import_id: int, execution_ids: Sequence[int]) -> None:
    conn.execute(
        "INSERT INTO import_executions (import_id, execution_id) SELECT %s, unnest(%s::bigint[]) ON CONFLICT DO NOTHING",
        (import_id, list(execution_ids)),
    )


def execution_from_row(row: dict) -> Execution:
    return Execution(
        executed_at=row["executed_at"],
        symbol=row["symbol"],
        side=row["side"],
        qty=row["qty"],
        price=row["price"],
        ref=row["ref"],
        fill_id=row["id"],
        commission=row["commission"],
        order_type=row["order_type"],
        line=row["line"],
    )


def load_executions(conn: psycopg.Connection, account_id: int, source: str, symbol: str) -> list[Execution]:
    """One account's stored orders of one symbol, oldest first."""
    rows = conn.execute(
        "SELECT * FROM executions WHERE account_id = %s AND source = %s AND symbol = %s ORDER BY executed_at, id",
        (account_id, source, symbol),
    )
    return [execution_from_row(row) for row in rows]


def rederive(conn: psycopg.Connection, account_id: int, source: str, symbols: Iterable[str]) -> Rederived:
    """Work out the fill pairs of these symbols again from every stored order, and regroup the trades they touch."""
    store.lock_user(conn)
    result = Rederived()
    for symbol in sorted(set(symbols)):
        _rederive_symbol(conn, account_id, source, symbol, result)
    return result


def _pair_key(pair: FillPair) -> tuple:
    return (
        str(pair.buy_fill_id),
        str(pair.sell_fill_id),
        pair.qty,
        pair.buy_price,
        pair.sell_price,
        pair.pnl,
        pair.bought_at,
        pair.sold_at,
        pair.direction,
    )


def _row_key(row: dict) -> tuple:
    return (
        row["buy_ref"],
        row["sell_ref"],
        row["qty"],
        row["buy_price"],
        row["sell_price"],
        row["pnl"],
        row["bought_at"],
        row["sold_at"],
        row["direction"],
    )


def _rederive_symbol(conn: psycopg.Connection, account_id: int, source: str, symbol: str, result: Rederived) -> None:
    paired = pair_fifo(load_executions(conn, account_id, source, symbol))
    result.open_positions.extend(
        OpenPosition(lot.symbol, "long" if lot.side == "buy" else "short", lot.qty, lot.opened_at)
        for lot in paired.open_lots
    )
    derived = {_pair_key(pair): pair for pair in paired.pairs}
    stored = {
        _row_key(row): row
        for row in conn.execute(
            "SELECT * FROM fill_pairs WHERE account_id = %s AND source = %s AND symbol = %s ORDER BY id",
            (account_id, source, symbol),
        )
    }
    stale = [row for key, row in stored.items() if key not in derived]
    fresh = [pair for key, pair in derived.items() if key not in stored]
    if not stale and not fresh:
        return

    # Which trade held each order before: a new pair joins it, so the journal follows the orders.
    held_by: dict[int, int] = {}  # order id -> trade id
    stale_orders: dict[int, set[int]] = {}  # trade id -> the orders its replaced pairs held
    for row in stale:
        if row["trade_id"] is None:
            continue
        for ref in (row["buy_ref"], row["sell_ref"]):
            order_id = int(ref)
            held_by[order_id] = min(held_by.get(order_id, row["trade_id"]), row["trade_id"])
            stale_orders.setdefault(row["trade_id"], set()).add(order_id)
    touched = set(stale_orders)

    conn.execute("DELETE FROM fill_pairs WHERE id = ANY(%s::bigint[])", ([row["id"] for row in stale],))
    result.pairs_removed += len(stale)
    new_ids = store.insert_fill_pairs(conn, account_id, fresh, source=source)
    result.pairs_added += len(new_ids)
    inherited: list[tuple[int, int]] = []  # (pair id, trade id)
    for pair, pair_id in zip(fresh, new_ids, strict=True):
        holders = [held_by[order] for order in (pair.buy_fill_id, pair.sell_fill_id) if order in held_by]
        if holders:
            inherited.append((pair_id, min(holders)))
    if inherited:
        conn.execute(
            """UPDATE fill_pairs SET trade_id = v.trade_id
               FROM unnest(%s::bigint[], %s::bigint[]) AS v(pair_id, trade_id)
               WHERE fill_pairs.id = v.pair_id""",
            ([pair_id for pair_id, _ in inherited], [trade_id for _, trade_id in inherited]),
        )

    still_held = [
        row["id"]
        for row in conn.execute("SELECT id FROM fill_pairs WHERE trade_id = ANY(%s::bigint[])", (sorted(touched),))
    ]
    rebuilt = store.rebuild_trades(conn, account_id, symbol, [*new_ids, *still_held])
    result.trades.absorb(rebuilt)

    with_pairs = {
        row["trade_id"]
        for row in conn.execute(
            "SELECT DISTINCT trade_id FROM fill_pairs WHERE trade_id = ANY(%s::bigint[])", (sorted(touched),)
        )
    }
    # A trade whose pairs were replaced has changed, even when its new pairs regroup exactly as the old ones did (the
    # rebuild compares member sets, and the new pairs already carried the trade's id).
    result.trades.updated |= with_pairs - rebuilt.removed
    emptied = sorted(touched - with_pairs - rebuilt.removed)
    for trade_id in emptied:
        orders = sorted(stale_orders[trade_id])
        target = conn.execute(
            """SELECT min(trade_id) AS trade_id FROM fill_pairs
               WHERE account_id = %s AND source = %s AND symbol = %s AND trade_id IS NOT NULL
                 AND (buy_ref = ANY(%s::text[]) OR sell_ref = ANY(%s::text[]))""",
            (account_id, source, symbol, [str(o) for o in orders], [str(o) for o in orders]),
        ).fetchone()["trade_id"]
        if target is not None:
            store.merge_journal(conn, trade_id, target)
            result.journal_moved.add(trade_id)
        else:
            result.journal_lost += len(store.journal_trade_ids(conn, [trade_id]))
            result.deleted.add(trade_id)
        conn.execute("DELETE FROM trades WHERE id = %s", (trade_id,))
    result.trades.created -= result.removed
    result.trades.updated -= result.removed


def open_positions(
    conn: psycopg.Connection, account_id: int, source: str, symbols: Iterable[str]
) -> list[OpenPosition]:
    """The contracts still held after the last stored order of each symbol."""
    positions: list[OpenPosition] = []
    for symbol in sorted(set(symbols)):
        positions.extend(
            OpenPosition(lot.symbol, "long" if lot.side == "buy" else "short", lot.qty, lot.opened_at)
            for lot in pair_fifo(load_executions(conn, account_id, source, symbol)).open_lots
        )
    return positions
