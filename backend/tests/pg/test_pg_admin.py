"""The operator's account tools on a real Postgres (migration 0011): what they show, what they change, what they refuse,
and what the operator's database role can and cannot touch (doc 07 section 6, ADR-012)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from auth_world import ALICE, audit_rows
from fake_idp import person

from tradejournal.api.admin_service import (
    NOT_FOUND,
    OPERATOR_REFUSED,
    SELF_REFUSED,
)
from tradejournal.api.pg_services import PgAccountService
from tradejournal.api.ports import AdminRefusal
from tradejournal.pg import audit, plans, users
from tradejournal.pg import sessions as pg_sessions

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def sign_in(system, user, **kwargs):
    with system.system_session("login") as conn:
        return pg_sessions.create(conn, user, now=NOW, **kwargs)


def row(owner, user):
    return owner.execute("SELECT status, deletion_scheduled_at, is_admin FROM users WHERE id = %s", (user,)).fetchone()


def count(owner, table, where="true", *args):
    return owner.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}", args).fetchone()["n"]  # noqa: S608


def admin_events(owner, action=None):
    rows = owner.execute("SELECT * FROM audit_log WHERE actor = 'admin' ORDER BY id").fetchall()
    return [r for r in rows if action is None or r["action"] == action]


# ---- the list ----------------------------------------------------------------------------------------------------------


def make_with_email(owner, email):
    return owner.execute(
        "INSERT INTO users (idp_subject, email, created_at) VALUES (%s, %s, now()) RETURNING id",
        (f"idp|{email}", email),
    ).fetchone()["id"]


def test_the_list_shows_every_account_newest_first_with_how_many_there_are(service, owner, alice, bob, op):
    owner.execute("UPDATE users SET created_at = %s WHERE id = %s", (NOW - 3 * DAY, alice))
    owner.execute("UPDATE users SET created_at = %s WHERE id = %s", (NOW - 1 * DAY, bob))
    owner.execute("UPDATE users SET created_at = %s WHERE id = %s", (NOW - 2 * DAY, op))
    page = service.list_users(q=None, status=None, limit=50, offset=0)
    assert [u.email for u in page.users] == ["bob@example.test", "op@example.test", "alice@example.test"]
    assert page.total == 3 and page.limit == 50 and page.offset == 0
    assert [u.is_admin for u in page.users] == [False, True, False]


def test_a_page_is_a_slice_and_the_total_is_the_whole(service, owner, alice, bob, op):
    first = service.list_users(q=None, status=None, limit=2, offset=0)
    second = service.list_users(q=None, status=None, limit=2, offset=2)
    assert len(first.users) == 2 and len(second.users) == 1 and first.total == second.total == 3
    assert (first.limit, first.offset, second.limit, second.offset) == (2, 0, 2, 2)  # the page says which page it is
    assert {u.id for u in first.users}.isdisjoint({u.id for u in second.users})


def test_a_page_is_never_more_than_a_hundred_whatever_is_asked_of_the_service(service, owner):
    for i in range(105):
        make_with_email(owner, f"person{i:03}@example.test")
    assert len(service.list_users(q=None, status=None, limit=100_000, offset=0).users) == 100
    assert len(service.list_users(q=None, status=None, limit=0, offset=0).users) == 1


def test_search_finds_part_of_an_email_in_any_case(service, alice, bob):
    assert [u.email for u in service.list_users(q="LICE@exam", status=None, limit=50, offset=0).users] == [
        "alice@example.test"
    ]
    assert service.list_users(q="  bob  ", status=None, limit=50, offset=0).total == 1
    assert service.list_users(q="nobody", status=None, limit=50, offset=0).users == []


def test_search_takes_percent_underscore_and_backslash_literally(service, owner):
    make_with_email(owner, "a_b@example.test")
    make_with_email(owner, "axb@example.test")
    make_with_email(owner, "100%@example.test")
    make_with_email(owner, "100x@example.test")
    make_with_email(owner, "back\\slash@example.test")
    found = lambda q: sorted(u.email for u in service.list_users(q=q, status=None, limit=50, offset=0).users)  # noqa: E731
    assert found("a_b") == ["a_b@example.test"]
    assert found("100%") == ["100%@example.test"]
    assert found("%") == ["100%@example.test"]
    assert found("\\") == ["back\\slash@example.test"]
    assert found("_") == ["a_b@example.test"]


def test_search_finds_an_account_by_its_id(service, alice, bob):
    assert [u.id for u in service.list_users(q=str(alice), status=None, limit=50, offset=0).users] == [alice]
    assert service.list_users(q=str(uuid4()), status=None, limit=50, offset=0).users == []


def test_search_is_not_an_injection_point(service, alice):
    for q in ("'; DROP TABLE users; --", "x' OR '1'='1", "alice' --", "%' OR true OR '%"):
        assert service.list_users(q=q, status=None, limit=50, offset=0).total == 0
    assert service.list_users(q=None, status=None, limit=50, offset=0).total == 1  # the table is still there


def test_the_list_can_be_narrowed_to_one_status(service, owner, alice, bob):
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (bob,))
    assert [u.email for u in service.list_users(q=None, status="suspended", limit=50, offset=0).users] == [
        "bob@example.test"
    ]
    assert [u.email for u in service.list_users(q=None, status="active", limit=50, offset=0).users] == [
        "alice@example.test"
    ]
    assert service.list_users(q=None, status="pending_deletion", limit=50, offset=0).users == []


def test_each_person_shows_their_counters_and_zeros_when_they_have_none(service, db, alice, bob):
    with db.tenant_session(alice) as conn:
        conn.execute(
            "UPDATE user_usage SET accounts_count = 2, trades_count = 40, screenshots_count = 3, storage_bytes = 1234"
        )
    by = {u.email: u for u in service.list_users(q=None, status=None, limit=50, offset=0).users}
    assert (by["alice@example.test"].usage.accounts, by["alice@example.test"].usage.trades) == (2, 40)
    assert (by["alice@example.test"].usage.screenshots, by["alice@example.test"].usage.storage_bytes) == (3, 1234)
    assert (by["bob@example.test"].usage.accounts, by["bob@example.test"].usage.trades) == (0, 0)


def test_each_person_shows_the_plan_they_are_on_computed_for_the_whole_page(service, system, owner, alice, bob, op):
    _, code = plans.create_grant(owner, days=30, now=NOW)
    with system.system_session("login") as conn:
        plans.redeem(conn, alice, code, now=NOW)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'press', 'x')", (bob,)
    )
    by = {u.email: u.plan for u in service.list_users(q=None, status=None, limit=50, offset=0).users}
    assert (by["alice@example.test"].effective, by["alice@example.test"].source) == ("pro", "grant")
    assert (by["bob@example.test"].effective, by["bob@example.test"].source) == ("pro", "override")
    assert (by["op@example.test"].effective, by["op@example.test"].source) == ("free", "free")


# ---- one person's detail --------------------------------------------------------------------------------------------------


def test_the_detail_has_everything_about_the_account_and_nothing_a_person_wrote(service, system, db, owner, alice, op):
    sign_in(system, alice)
    sign_in(system, alice)
    ended = sign_in(system, alice)
    with system.system_session("login") as conn:
        pg_sessions.revoke(conn, ended, now=NOW)
    _, code = plans.create_grant(owner, label="Beta testers", days=30, now=NOW)
    with system.system_session("login") as conn:
        plans.redeem(conn, alice, code, now=NOW)
    first = owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by, created_at) VALUES (%s, 'free', 'chargeback', 'admin:x', %s) RETURNING id",
        (alice, NOW - 5 * DAY),
    ).fetchone()["id"]
    owner.execute(
        "UPDATE plan_overrides SET revoked_at = %s, revoked_by = 'admin:x', revoke_reason = 'paid' WHERE id = %s",
        (NOW - 4 * DAY, first),
    )
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'press', 'admin:y')",
        (alice,),
    )
    detail = service.user_detail(op, alice, ip_hash="h")
    assert detail.user.id == alice and detail.user.email == "alice@example.test"
    assert detail.active_sessions == 2  # three began, one ended
    assert [(o.plan, o.reason, o.revoked_at is None) for o in detail.overrides] == [
        ("pro", "press", True),
        ("free", "chargeback", False),
    ]
    assert detail.overrides[1].revoke_reason == "paid"
    assert [(r.code_label, r.plan) for r in detail.redemptions] == [("Beta testers", "pro")]
    assert detail.redemptions[0].ends_at == NOW + 30 * DAY
    assert (detail.user.plan.effective, detail.user.plan.source) == ("pro", "override")


def test_looking_at_a_person_is_itself_an_event_naming_who_looked_and_at_whom(service, owner, alice, op):
    service.user_detail(op, alice, ip_hash="hash-1")
    [event] = admin_events(owner)
    assert (event["action"], event["actor"], event["user_id"], event["actor_user_id"]) == (
        "admin_viewed_user",
        "admin",
        alice,
        op,
    )
    assert event["ip_hash"] == "hash-1"


def test_the_events_in_the_detail_are_the_latest_about_this_account_newest_first_and_include_the_looks(
    service, owner, alice, bob, op
):
    for i in range(25):
        owner.execute(
            "INSERT INTO audit_log (user_id, actor_user_id, actor, action, created_at) VALUES (%s, %s, 'user', 'login', %s)",
            (
                alice,
                alice,
                datetime(2020, 1, 1, tzinfo=UTC) - timedelta(minutes=i),
            ),  # long ago: every later event is newer
        )
    owner.execute("INSERT INTO audit_log (user_id, actor, action) VALUES (%s, 'user', 'login')", (bob,))
    detail = service.user_detail(op, alice)
    assert len(detail.events) == 20 and all(
        e.actor == "user" for e in detail.events
    )  # this look is written after reading
    again = service.user_detail(op, alice)
    assert again.events[0].action == "admin_viewed_user" and again.events[0].by == op  # and the next look sees it
    assert {e.action for e in again.events} <= {"login", "admin_viewed_user"}


def test_an_unknown_account_is_a_404_and_is_not_logged_as_looked_at(service, owner, op):
    with pytest.raises(AdminRefusal) as refused:
        service.user_detail(op, uuid4())
    assert (refused.value.status, refused.value.detail) == (404, NOT_FOUND)
    assert admin_events(owner) == []


def test_an_operator_may_look_at_themselves_and_at_other_operators(service, owner, op):
    other = make_with_email(owner, "second@example.test")
    owner.execute("UPDATE users SET is_admin = true WHERE id = %s", (other,))
    assert service.user_detail(op, op).user.is_admin and service.user_detail(op, other).user.is_admin


# ---- suspending -----------------------------------------------------------------------------------------------------------------


def test_suspending_signs_the_person_out_at_once_and_says_why_in_the_log(service, system, store, owner, alice, op):
    token = sign_in(system, alice)
    assert store.lookup(token) is not None
    out = service.suspend(op, alice, "chargeback abuse", ip_hash="h")
    assert out.status == "suspended" and row(owner, alice)["status"] == "suspended"
    assert store.lookup(token) is None
    [event] = admin_events(owner, "admin_suspended")
    assert (event["user_id"], event["actor_user_id"], event["meta"], event["ip_hash"]) == (
        alice,
        op,
        {"reason": "chargeback abuse"},
        "h",
    )
    assert (
        PgAccountService(system=system, database=None, clock=lambda: NOW).load(alice) is None
    )  # not signed in anywhere


def test_a_suspended_person_cannot_sign_in_until_restored(world, service, owner, alice, op):
    service.suspend(op, alice, "x")
    browser = world()
    assert browser.sign_in(ALICE).headers["location"] == "/invite?error=account_unavailable"
    service.unsuspend(op, alice, "sorted")
    assert world().sign_in(ALICE).status_code == 303


def test_restoring_makes_the_account_active_again_and_says_why(service, owner, alice, op):
    service.suspend(op, alice, "x")
    out = service.unsuspend(op, alice, "it was a mistake")
    assert out.status == "active" and row(owner, alice)["status"] == "active"
    assert [e["meta"] for e in admin_events(owner, "admin_unsuspended")] == [{"reason": "it was a mistake"}]


@pytest.mark.parametrize(
    "action", ["suspend", "unsuspend", "revoke_sessions", "schedule_deletion", "cancel_deletion", "revoke_override"]
)
def test_an_operator_cannot_do_these_to_their_own_account_and_nothing_is_written(service, owner, op, action):
    with pytest.raises(AdminRefusal) as refused:
        getattr(service, action)(op, op, "x")
    assert (refused.value.status, refused.value.detail) == (422, SELF_REFUSED)
    assert admin_events(owner) == [] and row(owner, op)["status"] == "active"


def test_an_operator_cannot_set_their_own_plan_either(service, owner, op):
    with pytest.raises(AdminRefusal) as refused:
        service.set_override(op, op, "pro", None, "x")
    assert refused.value.detail == SELF_REFUSED and count(owner, "plan_overrides") == 0


@pytest.mark.parametrize(
    "action", ["suspend", "unsuspend", "revoke_sessions", "schedule_deletion", "cancel_deletion", "revoke_override"]
)
def test_an_operator_cannot_do_these_to_another_operator(service, owner, op, action):
    other = make_with_email(owner, "second@example.test")
    owner.execute("UPDATE users SET is_admin = true WHERE id = %s", (other,))
    with pytest.raises(AdminRefusal) as refused:
        getattr(service, action)(op, other, "x")
    assert (refused.value.status, refused.value.detail) == (422, OPERATOR_REFUSED)
    assert admin_events(owner) == [] and row(owner, other)["status"] == "active"


def test_an_operator_cannot_set_another_operators_plan(service, owner, op):
    other = make_with_email(owner, "second@example.test")
    owner.execute("UPDATE users SET is_admin = true WHERE id = %s", (other,))
    with pytest.raises(AdminRefusal) as refused:
        service.set_override(op, other, "free", None, "x")
    assert refused.value.detail == OPERATOR_REFUSED and count(owner, "plan_overrides") == 0


@pytest.mark.parametrize(
    "action", ["suspend", "unsuspend", "revoke_sessions", "schedule_deletion", "cancel_deletion", "revoke_override"]
)
def test_an_unknown_account_is_a_404_for_every_change(service, owner, op, action):
    with pytest.raises(AdminRefusal) as refused:
        getattr(service, action)(op, uuid4(), "x")
    assert (refused.value.status, refused.value.detail) == (404, NOT_FOUND)
    assert admin_events(owner) == []


def test_only_an_active_account_can_be_suspended_and_only_a_suspended_one_restored(service, owner, alice, bob, op):
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (bob,))
    with pytest.raises(AdminRefusal) as already:
        service.suspend(op, bob, "x")
    assert already.value.status == 409
    with pytest.raises(AdminRefusal) as not_suspended:
        service.unsuspend(op, alice, "x")
    assert not_suspended.value.status == 409
    service.schedule_deletion(op, alice, "x")
    with pytest.raises(AdminRefusal) as pending:
        service.suspend(op, alice, "x")  # a pending deletion is cancelled, not suspended
    assert pending.value.status == 409 and row(owner, alice)["status"] == "pending_deletion"


def test_a_change_and_its_record_are_one_transaction(service, owner, alice, op, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("the log is unavailable")

    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        service.suspend(op, alice, "x")
    assert row(owner, alice)["status"] == "active"  # no change without its record
    with pytest.raises(RuntimeError):
        service.set_override(op, alice, "pro", None, "x")
    assert count(owner, "plan_overrides") == 0


# ---- sessions ---------------------------------------------------------------------------------------------------------------------


def test_taking_a_persons_sessions_ends_them_all_and_counts_and_records_it(
    service, system, store, owner, alice, bob, op
):
    mine = [sign_in(system, alice) for _ in range(3)]
    theirs = sign_in(system, bob)
    assert all(store.lookup(t) for t in mine)
    assert service.revoke_sessions(op, alice, "laptop stolen", ip_hash="h") == 3
    assert not any(store.lookup(t) for t in mine) and store.lookup(theirs) is not None  # only that person's
    assert row(owner, alice)["status"] == "active"  # signed out, not suspended
    [event] = admin_events(owner, "admin_sessions_revoked")
    assert event["meta"] == {"reason": "laptop stolen", "ended": 3} and event["user_id"] == alice


def test_with_no_sessions_to_take_it_says_zero_and_still_leaves_a_record(service, owner, alice, op):
    assert service.revoke_sessions(op, alice, "just in case") == 0
    assert len(admin_events(owner, "admin_sessions_revoked")) == 1


# ---- deletion --------------------------------------------------------------------------------------------------------------------


def test_scheduling_a_deletion_marks_the_account_a_week_away_and_signs_the_person_out(
    service, system, store, owner, alice, op
):
    token = sign_in(system, alice)
    assert store.lookup(token) is not None  # in use, so this instance has it cached
    out = service.schedule_deletion(op, alice, "they asked by email")
    assert out.status == "pending_deletion" and out.deletion_scheduled_at == NOW + 7 * DAY
    assert row(owner, alice)["deletion_scheduled_at"] == NOW + 7 * DAY and store.lookup(token) is None
    assert count(owner, "sessions", "user_id = %s AND revoked_at IS NULL", alice) == 0  # ended, not only unusable
    assert [e["meta"] for e in admin_events(owner, "admin_deletion_scheduled")] == [{"reason": "they asked by email"}]


def test_cancelling_it_puts_the_account_back_and_a_second_schedule_is_refused(service, owner, alice, op):
    service.schedule_deletion(op, alice, "x")
    with pytest.raises(AdminRefusal) as twice:
        service.schedule_deletion(op, alice, "x")
    assert twice.value.status == 409
    out = service.cancel_deletion(op, alice, "changed their mind")
    assert out.status == "active" and row(owner, alice)["deletion_scheduled_at"] is None
    with pytest.raises(AdminRefusal) as nothing:
        service.cancel_deletion(op, alice, "x")
    assert nothing.value.status == 409


def test_nothing_is_deleted_by_scheduling_only_marked(service, db, owner, alice, op):
    from pg_helpers import make_account

    with db.tenant_session(alice) as conn:
        make_account(conn, "Apex 07")
    service.schedule_deletion(op, alice, "x")
    assert count(owner, "users", "id = %s", alice) == 1
    with db.tenant_session(alice) as conn:
        assert conn.execute("SELECT count(*) AS n FROM accounts").fetchone()["n"] == 1


# ---- plans ----------------------------------------------------------------------------------------------------------------------


def plan_of(db, system, user):
    return PgAccountService(db, system, clock=lambda: NOW).load(user).plan


def test_an_override_puts_the_person_on_that_plan_and_the_account_reports_it(service, db, system, owner, alice, op):
    out = service.set_override(op, alice, "pro", NOW + 10 * DAY, "press, one month")
    assert (out.plan.effective, out.plan.source, out.plan.expires_at) == ("pro", "override", NOW + 10 * DAY)
    plan = plan_of(db, system, alice)
    assert (plan.effective, plan.source) == ("pro", "override")
    [event] = admin_events(owner, "admin_plan_override_set")
    assert event["meta"] == {
        "reason": "press, one month",
        "plan": "pro",
        "ends": (NOW + 10 * DAY).isoformat(),
        "replaced": False,
    }
    created = owner.execute("SELECT created_by, reason FROM plan_overrides").fetchone()
    assert created == {"created_by": f"admin:{op}", "reason": "press, one month"}


def test_an_override_can_lower_the_plan_even_for_someone_with_a_code(service, db, system, owner, alice, op):
    _, code = plans.create_grant(owner, days=30, now=NOW)
    with system.system_session("login") as conn:
        plans.redeem(conn, alice, code, now=NOW)
    assert plan_of(db, system, alice).source == "grant"
    service.set_override(op, alice, "free", None, "refunded")
    plan = plan_of(db, system, alice)
    assert (plan.effective, plan.source) == ("free", "override")


def test_a_new_override_replaces_the_one_in_force_and_keeps_it_in_the_history(service, owner, alice, op):
    service.set_override(op, alice, "pro", None, "first")
    service.set_override(op, alice, "free", None, "second")
    rows = owner.execute("SELECT plan, reason, revoked_at, revoke_reason FROM plan_overrides ORDER BY id").fetchall()
    assert [(r["plan"], r["reason"], r["revoked_at"] is not None) for r in rows] == [
        ("pro", "first", True),
        ("free", "second", False),
    ]
    assert rows[0]["revoke_reason"] == "replaced by a new override"
    assert [e["meta"]["replaced"] for e in admin_events(owner, "admin_plan_override_set")] == [False, True]


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=-1), -DAY])
def test_an_override_has_to_end_in_the_future(service, owner, alice, op, delta):
    with pytest.raises(AdminRefusal) as refused:
        service.set_override(op, alice, "pro", NOW + delta, "x")
    assert refused.value.status == 422 and count(owner, "plan_overrides") == 0 and admin_events(owner) == []


def test_revoking_it_puts_the_person_back_on_what_they_would_have_and_says_why(service, db, system, owner, alice, op):
    service.set_override(op, alice, "pro", None, "x")
    out = service.revoke_override(op, alice, "trial over")
    assert (out.plan.effective, out.plan.source) == ("free", "free")
    assert plan_of(db, system, alice).source == "free"
    row_ = owner.execute("SELECT revoked_by, revoke_reason FROM plan_overrides").fetchone()
    assert row_ == {"revoked_by": f"admin:{op}", "revoke_reason": "trial over"}
    assert [e["meta"] for e in admin_events(owner, "admin_plan_override_revoked")] == [{"reason": "trial over"}]
    with pytest.raises(AdminRefusal) as nothing:
        service.revoke_override(op, alice, "x")
    assert nothing.value.status == 409


# ---- the operator's database role -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT code_hash FROM grant_codes",
        "SELECT code_hash FROM invites",
        "SELECT token_hash FROM sessions",
        "SELECT idp_subject FROM users",
        "SELECT * FROM identity_removals",
        "UPDATE users SET is_admin = true",
        "UPDATE users SET email = 'x@example.test'",
        "DELETE FROM users",
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (gen_random_uuid(), decode(repeat('00', 32), 'hex'), now())",
        "UPDATE sessions SET revoked_at = now()",
        "INSERT INTO grant_redemptions (grant_code_id, user_id, plan, starts_at, ends_at) VALUES (1, gen_random_uuid(), 'pro', now(), now() + interval '1 day')",
        "UPDATE grant_codes SET days = 3650",
        "UPDATE grant_codes SET max_redemptions = NULL",
        "UPDATE invites SET max_uses = 1000",
        "UPDATE plan_overrides SET plan = 'pro'",
        "UPDATE plan_overrides SET reason = 'rewritten'",
        "DELETE FROM plan_overrides",
        "DELETE FROM grant_codes",
        "DELETE FROM invites",
        "INSERT INTO invite_redemptions (invite_id, user_id) VALUES (1, gen_random_uuid())",
    ],
)
def test_the_operator_role_cannot_do_these(system, owner, alice, op, statement):
    plans.create_grant(owner, days=30)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'x', 'y')", (alice,)
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("admin") as conn:
        conn.execute(statement)


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET action = 'edited'", "DELETE FROM audit_log", "TRUNCATE audit_log"]
)
def test_the_operator_cannot_rewrite_history(system, owner, alice, op, statement):
    audit.record(owner, "login", user_id=alice)
    with (
        pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException)),
        system.system_session("admin") as conn,
    ):
        conn.execute(statement)


def test_the_operator_can_read_the_log_and_write_to_it(system, owner, alice):
    audit.record(owner, "login", user_id=alice)
    with system.system_session("admin") as conn:
        assert conn.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"] == 1
        audit.record(conn, "admin_viewed_user", user_id=alice, actor="admin", by=alice)
    assert count(owner, "audit_log") == 2


def test_the_operator_can_make_and_revoke_codes_and_invites_with_the_columns_it_is_given(system, owner):
    with system.system_session("admin") as conn:
        conn.execute(
            "INSERT INTO grant_codes (code_hash, label, plan, days) VALUES (decode(repeat('44', 32), 'hex'), 'x', 'pro', 30)"
        )
        conn.execute("UPDATE grant_codes SET revoked_at = now()")
        conn.execute("INSERT INTO invites (code_hash, label) VALUES (decode(repeat('55', 32), 'hex'), 'x')")
        conn.execute("UPDATE invites SET revoked_at = now()")
        assert conn.execute("SELECT label, revoked_at FROM grant_codes").fetchone()["label"] == "x"
        assert conn.execute("SELECT count(*) AS n FROM invite_redemptions").fetchone()["n"] == 0


def test_the_operator_still_cannot_read_a_tenant_table_after_this_migration(system, alice, db):
    from test_pg_tenancy import TENANT_TABLES

    for table in TENANT_TABLES:
        if table == "user_usage":
            continue  # counters are the one thing it may read (migration 0004)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("admin") as conn:
            conn.execute(f'SELECT * FROM "{table}"')  # noqa: S608


# ---- the owner's command -----------------------------------------------------------------------------------------------------


def test_the_command_makes_and_removes_an_operator_and_records_who_did_it(owner, alice):
    assert users.set_operator(owner, "ALICE@example.test", operator=True) == (alice, True)  # in any capitalization
    assert row(owner, alice)["is_admin"] is True
    assert users.set_operator(owner, "alice@example.test", operator=True) == (
        alice,
        False,
    )  # already so: nothing changes
    assert users.set_operator(owner, "alice@example.test", operator=False) == (alice, True)
    assert row(owner, alice)["is_admin"] is False
    log = owner.execute("SELECT action, actor, user_id, actor_user_id FROM audit_log ORDER BY id").fetchall()
    assert [(r["action"], r["actor"], r["user_id"], r["actor_user_id"]) for r in log] == [
        ("operator_granted", "cli:owner", alice, None),
        ("operator_removed", "cli:owner", alice, None),
    ]


def test_the_command_says_so_when_there_is_no_such_account(owner, alice):
    assert users.set_operator(owner, "nobody@example.test", operator=True) is None


def test_the_command_needs_the_owners_connection_and_prints_what_happened(owner_url, owner, alice, monkeypatch, capsys):
    monkeypatch.delenv("TJ_OWNER_URL", raising=False)
    assert users.main(["make-operator", "--email", "alice@example.test"]) == 2
    monkeypatch.setenv("TJ_OWNER_URL", owner_url)
    assert users.main(["make-operator", "--email", "alice@example.test"]) == 0
    assert "Done." in capsys.readouterr().out and row(owner, alice)["is_admin"] is True
    assert users.main(["make-operator", "--email", "alice@example.test"]) == 0
    assert "Nothing to change" in capsys.readouterr().out
    assert users.main(["make-operator", "--email", "nobody@example.test"]) == 1
    assert "no account" in capsys.readouterr().err
    assert (
        users.main(["remove-operator", "--email", "alice@example.test"]) == 0 and row(owner, alice)["is_admin"] is False
    )


def test_no_role_the_api_runs_as_can_make_an_operator(system, alice):
    for profile in ("login", "webhook", "sweep", "admin"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session(profile) as conn:
            conn.execute("UPDATE users SET is_admin = true WHERE id = %s", (alice,))


# ---- the whole thing on the real app ------------------------------------------------------------------------------------------


def operator_browser(world, admin_url, owner, *, mfa=True):
    """An operator signed in through the real flow, with or without a second factor a moment ago."""
    browser = world(admin_database_url=admin_url)
    identity = person(
        subject="idp|op@example.test",
        email="op@example.test",
        **({"acr": "mfa", "auth_time": datetime.now(UTC)} if mfa else {}),
    )
    assert browser.sign_in(identity).status_code == 303
    return browser


def writes(browser):
    return {"sec-fetch-site": "same-origin", "x-csrf-token": browser.session().json()["csrf_token"]}


def test_an_operator_with_two_step_verification_can_list_look_and_suspend_on_the_real_app(
    world, admin_url, owner, op, alice
):
    browser = operator_browser(world, admin_url, owner)
    assert browser.session().json()["flags"] == {"is_admin": True, "mfa_fresh": True, "recently_authenticated": True}
    listing = browser.client.get("/api/admin/v1/users").json()
    assert listing["total"] == 2 and {u["email"] for u in listing["users"]} == {"alice@example.test", "op@example.test"}
    detail = browser.client.get(f"/api/admin/v1/users/{alice}")
    assert detail.status_code == 200 and detail.json()["user"]["email"] == "alice@example.test"
    victim = browser.another(
        "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0.0.0"
    )  # the same server, another person's browser
    assert victim.sign_in(ALICE).status_code == 303 and victim.session().status_code == 200
    response = browser.client.post(
        f"/api/admin/v1/users/{alice}/suspend", json={"reason": "abuse"}, headers=writes(browser)
    )
    assert response.status_code == 200 and response.json()["status"] == "suspended"
    assert victim.session().status_code == 401  # signed out at once
    assert [e["action"] for e in admin_events(owner)] == ["admin_viewed_user", "admin_suspended"]


def test_an_operator_without_a_second_factor_is_refused_everywhere_on_the_real_app(world, admin_url, owner, op, alice):
    browser = operator_browser(world, admin_url, owner, mfa=False)
    assert (
        browser.session().json()["flags"]["is_admin"] is True
        and browser.session().json()["flags"]["mfa_fresh"] is False
    )
    assert browser.client.get("/api/admin/v1/users").status_code == 403
    assert browser.client.get(f"/api/admin/v1/users/{alice}").status_code == 403
    response = browser.client.post(
        f"/api/admin/v1/users/{alice}/suspend", json={"reason": "x"}, headers=writes(browser)
    )
    assert response.status_code == 403 and row(owner, alice)["status"] == "active" and admin_events(owner) == []


def test_an_ordinary_person_is_refused_on_the_real_app_even_with_a_second_factor(
    world, admin_url, owner, op, alice, bob
):
    browser = world(admin_database_url=admin_url)
    browser.sign_in(person(subject=ALICE.subject, email=ALICE.email, acr="mfa", auth_time=datetime.now(UTC)))
    assert browser.client.get("/api/admin/v1/users").status_code == 403
    response = browser.client.post(f"/api/admin/v1/users/{bob}/suspend", json={"reason": "x"}, headers=writes(browser))
    assert response.status_code == 403 and row(owner, bob)["status"] == "active"


def test_a_stranger_is_refused_on_the_real_app(world, admin_url, owner, op, alice):
    stranger = world(admin_database_url=admin_url)
    assert stranger.client.get("/api/admin/v1/users").status_code == 401
    assert (
        stranger.client.post(
            f"/api/admin/v1/users/{alice}/suspend", json={"reason": "x"}, headers={"sec-fetch-site": "same-origin"}
        ).status_code
        == 401
    )


def test_a_server_without_the_operator_role_has_no_operator_tools(world, owner, op):
    browser = world()  # no admin_database_url
    browser.sign_in(
        person(subject="idp|op@example.test", email="op@example.test", acr="mfa", auth_time=datetime.now(UTC))
    )
    assert browser.client.get("/api/admin/v1/users").status_code == 503


def test_an_override_changes_what_the_person_sees_on_their_own_account_on_the_real_app(
    world, admin_url, owner, op, alice
):
    browser = operator_browser(world, admin_url, owner)
    mine = world()
    mine.sign_in(ALICE)
    assert mine.session().json()["plan"]["source"] == "free"
    body = {"plan": "pro", "expires_at": (datetime.now(UTC) + 10 * DAY).isoformat(), "reason": "press"}
    assert (
        browser.client.put(f"/api/admin/v1/users/{alice}/plan-override", json=body, headers=writes(browser)).status_code
        == 200
    )
    assert (mine.session().json()["plan"]["effective"], mine.session().json()["plan"]["source"]) == ("pro", "override")
    assert "reason" not in json.dumps(mine.session().json())  # the operator's reason is never shown to the person
    revoked = browser.client.post(
        f"/api/admin/v1/users/{alice}/plan-override/revoke", json={"reason": "over"}, headers=writes(browser)
    )
    assert revoked.status_code == 200 and mine.session().json()["plan"]["source"] == "free"


def test_a_refusal_reaches_the_client_as_a_problem_with_the_sentence_on_the_real_app(world, admin_url, owner, op):
    browser = operator_browser(world, admin_url, owner)
    me = owner.execute("SELECT id FROM users WHERE email = 'op@example.test'").fetchone()["id"]
    response = browser.client.post(f"/api/admin/v1/users/{me}/suspend", json={"reason": "x"}, headers=writes(browser))
    assert response.status_code == 422 and response.json()["detail"] == SELF_REFUSED
    missing = browser.client.get(f"/api/admin/v1/users/{uuid4()}")
    assert missing.status_code == 404 and missing.json()["detail"] == NOT_FOUND
    assert audit_rows(owner) is not None
