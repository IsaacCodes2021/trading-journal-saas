"""Archiving accounts that passed, failed or closed."""

from datetime import date
from decimal import Decimal

import pytest

from helpers import SAMPLE, fixture_bytes
from tradejournal import accounts, db, importer
from tradejournal.accounts import archive_account, archive_summary, restore_account
from tradejournal.queries import TradeFilter, load_trades
from tradejournal.ui.state import FilterState


@pytest.fixture
def two_accounts(conn, account):
    """The sample day in an Apex evaluation; the 11/20 file in a funded account."""
    with db.transaction(conn):
        funded = accounts.create_account(conn, "Apex PA 01", firm="Apex", account_type="funded")
    importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
    importer.import_files(conn, funded.id, [("r.csv", fixture_bytes("reversal_2023-11-20.csv"))])
    return account, funded


def test_new_accounts_are_active(conn, account):
    assert not account.archived
    assert (account.outcome, account.ended_on, account.archive_note) == (None, None, "")


def test_archive_and_restore(conn, two_accounts):
    evaluation, _ = two_accounts
    with db.transaction(conn):
        archived = archive_account(
            conn, evaluation.id, outcome="passed", ended_on=date(2024, 1, 5), note="  Hit target on day 1  "
        )
    assert archived.archived
    assert (archived.outcome, archived.ended_on, archived.archive_note) == ("passed", date(2024, 1, 5), "Hit target on day 1")
    # Trades are untouched.
    assert len(load_trades(conn, TradeFilter(account_ids=(evaluation.id,)))) == 8

    with db.transaction(conn):
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
    with db.transaction(conn):
        archive_account(conn, evaluation.id, outcome="passed", ended_on=date(2024, 1, 5))
    with pytest.raises(ValueError, match="restore it first"):
        accounts.update_account(
            conn, evaluation.id, name=evaluation.name, firm="Apex", account_type="funded",
            account_size=None, starting_balance=Decimal("0"),
        )  # fmt: skip


def test_archived_trades_are_hidden_only_when_asked(conn, two_accounts):
    evaluation, funded = two_accounts
    with db.transaction(conn):
        archive_account(conn, evaluation.id, outcome="failed", ended_on=date(2024, 1, 5))
    assert len(load_trades(conn)) == 35  # the default filter keeps everything
    visible = load_trades(conn, TradeFilter(include_archived=False))
    assert len(visible) == 27
    assert {t.account_id for t in visible} == {funded.id}


def test_listing_accounts(conn, two_accounts):
    evaluation, funded = two_accounts
    with db.transaction(conn):
        archive_account(conn, evaluation.id, outcome="failed", ended_on=date(2024, 1, 5))
    assert [a.name for a in accounts.list_accounts(conn)] == ["Apex 07", "Apex PA 01"]
    assert [a.name for a in accounts.list_accounts(conn, include_archived=False)] == ["Apex PA 01"]


def test_last_trading_day(conn, two_accounts):
    evaluation, funded = two_accounts
    assert accounts.last_trading_day(conn, evaluation.id) == date(2024, 1, 5)
    assert accounts.last_trading_day(conn, funded.id) == date(2023, 11, 20)
    with db.transaction(conn):
        empty = accounts.create_account(conn, "Fresh eval")
    assert accounts.last_trading_day(conn, empty.id) is None


def test_archive_summary(conn):
    with db.transaction(conn):
        made = [accounts.create_account(conn, f"Eval {n}") for n in range(5)]
        made.append(accounts.create_account(conn, "PA 1", account_type="funded"))
        for account, outcome in zip(made, ["passed", "passed", "failed", "failed", "closed", "failed"]):
            archive_account(conn, account.id, outcome=outcome, ended_on=date(2024, 3, 1))
    summary = archive_summary(accounts.list_accounts(conn))
    # Pass rate counts decided evaluations only: 2 passed of 4 (the closed one doesn't count).
    assert summary == "Evaluations: 2 passed, 2 failed, 1 closed (50% pass rate) · Funded: 1 failed"
    assert archive_summary([]) == ""


def test_the_screens_hide_archived_accounts_by_default():
    assert FilterState().to_filter().include_archived is False
    assert FilterState(show_archived=True).to_filter().include_archived is True
