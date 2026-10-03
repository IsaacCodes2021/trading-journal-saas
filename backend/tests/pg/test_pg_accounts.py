"""The accounts tests (test_accounts.py, test_archive.py) run against Postgres, plus what only Postgres can say."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from pg_helpers import make_account, make_trades

from tradejournal.pg import accounts
from tradejournal.pg.accounts import archive_account, archive_summary, restore_account

# ---- ported from test_accounts.py ------------------------------------------------------------------------------


def test_create_and_change_account_type(conn):
    account = accounts.create_account(conn, "Apex PA 01", firm="Apex", account_type="funded")
    assert account.account_type == "funded"
    assert accounts.create_account(conn, "Apex Eval 02", firm="Apex").account_type == "evaluation"

    updated = accounts.update_account(
        conn,
        account.id,
        name="Apex PA 01",
        firm="Apex",
        account_type="evaluation",
        account_size=Decimal("50000"),
        starting_balance=Decimal("50000"),
    )
    assert updated.account_type == "evaluation"


def test_unknown_account_type_is_rejected(conn):
    with pytest.raises(ValueError, match="account type"):
        accounts.create_account(conn, "Mystery", account_type="live")
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():  # the database enforces it too
        conn.execute("INSERT INTO accounts (name, firm, account_type) VALUES ('x', 'Other', 'live')")


@pytest.mark.parametrize("firm", accounts.FIRMS)
def test_every_listed_firm_can_be_used(conn, firm):
    assert accounts.create_account(conn, f"{firm} 50K", firm=firm).firm == firm


def test_unlisted_firm_is_rejected(conn):
    with pytest.raises(ValueError, match="firm"):
        accounts.create_account(conn, "Someone 50K", firm="Someone Capital")


# ---- ported from test_archive.py (the ones that need no importer or query layer yet) ---------------------------


@pytest.fixture
def two_accounts(conn):
    evaluation = make_account(conn, "Apex 07")
    funded = accounts.create_account(conn, "Apex PA 01", firm="Apex", account_type="funded")
    make_trades(conn, evaluation.id, 8, day=date(2024, 1, 5))
    make_trades(conn, funded.id, 3, day=date(2023, 11, 20))
    return evaluation, funded


def test_new_accounts_are_active(conn):
    account = make_account(conn)
    assert not account.archived
    assert (account.outcome, account.ended_on, account.archive_note) == (None, None, "")


def test_archive_and_restore(conn, two_accounts):
    evaluation, _ = two_accounts
    archived = archive_account(conn, evaluation.id, outcome="passed", ended_on=date(2024, 1, 5), note="  Hit target  ")
    assert archived.archived
    assert (archived.outcome, archived.ended_on, archived.archive_note) == ("passed", date(2024, 1, 5), "Hit target")
    # Trades are untouched.
    assert conn.execute("SELECT count(*) AS n FROM trades WHERE account_id = %s", (evaluation.id,)).fetchone()["n"] == 8

    restored = restore_account(conn, evaluation.id)
    assert not restored.archived
    assert (restored.ended_on, restored.archive_note) == (None, "")


def test_outcomes_depend_on_account_type(conn, two_accounts):
    evaluation, funded = two_accounts
    with pytest.raises(ValueError, match="funded account ends as Failed or Closed"):
        archive_account(conn, funded.id, outcome="passed", ended_on=date(2023, 11, 20))
    with pytest.raises(ValueError):
        archive_account(conn, evaluation.id, outcome="won", ended_on=date(2024, 1, 5))
    assert archive_account(conn, funded.id, outcome="failed", ended_on=date(2023, 11, 20)).outcome == "failed"


def test_a_passed_evaluation_cannot_be_retyped_as_funded_while_archived(conn, two_accounts):
    evaluation, _ = two_accounts
    archive_account(conn, evaluation.id, outcome="passed", ended_on=date(2024, 1, 5))
    with pytest.raises(ValueError, match="restore it first"):
        accounts.update_account(
            conn, evaluation.id, name=evaluation.name, firm="Apex", account_type="funded",
            account_size=None, starting_balance=Decimal("0"),
        )  # fmt: skip


def test_listing_accounts(conn, two_accounts):
    evaluation, _ = two_accounts
    archive_account(conn, evaluation.id, outcome="failed", ended_on=date(2024, 1, 5))
    assert [a.name for a in accounts.list_accounts(conn)] == ["Apex 07", "Apex PA 01"]
    assert [a.name for a in accounts.list_accounts(conn, include_archived=False)] == ["Apex PA 01"]


def test_last_trading_day(conn, two_accounts):
    evaluation, funded = two_accounts
    assert accounts.last_trading_day(conn, evaluation.id) == date(2024, 1, 5)
    assert accounts.last_trading_day(conn, funded.id) == date(2023, 11, 20)
    assert accounts.last_trading_day(conn, accounts.create_account(conn, "Fresh eval").id) is None


def test_archive_summary(conn):
    made = [accounts.create_account(conn, f"Eval {n}") for n in range(5)]
    made.append(accounts.create_account(conn, "PA 1", account_type="funded"))
    for account, outcome in zip(made, ["passed", "passed", "failed", "failed", "closed", "failed"]):
        archive_account(conn, account.id, outcome=outcome, ended_on=date(2024, 3, 1))
    summary = archive_summary(accounts.list_accounts(conn))
    # Pass rate counts decided evaluations only: 2 passed of 4 (the closed one doesn't count).
    assert summary == "Evaluations: 2 passed, 2 failed, 1 closed (50% pass rate) · Funded: 1 failed"


# ---- what the port changes -------------------------------------------------------------------------------------


def test_account_names_are_unique_ignoring_case_per_user(conn):
    accounts.create_account(conn, "Apex 07")
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        accounts.create_account(conn, "APEX 07")  # was COLLATE NOCASE, now a unique index on lower(name)


def test_two_users_can_use_the_same_account_name(db, alice, bob):
    with db.tenant_session(alice) as a, db.tenant_session(bob) as b:
        assert accounts.create_account(a, "Apex 07").name == accounts.create_account(b, "Apex 07").name


def test_find_account_ignores_case_and_edge_spaces(conn):
    made = accounts.create_account(conn, "Tradeify 50k Flex")
    assert accounts.find_account(conn, "  tradeify 50K flex ") == made
    assert accounts.find_account(conn, "nope") is None


def test_listing_sorts_ignoring_case(conn):
    for name in ("zeta", "Alpha", "beta", "Gamma"):
        accounts.create_account(conn, name)
    assert [a.name for a in accounts.list_accounts(conn)] == ["Alpha", "beta", "Gamma", "zeta"]


def test_money_is_rounded_to_cents_half_up_and_stays_exact(conn):
    account = accounts.create_account(
        conn,
        "Rounding",
        account_size=Decimal("50000.005"),
        starting_balance=Decimal("100.125"),
        max_drawdown=Decimal("2500"),
    )
    assert account.account_size == Decimal("50000.01")
    assert account.starting_balance == Decimal("100.13")
    assert account.max_drawdown == Decimal("2500")
    assert isinstance(account.starting_balance, Decimal)  # numeric comes back as Decimal, never a float


def test_money_reaches_the_api_as_whole_cents(conn):
    """numeric(20,2) returns 50000.00, which the Money type sends as "50000.00"; numeric(20,6) sent "50000.000000"."""
    from pydantic import TypeAdapter

    from tradejournal.api.schemas import Money

    account = make_account(conn)
    assert TypeAdapter(Money).dump_json(account.account_size) == b'"50000.00"'
    accounts.set_fee(conn, account.id, "*", Decimal("4.125"))  # a rate may need more than cents
    assert accounts.fee_schedule(conn, account.id)["*"] == Decimal("4.125")


def test_prop_rules_round_trip_with_a_real_boolean(conn):
    account = accounts.create_account(
        conn, "Rules", trailing_type="eod", trail_lock_enabled=True, trail_lock_level=Decimal("52600"),
        daily_loss_limit=Decimal("1000"), profit_target=Decimal("3000"),
    )  # fmt: skip
    assert account.trail_lock_enabled is True
    assert (account.trailing_type, account.trail_lock_level) == ("eod", Decimal("52600"))
    with pytest.raises(ValueError, match="trailing type"):
        accounts.create_account(conn, "Bad", trailing_type="weekly")


def test_created_at_is_a_timestamp_the_database_set(conn):
    assert make_account(conn).created_at.endswith("+00:00")  # timestamptz, rendered in UTC


def test_fee_schedule_set_overwrite_and_remove(conn):
    account = make_account(conn)
    accounts.set_fee(conn, account.id, "*", Decimal("4.12"))
    accounts.set_fee(conn, account.id, " nq ", Decimal("2.50"))
    accounts.set_fee(conn, account.id, "*", Decimal("4.50"))  # replaces the default
    assert accounts.fee_schedule(conn, account.id) == {"*": Decimal("4.50"), "NQ": Decimal("2.50")}
    accounts.remove_fee(conn, account.id, "nq")
    assert accounts.fee_schedule(conn, account.id) == {"*": Decimal("4.50")}
    with pytest.raises(ValueError, match="negative"):
        accounts.set_fee(conn, account.id, "*", Decimal("-1"))


def test_a_fee_for_a_missing_account_is_refused_by_the_database(conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        accounts.set_fee(conn, 999_999, "*", Decimal("1"))
    with pytest.raises(LookupError):
        accounts.get_account(conn, 999_999)


def test_deleting_an_account_removes_its_trades_and_fees(conn):
    account = make_account(conn)
    make_trades(conn, account.id, 2)
    accounts.set_fee(conn, account.id, "*", Decimal("4"))
    conn.execute("DELETE FROM accounts WHERE id = %s", (account.id,))
    assert conn.execute("SELECT count(*) AS n FROM trades").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM fee_schedules").fetchone()["n"] == 0
