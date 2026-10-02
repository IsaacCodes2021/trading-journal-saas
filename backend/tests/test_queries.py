from datetime import date
from decimal import Decimal

import pytest
from helpers import SAMPLE, fixture_bytes

from tradejournal import accounts, db, importer
from tradejournal.queries import TradeFilter, load_trade, load_trades, symbol_roots


@pytest.fixture
def two_accounts(conn, account):
    """The sample day in an evaluation account, the 11/20 file in a funded one."""
    with db.transaction(conn):
        other = accounts.create_account(conn, "Lucid 01", firm="Lucid", account_type="funded")
    importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
    importer.import_files(conn, other.id, [("r.csv", fixture_bytes("reversal_2023-11-20.csv"))])
    return account, other


def test_no_filter_returns_everything_in_exit_order(conn, two_accounts):
    trades = load_trades(conn)
    assert len(trades) == 8 + 27
    exits = [t.exit_at for t in trades]
    assert exits == sorted(exits)


def test_filters(conn, two_accounts):
    apex, lucid = two_accounts
    assert len(load_trades(conn, TradeFilter(account_ids=(apex.id,)))) == 8
    assert len(load_trades(conn, TradeFilter(account_ids=(apex.id, lucid.id)))) == 35
    assert len(load_trades(conn, TradeFilter(start=date(2024, 1, 1)))) == 8
    assert len(load_trades(conn, TradeFilter(end=date(2023, 12, 31)))) == 27
    assert len(load_trades(conn, TradeFilter(start=date(2024, 1, 6), end=date(2024, 1, 31)))) == 0
    mnq = load_trades(conn, TradeFilter(symbol_roots=("MNQ",)))
    assert [t.symbol for t in mnq] == ["MNQZ3"]
    shorts = load_trades(conn, TradeFilter(direction="short"))
    assert shorts and {t.direction for t in shorts} == {"short"}
    assert symbol_roots(conn) == ["MNQ", "NQ"]


def test_account_type_filter(conn, two_accounts):
    apex, lucid = two_accounts
    funded = load_trades(conn, TradeFilter(account_type="funded"))
    evaluation = load_trades(conn, TradeFilter(account_type="evaluation"))
    assert (len(funded), len(evaluation)) == (27, 8)
    assert {t.account_type for t in funded} == {"funded"}
    assert {t.account_name for t in evaluation} == {"Apex 07"}
    # Combined with an account list, both conditions apply.
    assert load_trades(conn, TradeFilter(account_type="funded", account_ids=(apex.id,))) == []

    # Reclassifying an account moves all of its trades.
    with db.transaction(conn):
        accounts.update_account(
            conn, lucid.id, name="Lucid 01", firm="Lucid", account_type="evaluation",
            account_size=None, starting_balance=Decimal("0"),
        )  # fmt: skip
    assert load_trades(conn, TradeFilter(account_type="funded")) == []
    assert len(load_trades(conn, TradeFilter(account_type="evaluation"))) == 35


def test_firm_filter(conn, two_accounts):
    # Apex 07 (the fixture account) is an Apex account; Lucid 01 is Lucid.
    assert len(load_trades(conn, TradeFilter(firms=("Lucid",)))) == 27
    assert len(load_trades(conn, TradeFilter(firms=("Apex", "Lucid")))) == 35
    assert load_trades(conn, TradeFilter(firms=("Tradeify",))) == []
    assert {t.firm for t in load_trades(conn, TradeFilter(firms=("Apex",)))} == {"Apex"}


def test_record_fields(conn, two_accounts):
    first = load_trades(conn, TradeFilter(account_ids=(two_accounts[0].id,)))[5]
    assert first.account_name == "Apex 07"
    assert (first.total_qty, first.max_position, first.fill_pair_count) == (10, 10, 3)
    assert first.avg_entry == Decimal("16458.925")
    assert first.net_pnl == Decimal("1175.00")
    assert load_trade(conn, first.id) == first
    assert load_trade(conn, 999_999) is None
