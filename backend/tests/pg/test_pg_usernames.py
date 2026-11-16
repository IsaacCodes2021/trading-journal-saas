"""Usernames (migration 0024) against a real database: one per account whatever the capitals, the shape held by the
database too, "not now" remembered, and only the sign-in role able to read or write them."""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from tradejournal.api.pg_services import PgAccountService

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def service(db, system) -> PgAccountService:
    return PgAccountService(db, system, clock=lambda: NOW)


def username_of(owner, user_id):
    return owner.execute("SELECT username, username_prompt_skipped_at FROM users WHERE id = %s", (user_id,)).fetchone()


# ---- the account service ------------------------------------------------------------------------------------------


def test_an_account_starts_with_no_username_and_is_asked_for_one(service, alice):
    account = service.load(alice)
    assert account.user.username is None
    assert account.ask_username is True


def test_a_username_is_saved_with_its_capitals_and_the_prompt_stops(service, alice):
    assert service.set_username(alice, "Trader_Joe") is True
    account = service.load(alice)
    assert account.user.username == "Trader_Joe"
    assert account.ask_username is False


def test_a_username_can_be_changed_and_recased(service, alice):
    for name in ("first", "second", "SECOND", "third_one"):
        assert service.set_username(alice, name) is True
        assert service.load(alice).user.username == name


def test_a_name_someone_else_holds_is_refused_whatever_the_capitals(service, owner, alice, bob):
    assert service.set_username(bob, "Trader_Joe") is True
    for attempt in ("Trader_Joe", "trader_joe", "TRADER_JOE"):
        assert service.set_username(alice, attempt) is False, attempt
    assert username_of(owner, alice)["username"] is None
    assert username_of(owner, bob)["username"] == "Trader_Joe"


def test_a_refused_name_leaves_the_old_one_in_place(service, owner, alice, bob):
    service.set_username(alice, "alice_one")
    service.set_username(bob, "bob_one")
    assert service.set_username(alice, "BOB_ONE") is False
    assert username_of(owner, alice)["username"] == "alice_one"


def test_a_name_given_up_or_freed_by_a_deleted_account_can_be_taken(service, owner, alice, bob):
    service.set_username(alice, "shared")
    service.set_username(alice, "moved_on")
    assert service.set_username(bob, "shared") is True
    owner.execute("DELETE FROM users WHERE id = %s", (bob,))  # what the sweep does a week after a deletion request
    assert service.set_username(alice, "Shared") is True


def test_not_now_is_remembered_once_and_stops_the_prompt(service, owner, alice):
    service.skip_username(alice)
    row = username_of(owner, alice)
    assert row["username"] is None and row["username_prompt_skipped_at"] == NOW
    assert service.load(alice).ask_username is False
    later = PgAccountService(service._database, service._system, clock=lambda: datetime(2027, 1, 1, tzinfo=UTC))
    later.skip_username(alice)
    assert username_of(owner, alice)["username_prompt_skipped_at"] == NOW  # the first answer stands


def test_a_username_can_be_picked_after_not_now(service, alice):
    service.skip_username(alice)
    assert service.set_username(alice, "later") is True
    assert service.load(alice).user.username == "later"


# ---- the database holds the rules itself --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["ab", "a" * 21, "has space", "dash-ed", "dot.ted", "jöe", ""])
def test_the_database_refuses_a_username_of_the_wrong_shape(owner, alice, bad):
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute("UPDATE users SET username = %s WHERE id = %s", (bad, alice))


def test_the_database_refuses_the_same_name_in_other_capitals(owner, alice, bob):
    owner.execute("UPDATE users SET username = 'Same_Name' WHERE id = %s", (alice,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner.execute("UPDATE users SET username = 'same_NAME' WHERE id = %s", (bob,))


def test_many_accounts_may_have_no_username(owner, alice, bob):
    rows = owner.execute("SELECT count(*) AS n FROM users WHERE username IS NULL").fetchone()
    assert rows["n"] == 2


# ---- who may see it -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", ["username", "username_prompt_skipped_at"])
def test_only_the_sign_in_role_reads_or_writes_the_username(owner, column):
    def may(role, privilege):
        return owner.execute(
            "SELECT has_column_privilege(%s, 'users', %s, %s) AS ok", (role, column, privilege)
        ).fetchone()["ok"]

    assert may("app_login", "SELECT") and may("app_login", "UPDATE")
    assert not may("app_login", "INSERT")
    for role in ("app_rw", "app_webhook", "app_sweep", "app_admin"):
        assert not may(role, "SELECT"), role
        assert not may(role, "UPDATE"), role
