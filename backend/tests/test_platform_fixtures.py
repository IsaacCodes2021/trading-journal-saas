"""The reconstructed exports of the popular futures platforms (tests/fixtures/platforms) and the session they describe.

Every file there is one platform's layout of the same made-up trading session (session.json). These tests check the
session itself, hold the files to the generator that writes them, and run the one layout the app reads today through
the real importer. The import engine of docs/design/trade-import-export-plan.md must turn every other file into the
same trades.
"""

import csv
import io
import json
from collections import defaultdict, deque
from decimal import Decimal

import pytest
from platform_fixtures import LAYOUTS, PLATFORMS_DIR, SESSION_FILE, load_session

from tradejournal.finance import POINT_VALUES
from tradejournal.grouping import group_fill_pairs
from tradejournal.parser import parse_performance_csv
from tradejournal.timeutil import TradingSession, from_utc_iso
from tradejournal.trades import build_trade

SESSION = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
FILLS = {fill["id"]: fill for fill in SESSION["fills"]}
PAIRS = {pair["id"]: pair for pair in SESSION["fill_pairs"]}


def test_every_fill_pair_is_its_price_move_times_the_point_value():
    for pair in SESSION["fill_pairs"]:
        root = SESSION["contracts"][pair["contract"]]["root"]
        entry, exit_ = FILLS[pair["entry_fill"]], FILLS[pair["exit_fill"]]
        assert (pair["entry_price"], pair["exit_price"]) == (entry["price"], exit_["price"]), pair["id"]
        move = Decimal(pair["exit_price"]) - Decimal(pair["entry_price"])
        sign = 1 if pair["direction"] == "long" else -1
        assert move * sign * pair["qty"] * POINT_VALUES[root] == Decimal(pair["gross_pnl"]), pair["id"]
        assert Decimal(SESSION["contracts"][pair["contract"]]["point_value"]) == POINT_VALUES[root]


def test_first_in_first_out_pairing_of_the_fills_gives_the_fill_pairs_and_leaves_nothing_open():
    open_lots: dict[str, deque] = defaultdict(deque)  # contract -> [fill id, signed quantity still open]
    pairs = []
    for fill in sorted(SESSION["fills"], key=lambda f: (f["at"], int(f["id"][1:]))):
        sign = 1 if fill["side"] == "buy" else -1
        remaining = fill["qty"]
        lots = open_lots[fill["contract"]]
        while remaining and lots and lots[0][1] * sign < 0:
            lot = lots[0]
            taken = min(remaining, abs(lot[1]))
            direction = "long" if lot[1] > 0 else "short"
            pairs.append((fill["contract"], direction, taken, lot[0], fill["id"]))
            lot[1] += taken * sign
            remaining -= taken
            if lot[1] == 0:
                lots.popleft()
        if remaining:
            lots.append([fill["id"], remaining * sign])

    expected = [
        (p["contract"], p["direction"], p["qty"], p["entry_fill"], p["exit_fill"]) for p in SESSION["fill_pairs"]
    ]
    assert sorted(pairs) == sorted(expected)
    assert not any(open_lots.values())


def test_every_fill_belongs_to_its_order_and_cancelled_orders_have_none():
    orders = {order["id"]: order for order in SESSION["orders"]}
    filled = defaultdict(int)
    for fill in SESSION["fills"]:
        order = orders[fill["order"]]
        assert (fill["contract"], fill["side"]) == (order["contract"], order["side"]), fill["id"]
        filled[order["id"]] += fill["qty"]
    for order in SESSION["orders"]:
        assert filled[order["id"]] == (order["qty"] if order["status"] == "filled" else 0), order["id"]


def test_trades_add_up_to_their_fill_pairs_and_the_session_total():
    claimed = [pair for trade in SESSION["trades"] for pair in trade["pairs"]]
    assert sorted(claimed) == sorted(PAIRS)  # every pair in exactly one trade
    total = Decimal(0)
    for trade in SESSION["trades"]:
        pairs = [PAIRS[p] for p in trade["pairs"]]
        assert {p["contract"] for p in pairs} == {trade["contract"]}
        assert {p["direction"] for p in pairs} == {trade["direction"]}
        assert sum(p["qty"] for p in pairs) == trade["total_qty"]
        assert sum(Decimal(p["gross_pnl"]) for p in pairs) == Decimal(trade["gross_pnl"]), trade["id"]
        assert trade["entry_at"] == min(FILLS[p["entry_fill"]]["at"] for p in pairs)
        assert trade["exit_at"] == max(FILLS[p["exit_fill"]]["at"] for p in pairs)
        total += Decimal(trade["gross_pnl"])
    assert total == Decimal(SESSION["total_gross_pnl"])


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.file)
def test_fixture_file_is_exactly_what_the_generator_writes(layout):
    on_disk = (PLATFORMS_DIR / layout.file).read_bytes()
    assert on_disk == layout.render(load_session()).encode("utf-8"), (
        f"{layout.file} differs from tests/platform_fixtures.py: change the generator and rerun it, never the file"
    )


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.file)
def test_every_row_of_a_fixture_has_as_many_cells_as_its_header(layout):
    text = (PLATFORMS_DIR / layout.file).read_bytes().decode("utf-8")  # line endings as written
    if layout.file.startswith("rithmic"):
        text = text.split("Completed Orders\r\n", 1)[1]  # the section the trades are in
    delimiter = "\t" if layout.file.endswith(".txt") else ";" if layout.file.endswith("_de.csv") else ","
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if row]
    assert len(rows) > 1
    assert {len(row) for row in rows} == {len(rows[0])}


def test_tradovate_performance_fixture_imports_to_the_session_with_todays_importer():
    result = parse_performance_csv((PLATFORMS_DIR / "tradovate_performance.csv").read_bytes(), "America/Los_Angeles")
    assert (result.fatal, result.errors, result.warnings) == (None, [], [])
    assert len(result.pairs) == len(SESSION["fill_pairs"])

    built = [
        build_trade([result.pairs[i] for i in group], fee_schedule={"*": Decimal(0)}, session=TradingSession())
        for group in group_fill_pairs(result.pairs)
    ]
    got = sorted(
        (
            t.symbol,
            t.direction,
            t.entry_at,
            t.exit_at,
            t.total_qty,
            t.max_position,
            t.gross_pnl,
            t.trade_date.isoformat(),
        )
        for t in built
    )
    expected = sorted(
        (
            t["contract"],
            t["direction"],
            from_utc_iso(t["entry_at"]),
            from_utc_iso(t["exit_at"]),
            t["total_qty"],
            t["max_position"],
            Decimal(t["gross_pnl"]),
            t["trade_date"],
        )
        for t in SESSION["trades"]
    )
    assert got == expected
