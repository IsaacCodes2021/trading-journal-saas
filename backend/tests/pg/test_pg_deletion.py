"""Deleting an account on a real Postgres (requirement F-A6): who may ask, what it removes, and what it must never remove."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from pg_helpers import make_account, make_trades
from test_pg_tenancy import SYSTEM_TABLES, TENANT_TABLES

from tradejournal.pg import audit, deletion, invites, journal
from tradejournal.pg import sessions as pg_sessions

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
LOGIN, SWEEP = "login", "sweep"


def status_of(owner, user):
    return owner.execute("SELECT status, deletion_scheduled_at FROM users WHERE id = %s", (user,)).fetchone()


def ask(system, user, now=NOW):
    with system.system_session(LOGIN) as conn:
        return deletion.request(conn, user, now=now)


def cancel(system, user):
    with system.system_session(LOGIN) as conn:
        return deletion.cancel(conn, user)


def fill(db, user, name="Apex 07"):
    """Something in every kind of table: accounts and trades, a note, a tag, plus the defaults sign-in makes."""
    with db.tenant_session(user) as conn:
        account = make_account(conn, name)
        trade = make_trades(conn, account.id, 3)[0]
        journal.set_notes(conn, trade, "<p>a note</p>")
        conn.execute("INSERT INTO day_notes (trade_date, notes) VALUES ('2024-01-05', '<p>a day</p>')")


def tenant_rows(db, user) -> dict[str, int]:
    with db.tenant_session(user) as conn:
        return {t: conn.execute(f'SELECT count(*) AS n FROM "{t}"').fetchone()["n"] for t in TENANT_TABLES}  # noqa: S608


# ---- asking, and changing your mind -------------------------------------------------------------------------------------


def test_an_active_account_becomes_pending_with_a_date_a_week_away(system, owner, alice):
    assert ask(system, alice) == NOW + timedelta(days=7) == NOW + deletion.GRACE
    row = status_of(owner, alice)
    assert row["status"] == "pending_deletion" and row["deletion_scheduled_at"] == NOW + timedelta(days=7)


def test_asking_twice_moves_nothing_and_keeps_the_first_date(system, owner, alice):
    first = ask(system, alice)
    assert ask(system, alice, NOW + timedelta(days=3)) is None
    assert status_of(owner, alice)["deletion_scheduled_at"] == first


def test_a_suspended_account_cannot_ask_and_an_unknown_one_is_a_quiet_no(system, owner, alice):
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (alice,))
    assert ask(system, alice) is None
    assert status_of(owner, alice) == {"status": "suspended", "deletion_scheduled_at": None}
    from uuid import uuid4

    assert ask(system, uuid4()) is None


def test_signing_in_again_restores_a_pending_account_and_nothing_else(system, owner, alice, bob):
    ask(system, alice)
    assert cancel(system, alice) is True
    assert status_of(owner, alice) == {"status": "active", "deletion_scheduled_at": None}
    assert cancel(system, alice) is False  # not pending any more
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (bob,))
    assert cancel(system, bob) is False and status_of(owner, bob)["status"] == "suspended"  # never un-suspends


def test_the_database_itself_keeps_a_date_and_a_pending_status_together(owner, alice):
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute("UPDATE users SET status = 'pending_deletion' WHERE id = %s", (alice,))  # no date
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute("UPDATE users SET deletion_scheduled_at = %s WHERE id = %s", (NOW, alice))  # active, with a date


# ---- who may do what ----------------------------------------------------------------------------------------------------


def test_only_the_sign_in_role_can_ask_or_cancel_and_it_cannot_write_status_any_other_way(owner, system, alice):
    for function, args in (("request_account_deletion", "(uuid, timestamptz)"), ("cancel_account_deletion", "(uuid)")):
        for role in ("app_rw", "app_webhook", "app_sweep", "app_admin"):
            assert not owner.execute(
                f"SELECT has_function_privilege(%s, '{function}{args}', 'EXECUTE') AS ok", (role,)
            ).fetchone()["ok"], (role, function)  # noqa: S608
        assert owner.execute(
            f"SELECT has_function_privilege('app_login', '{function}{args}', 'EXECUTE') AS ok"
        ).fetchone()["ok"]  # noqa: S608
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session(LOGIN) as conn:
        conn.execute(
            "UPDATE users SET status = 'pending_deletion' WHERE id = %s", (alice,)
        )  # still not allowed directly
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session(LOGIN) as conn:
        conn.execute("UPDATE users SET deletion_scheduled_at = now() WHERE id = %s", (alice,))


def test_the_request_role_cannot_call_either_function_or_see_the_schedule(db, alice):
    for statement in (
        "SELECT request_account_deletion(app_user_id(), now())",
        "SELECT cancel_account_deletion(app_user_id())",
        "SELECT deletion_scheduled_at FROM users",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tenant_session(alice) as conn:
            conn.execute(statement)


def test_the_functions_cannot_be_used_to_reach_anyone_elses_account_through_a_search_path_trick(owner, system, alice):
    with system.system_session(LOGIN) as conn:
        conn.execute(
            "CREATE TEMP TABLE users (id uuid, status text, deletion_scheduled_at timestamptz) ON COMMIT DROP"
        )  # a decoy
        conn.execute("SET LOCAL search_path = pg_temp, public")
        assert deletion.request(conn, alice, now=NOW) == NOW + deletion.GRACE  # the real table, not the decoy
    assert status_of(owner, alice)["status"] == "pending_deletion"


# ---- what is due ----------------------------------------------------------------------------------------------------------


def test_only_pending_accounts_past_their_date_are_due_oldest_first_and_limited(system, owner, alice, bob):
    ask(system, alice, NOW - timedelta(days=9))  # due since two days
    ask(system, bob, NOW - timedelta(days=8))  # due since one day
    with system.system_session(SWEEP) as conn:
        assert [d.user_id for d in deletion.due(conn, now=NOW)] == [alice, bob]
        assert [d.user_id for d in deletion.due(conn, now=NOW, limit=1)] == [alice]
        assert deletion.due(conn, now=NOW - timedelta(days=5)) == []  # not yet: both dates are later than that
        assert deletion.due(conn, now=NOW)[0].idp_subject == "idp|alice@example.test"


def test_active_and_suspended_accounts_are_never_due_however_old_they_are(system, owner, alice, bob):
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (bob,))
    with system.system_session(SWEEP) as conn:
        assert deletion.due(conn, now=NOW + timedelta(days=3650)) == []


# ---- deleting -------------------------------------------------------------------------------------------------------------


def test_deleting_removes_every_row_of_theirs_in_every_table_and_not_one_of_anyone_elses(db, system, owner, alice, bob):
    fill(db, alice)
    fill(db, bob, "Bob Account")
    with system.system_session(LOGIN) as conn:
        pg_sessions.create(conn, alice, now=NOW)
        pg_sessions.create(conn, bob, now=NOW)
    before = tenant_rows(db, alice)
    assert (
        before["trades"] == 3 and before["accounts"] == 1 and before["day_notes"] == 1
    )  # there is something to delete
    bobs_before = tenant_rows(db, bob)
    ask(system, alice, NOW - timedelta(days=8))

    with system.system_session(SWEEP) as conn:
        assert deletion.delete(conn, alice, now=NOW) is True

    assert tenant_rows(db, alice) == {table: 0 for table in TENANT_TABLES}  # not one row left in any tenant table
    assert tenant_rows(db, bob) == bobs_before and bobs_before["trades"] == 3  # and all of bob's are there
    assert owner.execute("SELECT count(*) AS n FROM users WHERE id = %s", (alice,)).fetchone()["n"] == 0
    assert owner.execute("SELECT count(*) AS n FROM sessions WHERE user_id = %s", (alice,)).fetchone()["n"] == 0
    assert owner.execute("SELECT count(*) AS n FROM sessions WHERE user_id = %s", (bob,)).fetchone()["n"] == 1


def test_deleting_removes_their_invite_redemptions_and_keeps_the_audit_history_without_them(system, owner, alice):
    code = invites.create_invite(owner, label="Sam")
    with system.system_session(LOGIN) as conn:
        invite = invites.check(conn, code, "alice@example.test", now=NOW)
        assert invites.redeem(conn, invite, alice, now=NOW)
        audit.record(conn, "login", user_id=alice)
    ask(system, alice, NOW - timedelta(days=8))
    with system.system_session(SWEEP) as conn:
        deletion.delete(conn, alice, now=NOW)
    assert owner.execute("SELECT count(*) AS n FROM invite_redemptions").fetchone()["n"] == 0
    rows = owner.execute("SELECT action, user_id, actor_user_id FROM audit_log ORDER BY id").fetchall()
    assert [(r["action"], r["user_id"], r["actor_user_id"]) for r in rows] == [("login", None, None)]  # history stays


def test_an_account_that_is_not_pending_or_not_yet_due_is_not_deleted(db, system, owner, alice, bob):
    fill(db, alice)
    with system.system_session(SWEEP) as conn:
        assert deletion.delete(conn, alice, now=NOW) is False  # active
    ask(system, alice, NOW)
    with system.system_session(SWEEP) as conn:
        assert deletion.delete(conn, alice, now=NOW + timedelta(days=6, hours=23)) is False  # a minute early
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (bob,))
    with system.system_session(SWEEP) as conn:
        assert deletion.delete(conn, bob, now=NOW + timedelta(days=100)) is False
    assert tenant_rows(db, alice)["trades"] == 3  # nothing of theirs was touched
    assert owner.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 2


def test_a_person_who_signs_in_between_the_list_and_the_delete_is_not_deleted(db, system, owner, alice):
    fill(db, alice)
    ask(system, alice, NOW - timedelta(days=8))
    with system.system_session(SWEEP) as conn:
        listed = deletion.due(conn, now=NOW)
    assert [d.user_id for d in listed] == [alice]
    assert cancel(system, alice)  # the sign-in that cancels it, a moment after the list was read
    with system.system_session(SWEEP) as conn:
        assert deletion.delete(conn, listed[0].user_id, now=NOW) is False
    assert status_of(owner, alice)["status"] == "active" and tenant_rows(db, alice)["trades"] == 3


def test_the_delete_checks_the_status_itself_and_not_only_through_the_constraint(db, owner, alice):
    """The constraint makes an active account with a date impossible, so the statement's own status check is a second
    lock. Take the first off (inside a transaction that is rolled back) and prove the second still holds."""
    fill(db, alice)
    with owner.transaction(force_rollback=True):
        owner.execute("ALTER TABLE users DROP CONSTRAINT users_deletion_matches_status")
        owner.execute("UPDATE users SET deletion_scheduled_at = %s WHERE id = %s", (NOW - timedelta(days=30), alice))
        assert deletion.delete(owner, alice, now=NOW) is False  # active, with a date that is long past
    assert status_of(owner, alice)["status"] == "active" and tenant_rows(db, alice)["trades"] == 3


# ---- the sweep role can do this and nothing else --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT email FROM users",
        "SELECT * FROM users",
        "SELECT * FROM trades",
        "SELECT * FROM accounts",
        "UPDATE users SET status = 'active'",
        "UPDATE users SET is_admin = true",
        "SELECT user_id FROM audit_log",
        "DELETE FROM audit_log",
        "UPDATE audit_log SET action = 'x'",
        "TRUNCATE users",
    ],
)
def test_the_sweep_role_cannot_read_a_persons_data_or_rewrite_anything_it_does_not_own(system, alice, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session(SWEEP) as conn:
        conn.execute(statement)


def test_the_sweep_role_can_see_exactly_what_it_needs_and_the_outbox(system, alice):
    with system.system_session(SWEEP) as conn:
        conn.execute("SELECT id, status, idp_subject, deletion_scheduled_at FROM users")
        audit.record(conn, "x", user_id=alice, actor=audit.ACTOR_SYSTEM)
        conn.execute("INSERT INTO identity_removals (idp_subject) VALUES ('kp_one')")
        conn.execute("UPDATE identity_removals SET attempts = 1, last_error = 'x'")
        conn.execute("DELETE FROM identity_removals")
    for role in (LOGIN, "webhook", "admin"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session(role) as conn:
            conn.execute("SELECT * FROM identity_removals")


def test_the_outbox_is_a_system_table_that_names_no_user(owner):
    columns = [
        r["column_name"]
        for r in owner.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'identity_removals'"
        )
    ]
    assert "user_id" not in columns and set(columns) == {"idp_subject", "created_at", "attempts", "last_error"}
    assert "identity_removals" not in set(TENANT_TABLES) | set(SYSTEM_TABLES)
