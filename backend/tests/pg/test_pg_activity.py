"""A person's own recent security activity on a real Postgres (migration 0009): only theirs, only what they may see."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from auth_world import ALICE, audit_rows

from tradejournal.api.audit import ACTIVITY_KINDS, PgAuditLog
from tradejournal.pg import audit

MIGRATION = Path(__file__).parents[2] / "tradejournal" / "pg" / "migrations" / "0009_my_activity.sql"
T0 = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


def log(owner, user, action, minutes=0, meta=None, actor="user"):
    """Write an event as the owner with a chosen time (the audit trigger refuses to change one later, so it is written once)."""
    owner.execute(
        "INSERT INTO audit_log (user_id, actor_user_id, actor, action, ip_hash, meta, created_at) "
        "VALUES (%s, %s, %s, %s, 'a-keyed-hash', %s, %s)",
        (
            user,
            user if actor == "user" else None,
            actor,
            action,
            json.dumps(meta or {}),
            T0 + timedelta(minutes=minutes),
        ),
    )


def mine(system, user, limit=30):
    with system.system_session("login") as conn:
        return audit.activity(conn, user, limit)


def test_a_person_sees_their_own_events_newest_first_and_nobody_elses(system, owner, alice, bob):
    log(owner, alice, "login", 0, {"mfa": True})
    log(owner, bob, "login", 1)
    log(owner, alice, "export", 2, {"bytes": 123})
    log(owner, bob, "export", 3)
    rows = mine(system, alice)
    assert [r["action"] for r in rows] == ["export", "login"]
    assert [r["at"] for r in rows] == [T0 + timedelta(minutes=2), T0]
    assert [r["action"] for r in mine(system, bob)] == ["export", "login"]


def test_only_the_kinds_a_person_may_see_come_back(system, owner, alice):
    for action in ACTIVITY_KINDS:
        log(owner, alice, action)
    for action in ("admin_viewed_user", "plan_override_set", "account_deleted", "something_new", "invite_redeemed"):
        log(owner, alice, action)
    log(owner, alice, "admin_suspended", actor="admin")
    assert sorted(r["action"] for r in mine(system, alice)) == sorted(ACTIVITY_KINDS)


def test_the_list_in_the_database_is_the_list_the_api_words(owner):
    """The function names the actions a person may see; `ACTIVITY_KINDS` gives each one its word. They must agree."""
    text = MIGRATION.read_text(encoding="utf-8")
    listed = set(re.findall(r"'([a-z_]+)'", text.split("a.action IN (")[1].split(")")[0]))
    assert listed == set(ACTIVITY_KINDS)


def test_a_row_carries_the_time_the_action_and_two_small_facts_and_never_an_address_or_the_rest(system, owner, alice):
    log(owner, alice, "logout_everywhere", 0, {"ended": 3, "note": "secret detail", "email": "alice@example.test"})
    with system.system_session("login") as conn:
        cursor = conn.execute("SELECT * FROM my_activity(%s, 5)", (alice,))
        columns = [c.name for c in cursor.description]
        row = cursor.fetchone()
    assert columns == ["id", "at", "action", "ended", "mfa"]
    assert row["ended"] == 3 and row["mfa"] is None
    assert "secret detail" not in str(row) and "a-keyed-hash" not in str(row) and "alice@example.test" not in str(row)


@pytest.mark.parametrize(
    "meta, ended, mfa",
    [
        ({"ended": 2, "mfa": True}, 2, True),
        ({"ended": "7", "mfa": False}, 7, False),
        ({"ended": "many", "mfa": "yes"}, None, None),  # not numbers or booleans: nothing is guessed
        ({"ended": 10**12, "mfa": 1}, None, None),  # too big to be a count of places
        ({"ended": -4}, None, None),
        ({"ended": 1.5}, None, None),
        ({}, None, None),
    ],
)
def test_the_two_facts_are_read_only_when_they_are_the_right_kind_of_value(system, owner, alice, meta, ended, mfa):
    log(owner, alice, "login", 0, meta)
    row = mine(system, alice)[0]
    assert (row["ended"], row["mfa"]) == (ended, mfa)


@pytest.mark.parametrize("limit, expected", [(1, 1), (5, 5), (100, 100), (101, 100), (100000, 100), (0, 1), (-7, 1)])
def test_the_limit_is_kept_between_one_and_a_hundred_whatever_is_asked(system, owner, alice, limit, expected):
    for i in range(120):
        log(owner, alice, "login", i)
    rows = mine(system, alice, limit)
    assert len(rows) == expected
    assert rows[0]["at"] == T0 + timedelta(minutes=119)  # always the newest first


def test_events_at_the_same_moment_keep_a_stable_order_newest_written_first(system, owner, alice):
    log(owner, alice, "login", 0)
    log(owner, alice, "export", 0)
    log(owner, alice, "logout", 0)
    assert [r["action"] for r in mine(system, alice)] == ["logout", "export", "login"]


def test_a_deleted_accounts_history_is_no_longer_reachable_by_anyone(system, owner, alice):
    log(owner, alice, "login")
    owner.execute("DELETE FROM users WHERE id = %s", (alice,))
    assert mine(system, alice) == []
    assert (
        owner.execute("SELECT count(*) AS n FROM audit_log WHERE user_id IS NULL").fetchone()["n"] == 1
    )  # kept, detached


def test_only_the_sign_in_role_may_call_it(system, owner, alice, db):
    log(owner, alice, "login")
    for profile in ("webhook", "sweep", "admin"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session(profile) as conn:
            conn.execute("SELECT * FROM my_activity(%s, 5)", (alice,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tenant_session(alice) as conn:
        conn.execute("SELECT * FROM my_activity(%s, 5)", (alice,))


def test_the_sign_in_role_still_cannot_read_the_log_itself(system, owner, alice):
    log(owner, alice, "login")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("login") as conn:
        conn.execute("SELECT * FROM audit_log")


def test_the_function_cannot_be_bent_by_a_search_path_decoy(system, owner, alice, bob):
    """Its search path is fixed: a table of the same name placed first in a session's path is not what it reads."""
    log(owner, alice, "login")
    log(owner, bob, "export", 5)
    with system.system_session("login") as conn:
        conn.execute("CREATE TEMP TABLE audit_log (id bigint, user_id uuid, action text) ON COMMIT DROP")
        conn.execute("SET LOCAL search_path = pg_temp, public")
        assert [r["action"] for r in conn.execute("SELECT action FROM my_activity(%s, 5)", (alice,)).fetchall()] == [
            "login"
        ]


