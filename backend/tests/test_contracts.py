from decimal import Decimal

from tradejournal import finance
from tradejournal.contracts import CONTRACTS, contract_for, price_format

D = Decimal

# The dollar value of one tick for one contract, as the exchanges publish it. Written out apart from the table, so a
# typo in either one fails here instead of turning into wrong P&L.
TICK_VALUES = {
    "ES": "12.50", "MES": "1.25", "NQ": "5.00", "MNQ": "0.50", "RTY": "5.00", "M2K": "0.50",
    "YM": "5.00", "MYM": "0.50", "EMD": "10.00", "NKD": "25.00",
    "CL": "10.00", "MCL": "1.00", "QM": "12.50", "NG": "10.00", "QG": "12.50", "RB": "4.20", "HO": "4.20",
    "GC": "10.00", "MGC": "1.00", "SI": "25.00", "SIL": "5.00", "HG": "12.50", "MHG": "1.25",
    "PL": "5.00", "PA": "50.00",
    "ZT": "7.8125", "ZF": "7.8125", "ZN": "15.625", "TN": "15.625", "ZB": "31.25", "UB": "31.25",
    "6E": "6.25", "M6E": "1.25", "6B": "6.25", "M6B": "0.625", "6J": "6.25", "6A": "5.00", "M6A": "1.00",
    "6C": "5.00", "6S": "6.25", "6N": "5.00", "6M": "5.00",
    "ZC": "12.50", "ZW": "12.50", "KE": "12.50", "ZS": "12.50", "ZO": "12.50", "ZL": "6.00", "ZM": "10.00",
    "LE": "10.00", "GF": "12.50", "HE": "10.00",
    "BTC": "25.00", "MBT": "0.50", "ETH": "25.00", "MET": "0.05",
}  # fmt: skip


def test_every_contract_has_its_published_tick_value():
    assert set(CONTRACTS) == set(TICK_VALUES)
    for root, value in TICK_VALUES.items():
        assert CONTRACTS[root].tick_value == D(value), root


def test_every_entry_is_filed_under_its_own_root_with_positive_values():
    for root, contract in CONTRACTS.items():
        assert contract.root == root
        assert contract.point_value > 0 and contract.tick_size > 0, root


def test_the_table_agrees_with_the_point_values_tradovate_pnl_is_checked_against():
    for root, value in finance.POINT_VALUES.items():
        assert CONTRACTS[root].point_value == value, root


def test_micros_are_a_tenth_of_their_full_size_contract():
    for micro, full in (("MES", "ES"), ("MNQ", "NQ"), ("M2K", "RTY"), ("MYM", "YM"), ("MCL", "CL"), ("MGC", "GC")):
        assert CONTRACTS[micro].point_value * 10 == CONTRACTS[full].point_value
        assert CONTRACTS[micro].tick_size == CONTRACTS[full].tick_size


def test_lookup_is_by_root_and_never_guesses():
    assert contract_for("NQ").point_value == D("20")
    assert contract_for(" mnq ").root == "MNQ"
    assert contract_for("NQZ6") is None  # a full symbol is not a root; callers parse it first
    assert contract_for("XYZ") is None
    assert contract_for("") is None


def test_price_format_is_what_tradovate_writes():
    assert price_format(D("0.25")) == ("-2", "0")  # NQ, as in every Tradovate fixture
    assert price_format(D("0.1")) == ("-1", "0")
    assert price_format(D("0.01")) == ("-2", "0")
    assert price_format(D("1")) == ("0", "0")
    assert price_format(D("5")) == ("0", "0")
    assert price_format(D("0.015625")) == ("-6", "0")
