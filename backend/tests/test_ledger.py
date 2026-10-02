"""Costs and payouts of prop accounts."""

from datetime import date
from decimal import Decimal

import pytest

from tradejournal import accounts, db, finance, ledger
from tradejournal.accounts import archive_account

D = Decimal


def test_default_cost_kind():
    assert ledger.default_cost_kind("evaluation") == "evaluation_fee"
    assert ledger.default_cost_kind("funded") == "activation_fee"


def test_add_list_and_delete(conn, account):
    with db.transaction(conn):
        fee = ledger.add_entry(conn, account.id, "evaluation_fee", D("167"), date(2024, 1, 2), "  50K eval  ")
        reset = ledger.add_entry(conn, account.id, "reset_fee", D("80.00"), date(2024, 1, 20))
        payout = ledger.add_entry(conn, account.id, "payout", D("1500.5"), date(2024, 3, 1))
    assert (fee.amount, fee.note) == (D("167.00"), "50K eval")
    assert payout.is_payout and not reset.is_payout
    assert [e.id for e in ledger.entries(conn, account.id)] == [fee.id, reset.id, payout.id]  # by date

    with db.transaction(conn):
        ledger.delete_entry(conn, reset.id)
    assert [e.kind for e in ledger.entries(conn, account.id)] == ["evaluation_fee", "payout"]


@pytest.mark.parametrize("amount", [D("0"), D("-5"), D("0.004")])
def test_amount_must_be_positive(conn, account, amount):
    with pytest.raises(ValueError, match="more than zero"):
        ledger.add_entry(conn, account.id, "reset_fee", amount, date(2024, 1, 2))


def test_bad_kind_or_account(conn, account):
    with pytest.raises(ValueError, match="unknown kind"):
        ledger.add_entry(conn, account.id, "lunch", D("10"), date(2024, 1, 2))
    with pytest.raises(LookupError):
        ledger.add_entry(conn, 999, "reset_fee", D("10"), date(2024, 1, 2))


def test_entries_across_accounts_and_archived_accounts(conn, account):
    with db.transaction(conn):
        other = accounts.create_account(conn, "Apex PA 01", firm="Apex", account_type="funded")
        ledger.add_entry(conn, account.id, "evaluation_fee", D("167"), date(2024, 1, 2))
        archive_account(conn, account.id, outcome="passed", ended_on=date(2024, 1, 10))
        # Costs can still be added after an account is archived.
        ledger.add_entry(conn, account.id, "other_cost", D("20"), date(2024, 1, 11), "data fee")
        ledger.add_entry(conn, other.id, "activation_fee", D("85"), date(2024, 1, 12))
    assert len(ledger.entries(conn)) == 3
    assert len(ledger.entries(conn, other.id)) == 1


def test_business_totals(conn, account):
    with db.transaction(conn):
        for kind, amount in [("evaluation_fee", "167"), ("reset_fee", "80"), ("monthly_fee", "167"),
                             ("activation_fee", "85"), ("payout", "1500"), ("payout", "250.25")]:  # fmt: skip
            ledger.add_entry(conn, account.id, kind, D(amount), date(2024, 1, 2))
    totals = finance.business_totals(ledger.entries(conn))
    assert totals.costs == D("499.00")
    assert totals.payouts == D("1750.25")
    assert totals.net == D("1251.25")
    empty = finance.business_totals([])
    assert (empty.costs, empty.payouts, empty.net) == (D("0"), D("0"), D("0"))