def test_the_real_port_words_each_event_and_hides_what_it_does_not_know(system, owner, alice):
    log(owner, alice, "signup", 0, {"mfa": False})
    log(owner, alice, "login", 1, {"mfa": True})
    log(owner, alice, "logout_everywhere", 2, {"ended": 2})
    log(owner, alice, "admin_viewed_user", 3)
    events = PgAuditLog(system).recent(alice, 10)
    assert [(e.kind, e.ended, e.mfa) for e in events] == [
        ("sign_out_everywhere", 2, None),
        ("sign_in", None, True),
        ("sign_up", None, False),
    ]


# ---- on the real app, through real sign-in -------------------------------------------------------------------------------


def test_signing_in_and_out_shows_up_in_your_own_list_on_the_real_app(world, owner, alice):
    laptop = world()
    assert laptop.sign_in(ALICE).status_code == 303
    phone = laptop.another(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Mobile/15E148 Safari/604.1"
    )
    assert phone.sign_in(ALICE).status_code == 303
    writes = {"sec-fetch-site": "same-origin", "x-csrf-token": laptop.session().json()["csrf_token"]}
    assert laptop.client.delete("/api/v1/sessions", headers=writes).status_code == 200  # the phone is signed out
    events = laptop.client.get("/api/v1/activity").json()["events"]
    assert [e["kind"] for e in events] == ["sign_out_everywhere", "sign_in", "sign_in"]
    assert events[0]["ended"] == 1 and events[1]["mfa"] is False
    assert set(events[0]) == {"at", "kind", "ended", "mfa"}
    assert "keyed" not in json.dumps(events) and "ip_hash" not in json.dumps(events)
    assert len(audit_rows(owner)) == 3  # the log holds the address hash and the rest; the person is shown none of it


def test_someone_elses_sign_ins_are_not_in_your_list_on_the_real_app(world, owner, alice, bob):
    mine_ = world()
    assert mine_.sign_in(ALICE).status_code == 303
    log(owner, bob, "login", 0)
    log(owner, bob, "export", 1)
    events = mine_.client.get("/api/v1/activity").json()["events"]
    assert [e["kind"] for e in events] == ["sign_in"]


def test_a_stranger_gets_nothing_from_the_real_app(world):
    stranger = world()
    assert stranger.client.get("/api/v1/activity").status_code == 401
