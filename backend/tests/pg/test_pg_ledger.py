"""The ledger tests (test_ledger.py) run against Postgres, plus what only Postgres can say."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from pg_helpers import make_account

from tradejournal import finance
from tradejournal.pg import accounts, ledger
from tradejournal.pg.accounts import archive_account

D = Decimal

# ---- ported from test_ledger.py --------------------------------------------------------------------------------


def test_default_cost_kind():
    assert ledger.default_cost_kind("evaluation") == "evaluation_fee"
    assert ledger.default_cost_kind("funded") == "activation_fee"


def test_add_list_and_delete(conn):
    account = make_account(conn)
    fee = ledger.add_entry(conn, account.id, "evaluation_fee", D("167"), date(2024, 1, 2), "  50K eval  ")
    reset = ledger.add_entry(conn, account.id, "reset_fee", D("80.00"), date(2024, 1, 20))
    payout = ledger.add_entry(conn, account.id, "payout", D("1500.5"), date(2024, 3, 1))
    assert (fee.amount, fee.note) == (D("167.00"), "50K eval")
    assert payout.amount == D("1500.50")
    assert payout.is_payout and not reset.is_payout
    assert [e.id for e in ledger.entries(conn, account.id)] == [fee.id, reset.id, payout.id]  # by date

    ledger.delete_entry(conn, reset.id)
    assert [e.kind for e in ledger.entries(conn, account.id)] == ["evaluation_fee", "payout"]


@pytest.mark.parametrize("amount", [D("0"), D("-5"), D("0.004")])
def test_amount_must_be_positive(conn, amount):
    account = make_account(conn)
    with pytest.raises(ValueError, match="more than zero"):
        ledger.add_entry(conn, account.id, "reset_fee", amount, date(2024, 1, 2))


def test_bad_kind_or_account(conn):
    account = make_account(conn)
    with pytest.raises(ValueError, match="unknown kind"):
        ledger.add_entry(conn, account.id, "lunch", D("10"), date(2024, 1, 2))
    with pytest.raises(LookupError):
        ledger.add_entry(conn, 999, "reset_fee", D("10"), date(2024, 1, 2))


def test_entries_across_accounts_and_archived_accounts(conn):
    account = make_account(conn)
    other = accounts.create_account(conn, "Apex PA 01", firm="Apex", account_type="funded")
    ledger.add_entry(conn, account.id, "evaluation_fee", D("167"), date(2024, 1, 2))
    archive_account(conn, account.id, outcome="passed", ended_on=date(2024, 1, 10))
    # Costs can still be added after an account is archived.
    ledger.add_entry(conn, account.id, "other_cost", D("20"), date(2024, 1, 11), "data fee")
    ledger.add_entry(conn, other.id, "activation_fee", D("85"), date(2024, 1, 12))
    assert len(ledger.entries(conn)) == 3
    assert len(ledger.entries(conn, other.id)) == 1


def test_business_totals(conn):
    account = make_account(conn)
    for kind, amount in [("evaluation_fee", "167"), ("reset_fee", "80"), ("monthly_fee", "167"),
                         ("activation_fee", "85"), ("payout", "1500"), ("payout", "250.25")]:  # fmt: skip
        ledger.add_entry(conn, account.id, kind, D(amount), date(2024, 1, 2))
    totals = finance.business_totals(ledger.entries(conn))
    assert totals.costs == D("499.00")
    assert totals.payouts == D("1750.25")
    assert totals.net == D("1251.25")


# ---- what only Postgres can say --------------------------------------------------------------------------------


def test_the_database_refuses_what_the_module_refuses(conn):
    account = make_account(conn)
    for kind, amount in [("lunch", 10), ("reset_fee", 0), ("reset_fee", -5)]:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO account_ledger (account_id, kind, amount, entry_date) VALUES (%s, %s, %s, '2024-01-02')",
                (account.id, kind, amount),
            )


def test_the_database_accepts_exactly_the_kinds_the_module_lists(conn):
    """The CHECK in migration 0002 repeats ledger.KINDS; this fails if they drift apart."""
    account = make_account(conn)
    for kind in ledger.KINDS:
        conn.execute(
            "INSERT INTO account_ledger (account_id, kind, amount, entry_date) VALUES (%s, %s, 1, '2024-01-02')",
            (account.id, kind),
        )
    assert len(ledger.entries(conn)) == len(ledger.KINDS)


def test_amounts_keep_two_decimals_exactly(conn):
    account = make_account(conn)
    entry = ledger.add_entry(conn, account.id, "payout", D("0.10"), date(2024, 1, 2))
    ledger.add_entry(conn, account.id, "payout", D("0.20"), date(2024, 1, 2))
    assert entry.amount == D("0.10")
    assert sum(e.amount for e in ledger.entries(conn)) == D("0.30")  # binary floats would give 0.30000000000000004


def test_deleting_an_account_deletes_its_ledger(conn):
    account = make_account(conn)
    ledger.add_entry(conn, account.id, "evaluation_fee", D("167"), date(2024, 1, 2))
    conn.execute("DELETE FROM accounts WHERE id = %s", (account.id,))
    assert ledger.entries(conn) == []


def test_users_cannot_see_or_touch_each_others_ledgers(db, alice, bob):
    with db.tenant_session(alice) as a:
        account = make_account(a)
        entry = ledger.add_entry(a, account.id, "payout", D("500"), date(2024, 2, 1))
    with db.tenant_session(bob) as b:
        assert ledger.entries(b) == []
        with pytest.raises(LookupError):  # Alice's account looks like a missing one
            ledger.add_entry(b, account.id, "reset_fee", D("80"), date(2024, 2, 2))
        ledger.delete_entry(b, entry.id)  # deletes nothing: row-level security hides the row
    with db.tenant_session(alice) as a:
        assert [e.id for e in ledger.entries(a)] == [entry.id]
