"""The operator's invites, plan codes, audit log and overview on a real Postgres (migration 0011; doc 07 section 6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from auth_world import NEWCOMER, audit_rows
from fake_idp import person

from tradejournal.api.ports import AdminRefusal
from tradejournal.pg import invites, plans

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def count(owner, table, where="true", *args):
    return owner.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}", args).fetchone()["n"]  # noqa: S608


def admin_events(owner, action=None):
    rows = owner.execute("SELECT * FROM audit_log WHERE actor = 'admin' ORDER BY id").fetchall()
    return [r for r in rows if action is None or r["action"] == action]


def make_invite(service, op, **kwargs):
    return service.create_invite(
        op,
        **{"label": "Sam", "email": None, "max_uses": 1, "days": None, "grant_code_id": None, **kwargs},
    )


def make_grant(service, op, **kwargs):
    return service.create_grant_code(
        op,
        **{
            "label": "Beta testers",
            "plan": "pro",
            "days": 90,
            "max_redemptions": None,
            "expires_in_days": None,
            **kwargs,
        },
    )


# ---- invites ----------------------------------------------------------------------------------------------------------------


def test_an_invite_is_made_with_a_code_shown_once_and_only_its_hash_kept(service, owner, op):
    made = make_invite(service, op, label="Sam, futures group", max_uses=3, days=14)
    code = made.code
    assert code.startswith("TJ-") and invites.normalize(code) == code and len(code) == 12
    row = owner.execute("SELECT * FROM invites").fetchone()
    assert bytes(row["code_hash"]) == invites.hash_code(code)
    assert code not in str(dict(row)) and code not in json.dumps(made.invite.model_dump(mode="json"))
    assert (made.invite.label, made.invite.max_uses, made.invite.used, made.invite.status) == (
        "Sam, futures group",
        3,
        0,
        "works",
    )
    assert made.invite.expires_at == NOW + 14 * DAY
    assert make_invite(service, op).code != code  # a new one each time


def test_making_an_invite_is_recorded_without_the_code_or_whom_it_is_for(service, owner, op):
    code = make_invite(service, op, label="Sam", email="Sam@Example.test", max_uses=2, days=7).code
    [event] = admin_events(owner, "admin_invite_created")
    assert (event["user_id"], event["actor_user_id"], event["actor"]) == (None, op, "admin")
    assert event["meta"] == {
        "invite": 1,
        "label": "Sam",
        "max_uses": 2,
        "days": 7,
        "grant": None,
        "for_one_address": True,
    }
    assert code not in str(audit_rows(owner)) and "sam@example.test" not in str(audit_rows(owner)).lower()


def test_an_invite_for_one_address_is_kept_in_lower_case_and_only_that_address_may_use_it(service, owner, op):
    made = make_invite(service, op, email="Sam@Example.test")
    assert made.invite.email == "sam@example.test"
    assert owner.execute("SELECT email FROM invites").fetchone()["email"] == "sam@example.test"


def test_an_invite_the_operator_makes_signs_a_newcomer_up_once_on_the_real_app(world, service, owner, op):
    code = make_invite(service, op).code
    assert world().sign_in(NEWCOMER, invite=code).status_code == 303
    again = world().sign_in(person(subject="kp_second", email="second@example.test"), invite=code)
    assert again.headers["location"] == "/invite?error=invite_invalid"  # one use
    listed = service.list_invites(limit=50, offset=0).invites[0]
    assert (listed.used, listed.status) == (1, "used_up")


def test_an_invite_for_one_address_is_refused_to_anyone_else_on_the_real_app(world, service, owner, op):
    code = make_invite(service, op, email="newcomer@example.test").code
    other = world().sign_in(person(subject="kp_other", email="other@example.test"), invite=code)
    assert other.headers["location"] == "/invite?error=invite_invalid"
    assert world().sign_in(NEWCOMER, invite=code).status_code == 303


def test_an_invite_that_carries_a_plan_code_gives_that_plan_at_sign_up(world, service, owner, op):
    grant = make_grant(service, op, days=60)
    made = make_invite(service, op, grant_code_id=grant.grant_code.id)
    assert (made.invite.grant_code_id, made.invite.grant_label) == (grant.grant_code.id, "Beta testers")
    browser = world()
    assert browser.sign_in(NEWCOMER, invite=made.code).status_code == 303
    plan = browser.session().json()["plan"]
    assert (plan["effective"], plan["source"]) == ("pro", "grant")


@pytest.mark.parametrize("how", ["unknown", "revoked", "expired"])
def test_an_invite_cannot_carry_a_plan_code_that_cannot_be_given(service, owner, op, how):
    grant = make_grant(service, op, expires_in_days=1)
    gid = grant.grant_code.id
    if how == "revoked":
        service.revoke_grant_code(op, gid, "x")
    if how == "unknown":
        gid = 9999
    service._clock = lambda: NOW + (3 * DAY if how == "expired" else timedelta(0))
    with pytest.raises(AdminRefusal) as refused:
        make_invite(service, op, grant_code_id=gid)
    assert refused.value.status == 422 and count(owner, "invites") == 0
    assert not [e for e in admin_events(owner) if e["action"] == "admin_invite_created"]


def test_the_invites_are_listed_newest_first_with_how_many_times_each_was_used_and_where_it_stands(
    service, system, owner, op, alice, bob
):
    old = make_invite(service, op, label="old")
    service._clock = lambda: NOW + DAY
    shared = make_invite(service, op, label="shared", max_uses=2)
    expiring = make_invite(service, op, label="expiring", days=1)
    revoked = make_invite(service, op, label="revoked")
    service.revoke_invite(op, revoked.invite.id, "wrong person")
    owner.execute("INSERT INTO invite_redemptions (invite_id, user_id) VALUES (%s, %s)", (old.invite.id, alice))
    owner.execute("INSERT INTO invite_redemptions (invite_id, user_id) VALUES (%s, %s)", (shared.invite.id, alice))
    service._clock = lambda: NOW + 5 * DAY  # the one with a day to live has run out
    page = service.list_invites(limit=50, offset=0)
    assert page.total == 4
    assert [(i.label, i.used, i.status) for i in page.invites] == [
        ("revoked", 0, "revoked"),
        ("expiring", 0, "expired"),
        ("shared", 1, "works"),
        ("old", 1, "used_up"),
    ]
    assert expiring.invite.id != shared.invite.id


def test_an_invite_and_a_plan_code_stop_working_at_the_very_moment_they_end(service, op):
    invite_made = make_invite(service, op, days=1)
    grant_made = make_grant(service, op, expires_in_days=1)
    service._clock = lambda: NOW + DAY - timedelta(seconds=1)
    assert service.list_invites(limit=50, offset=0).invites[0].status == "works"
    assert service.list_grant_codes(limit=50, offset=0).grant_codes[0].status == "works"
    service._clock = lambda: NOW + DAY  # exactly when they end: over
    assert service.list_invites(limit=50, offset=0).invites[0].status == "expired"
    assert service.list_grant_codes(limit=50, offset=0).grant_codes[0].status == "expired"
    assert invite_made.invite.id == 1 and grant_made.grant_code.id == 1


def test_the_invite_list_is_paged_and_its_total_is_the_whole(service, op):
    for i in range(7):
        make_invite(service, op, label=f"i{i}")
    first = service.list_invites(limit=3, offset=0)
    last = service.list_invites(limit=3, offset=6)
    assert (len(first.invites), first.total, len(last.invites), last.offset) == (3, 7, 1, 6)
    assert len(service.list_invites(limit=100_000, offset=0).invites) == 7
    assert len(service.list_invites(limit=0, offset=0).invites) == 1


def test_an_invite_can_be_revoked_with_a_reason_and_then_no_one_can_use_it(world, service, owner, op):
    made = make_invite(service, op)
    out = service.revoke_invite(op, made.invite.id, "sent to the wrong person", ip_hash="h")
    assert out.status == "revoked" and out.revoked_at == NOW
    [event] = admin_events(owner, "admin_invite_revoked")
    assert (event["meta"], event["actor_user_id"], event["ip_hash"]) == (
        {"invite": made.invite.id, "reason": "sent to the wrong person"},
        op,
        "h",
    )
    assert world().sign_in(NEWCOMER, invite=made.code).headers["location"] == "/invite?error=invite_invalid"
    assert count(owner, "users", "email = 'newcomer@example.test'") == 0


def test_revoking_an_invite_that_is_unknown_or_already_revoked_is_refused(service, owner, op):
    made = make_invite(service, op)
    with pytest.raises(AdminRefusal) as unknown:
        service.revoke_invite(op, 9999, "x")
    assert unknown.value.status == 404
    service.revoke_invite(op, made.invite.id, "x")
    with pytest.raises(AdminRefusal) as twice:
        service.revoke_invite(op, made.invite.id, "again")
    assert twice.value.status == 409
    assert len(admin_events(owner, "admin_invite_revoked")) == 1  # only the one that did something


def test_people_who_already_signed_up_with_a_revoked_invite_keep_their_accounts(world, service, owner, op):
    made = make_invite(service, op, max_uses=2)
    assert world().sign_in(NEWCOMER, invite=made.code).status_code == 303
    service.revoke_invite(op, made.invite.id, "enough")
    assert count(owner, "users", "email = 'newcomer@example.test'") == 1
    assert world().sign_in(NEWCOMER).status_code == 303  # and they can still sign in


# ---- plan codes -------------------------------------------------------------------------------------------------------------


def test_a_plan_code_is_made_with_a_code_shown_once_and_only_its_hash_kept(service, owner, op):
    made = make_grant(service, op, label="Beta testers", days=90, max_redemptions=25, expires_in_days=60)
    code = made.code
    assert code.startswith("GR-") and len(code) == 12
    row = owner.execute("SELECT * FROM grant_codes").fetchone()
    assert bytes(row["code_hash"]) == invites.hash_code(code) and row["created_by"] == f"admin:{op}"
    assert code not in str(dict(row)) and code not in json.dumps(made.grant_code.model_dump(mode="json"))
    g = made.grant_code
    assert (g.label, g.plan, g.days, g.max_redemptions, g.used, g.status) == ("Beta testers", "pro", 90, 25, 0, "works")
    assert g.expires_at == NOW + 60 * DAY and g.created_by == f"admin:{op}"


def test_making_a_plan_code_is_recorded_without_the_code(service, owner, op):
    code = make_grant(service, op, max_redemptions=5).code
    [event] = admin_events(owner, "admin_grant_code_created")
    assert event["meta"] == {"grant": 1, "label": "Beta testers", "plan": "pro", "days": 90, "max_redemptions": 5}
    assert (event["user_id"], event["actor_user_id"]) == (None, op) and code not in str(audit_rows(owner))


def test_a_plan_code_the_operator_makes_works_for_a_person_and_the_list_counts_it(service, system, owner, op, alice):
    made = make_grant(service, op, days=30)
    with system.system_session("login") as conn:
        outcome = plans.redeem(conn, alice, made.code, now=NOW)
    assert isinstance(outcome, plans.Redeemed) and outcome.days == 30
    listed = service.list_grant_codes(limit=50, offset=0).grant_codes[0]
    assert (listed.used, listed.status) == (1, "works")


def test_plan_codes_are_listed_newest_first_with_where_each_stands(service, system, owner, op, alice, bob):
    one_use = make_grant(service, op, label="one use", max_redemptions=1)
    service._clock = lambda: NOW + DAY
    short = make_grant(service, op, label="short", expires_in_days=1)
    revoked = make_grant(service, op, label="revoked")
    service.revoke_grant_code(op, revoked.grant_code.id, "leaked")
    with system.system_session("login") as conn:
        plans.redeem(conn, alice, one_use.code, now=NOW)
    service._clock = lambda: NOW + 5 * DAY
    page = service.list_grant_codes(limit=50, offset=0)
    assert page.total == 3
    assert [(g.label, g.used, g.status) for g in page.grant_codes] == [
        ("revoked", 0, "revoked"),
        ("short", 0, "expired"),
        ("one use", 1, "used_up"),
    ]
    assert short.grant_code.id != one_use.grant_code.id


def test_a_plan_code_can_be_revoked_and_those_who_used_it_keep_their_plan(service, system, owner, op, alice, bob):
    made = make_grant(service, op)
    with system.system_session("login") as conn:
        plans.redeem(conn, alice, made.code, now=NOW)
    out = service.revoke_grant_code(op, made.grant_code.id, "leaked", ip_hash="h")
    assert out.status == "revoked" and out.used == 1
    with system.system_session("login") as conn:
        assert plans.redeem(conn, bob, made.code, now=NOW) == plans.Refused()
        assert plans.effective(conn, alice, NOW).source == "grant"
    [event] = admin_events(owner, "admin_grant_code_revoked")
    assert event["meta"] == {"grant": made.grant_code.id, "reason": "leaked"} and event["ip_hash"] == "h"


def test_revoking_a_plan_code_that_is_unknown_or_already_revoked_is_refused(service, op):
    made = make_grant(service, op)
    with pytest.raises(AdminRefusal) as unknown:
        service.revoke_grant_code(op, 9999, "x")
    assert unknown.value.status == 404
    service.revoke_grant_code(op, made.grant_code.id, "x")
    with pytest.raises(AdminRefusal) as twice:
        service.revoke_grant_code(op, made.grant_code.id, "x")
    assert twice.value.status == 409


# ---- the audit log ------------------------------------------------------------------------------------------------------------


def log(owner, action, *, user=None, by=None, actor="user", minutes=0, meta=None, ip="a-keyed-hash"):
    owner.execute(
        "INSERT INTO audit_log (user_id, actor_user_id, actor, action, ip_hash, meta, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user, by, actor, action, ip, json.dumps(meta or {}), NOW + timedelta(minutes=minutes)),
    )


def view(service, **kwargs):
    return service.audit(
        **{"action": None, "q": None, "actor": None, "days": None, "limit": 50, "before": None, **kwargs}
    )


def test_the_log_is_shown_newest_first_with_the_emails_of_who_it_is_about_and_who_did_it(service, owner, alice, op):
    log(owner, "login", user=alice, by=alice, minutes=0)
    log(owner, "admin_suspended", user=alice, by=op, actor="admin", minutes=1, meta={"reason": "abuse"})
    log(owner, "admin_invite_created", by=op, actor="admin", minutes=2, meta={"invite": 1})
    events = view(service).events
    assert [e.action for e in events] == ["admin_invite_created", "admin_suspended", "login"]
    assert (events[1].user_email, events[1].actor_email, events[1].actor, events[1].meta) == (
        "alice@example.test",
        "op@example.test",
        "admin",
        {"reason": "abuse"},
    )
    assert (events[2].user_email, events[2].actor_email) == ("alice@example.test", "alice@example.test")
    assert (events[0].user_id, events[0].user_email, events[0].actor_email) == (None, None, "op@example.test")


def test_the_log_never_shows_the_address_hash(service, owner, alice):
    log(owner, "login", user=alice, by=alice, ip="secret-hash-value")
    dumped = view(service).events[0].model_dump_json()
    assert "secret-hash-value" not in dumped and "ip_hash" not in dumped


def test_an_event_about_a_deleted_account_has_no_email_left(service, owner, alice, op):
    log(owner, "login", user=alice, by=alice)
    owner.execute("DELETE FROM users WHERE id = %s", (alice,))
    [event] = view(service).events
    assert (event.user_id, event.user_email, event.actor_email) == (None, None, None)


def test_it_can_be_narrowed_by_the_kind_of_event_and_by_who_did_it(service, owner, alice, bob, op):
    log(owner, "login", user=alice, by=alice, minutes=0)
    log(owner, "export", user=alice, by=alice, minutes=1)
    log(owner, "admin_suspended", user=bob, by=op, actor="admin", minutes=2)
    log(owner, "account_deleted", user=None, actor="system", minutes=3)
    log(owner, "operator_granted", user=op, actor="cli:owner", minutes=4)
    assert [e.action for e in view(service, action="login").events] == ["login"]
    assert [e.action for e in view(service, actor="admin").events] == ["admin_suspended"]
    assert [e.action for e in view(service, actor="system").events] == ["account_deleted"]
    assert [e.action for e in view(service, actor="cli:owner").events] == ["operator_granted"]
    assert [e.action for e in view(service, actor="user").events] == ["export", "login"]
    assert [e.action for e in view(service, action="login", actor="admin").events] == []


def test_it_can_be_narrowed_to_one_person_by_part_of_an_email_or_an_id_either_as_the_subject_or_the_operator(
    service, owner, alice, bob, op
):
    log(owner, "login", user=alice, by=alice, minutes=0)
    log(owner, "login", user=bob, by=bob, minutes=1)
    log(owner, "admin_suspended", user=bob, by=op, actor="admin", minutes=2)
    assert [e.action for e in view(service, q="alice").events] == ["login"]
    assert [e.action for e in view(service, q="ALICE@EXAMPLE").events] == ["login"]
    assert [e.action for e in view(service, q="bob").events] == ["admin_suspended", "login"]
    assert [e.action for e in view(service, q="op@example").events] == ["admin_suspended"]  # the operator's own actions
    assert [e.action for e in view(service, q=str(alice)).events] == ["login"]
    assert [e.action for e in view(service, q=str(op)).events] == ["admin_suspended"]
    assert view(service, q="nobody").events == [] and view(service, q=str(uuid4())).events == []


def test_a_search_takes_percent_and_underscore_literally_and_is_not_an_injection_point(service, owner, alice):
    log(owner, "login", user=alice, by=alice)
    assert view(service, q="%").events == [] and view(service, q="_lice").events == []
    for q in ("'; DROP TABLE audit_log; --", "x' OR '1'='1"):
        assert view(service, q=q).events == []
    assert len(view(service).events) == 1


def test_it_can_be_narrowed_to_the_last_so_many_days(service, owner, alice):
    log(owner, "login", user=alice, minutes=-60 * 24 * 10)
    log(owner, "export", user=alice, minutes=-60 * 24 * 3)
    log(owner, "logout", user=alice, minutes=-60)
    assert [e.action for e in view(service, days=7).events] == ["logout", "export"]
    assert [e.action for e in view(service, days=1).events] == ["logout"]
    assert len(view(service, days=30).events) == 3
    assert len(view(service, days=None).events) == 3


def test_a_page_says_where_the_next_one_starts_and_following_it_reads_every_event_once(service, owner, alice):
    for i in range(120):
        log(owner, "login", user=alice, minutes=i, meta={"n": i})
    seen, before, pages = [], None, 0
    while True:
        page = view(service, limit=50, before=before)
        seen += [e.meta["n"] for e in page.events]
        pages += 1
        if page.next_before is None:
            break
        before = page.next_before
    assert pages == 3 and seen == list(range(119, -1, -1))  # newest first, none missing, none twice


def test_the_last_page_has_no_next_even_when_it_is_exactly_full(service, owner, alice):
    for i in range(50):
        log(owner, "login", user=alice, minutes=i)
    assert view(service, limit=50).next_before is None
    log(owner, "login", user=alice, minutes=51)
    page = view(service, limit=50)
    assert len(page.events) == 50 and page.next_before == page.events[-1].id
    assert len(view(service, limit=50, before=page.next_before).events) == 1


def test_a_page_is_never_more_than_a_hundred_whatever_is_asked_of_the_service(service, owner, alice):
    for i in range(130):
        log(owner, "login", user=alice, minutes=i)
    page = view(service, limit=100_000)
    assert len(page.events) == 100 and page.next_before is not None
    assert len(view(service, limit=0).events) == 1


def test_asking_for_events_before_the_first_one_or_far_past_the_last_gives_an_empty_page(service, owner, alice):
    log(owner, "login", user=alice)
    assert view(service, before=1).events == [] and view(service, before=2**62).events != []


def test_the_operator_cannot_change_the_log_through_the_viewer(service, owner, alice):
    log(owner, "login", user=alice)
    before = count(owner, "audit_log")
    view(service)
    assert count(owner, "audit_log") == before  # reading it is not an event


# ---- the overview ------------------------------------------------------------------------------------------------------------


def test_the_overview_counts_accounts_by_status_and_who_signed_up_and_in_this_week(service, owner, alice, bob, op):
    owner.execute(
        "UPDATE users SET created_at = %s, last_login_at = %s WHERE id = %s", (NOW - 10 * DAY, NOW - 8 * DAY, alice)
    )
    owner.execute(
        "UPDATE users SET created_at = %s, last_login_at = %s, status = 'suspended' WHERE id = %s",
        (NOW - 6 * DAY, NOW - 1 * DAY, bob),
    )
    owner.execute("UPDATE users SET created_at = %s, last_login_at = NULL WHERE id = %s", (NOW - 7 * DAY, op))
    carol = owner.execute(
        "INSERT INTO users (idp_subject, email, created_at, last_login_at, status, deletion_scheduled_at) VALUES ('c', 'c@example.test', %s, %s, 'pending_deletion', %s) RETURNING id",
        (NOW - 2 * DAY, NOW, NOW + 7 * DAY),
    ).fetchone()["id"]
    o = service.overview()
    assert (o.accounts, o.active, o.suspended, o.pending_deletion) == (4, 2, 1, 1)
    assert o.signups_7d == 3  # bob, carol, and the operator who joined exactly seven days ago
    assert o.active_7d == 2  # bob and carol; alice's last sign-in was eight days ago
    assert carol is not None


def test_someone_who_signed_in_exactly_seven_days_ago_is_still_active_this_week(service, owner, alice, bob):
    owner.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (NOW - 7 * DAY, alice))
    owner.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (NOW - 7 * DAY - timedelta(seconds=1), bob))
    assert service.overview().active_7d == 1


def test_the_overview_counts_pro_the_way_every_plan_is_worked_out(service, system, owner, alice, bob, op):
    grant = make_grant(service, op, days=30)
    with system.system_session("login") as conn:
        plans.redeem(conn, alice, grant.code, now=NOW)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'press', 'x')", (bob,)
    )
    assert (service.overview().pro, service.overview().free) == (2, 1)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'free', 'refund', 'x')", (alice,)
    )
    assert (service.overview().pro, service.overview().free) == (1, 2)  # an override that lowers a plan counts
    service._clock = lambda: NOW + 40 * DAY
    assert service.overview().pro == 1  # the code has run out


def test_the_overview_counts_only_the_invites_and_plan_codes_that_still_work(service, system, owner, alice, op):
    live = make_invite(service, op)
    make_invite(service, op, max_uses=2)
    gone = make_invite(service, op)
    service.revoke_invite(op, gone.invite.id, "x")
    used = make_invite(service, op)
    owner.execute("INSERT INTO invite_redemptions (invite_id, user_id) VALUES (%s, %s)", (used.invite.id, alice))
    make_invite(service, op, days=1)
    make_grant(service, op)
    spent = make_grant(service, op, max_redemptions=1)
    with system.system_session("login") as conn:
        plans.redeem(conn, alice, spent.code, now=NOW)
    revoked = make_grant(service, op)
    service.revoke_grant_code(op, revoked.grant_code.id, "x")
    make_grant(service, op, expires_in_days=1)
    o = service.overview()
    assert (o.open_invites, o.open_grant_codes) == (
        3,
        2,
    )  # not revoked, used up or expired: three invites and two codes
    service._clock = lambda: NOW + 2 * DAY
    o = service.overview()
    assert (o.open_invites, o.open_grant_codes) == (
        2,
        1,
    ) and live.invite.id != gone.invite.id  # the two with a day to live ran out


def test_the_overview_of_an_empty_installation_is_zeroes_and_one_operator(service, op):
    o = service.overview()
    assert (o.accounts, o.active, o.free, o.pro, o.open_invites, o.open_grant_codes) == (1, 1, 1, 0, 0, 0)


# ---- the whole thing on the real app ------------------------------------------------------------------------------------------


def operator_browser(world, admin_url):
    browser = world(admin_database_url=admin_url)
    identity = person(subject="idp|op@example.test", email="op@example.test", acr="mfa", auth_time=datetime.now(UTC))
    assert browser.sign_in(identity).status_code == 303
    return browser


def writes(browser):
    return {"sec-fetch-site": "same-origin", "x-csrf-token": browser.session().json()["csrf_token"]}


def test_an_operator_makes_an_invite_over_the_api_and_a_newcomer_signs_up_with_it(world, admin_url, owner, op):
    browser = operator_browser(world, admin_url)
    made = browser.client.post(
        "/api/admin/v1/invites", json={"label": "Sam", "max_uses": 1, "days": 7}, headers=writes(browser)
    )
    assert made.status_code == 200
    code = made.json()["code"]
    assert code.startswith("TJ-") and made.json()["invite"]["status"] == "works"
    assert browser.client.get("/api/admin/v1/invites").json()["invites"][0].keys() >= {"label", "used", "status"}
    assert code not in browser.client.get("/api/admin/v1/invites").text  # shown once, never again
    assert world().sign_in(NEWCOMER, invite=code).status_code == 303
    assert browser.client.get("/api/admin/v1/invites").json()["invites"][0]["used"] == 1


def test_an_operator_makes_a_plan_code_over_the_api_and_a_person_redeems_it(world, admin_url, owner, op, alice):
    from auth_world import ALICE

    browser = operator_browser(world, admin_url)
    made = browser.client.post(
        "/api/admin/v1/grant-codes", json={"label": "Testers", "days": 30}, headers=writes(browser)
    )
    assert made.status_code == 200
    code = made.json()["code"]
    mine = world()
    mine.sign_in(ALICE)
    token = mine.session().json()["csrf_token"]
    redeemed = mine.client.post(
        "/api/v1/grants/redeem", json={"code": code}, headers={"sec-fetch-site": "same-origin", "x-csrf-token": token}
    )
    assert redeemed.status_code == 200 and redeemed.json()["source"] == "grant"
    assert browser.client.get("/api/admin/v1/grant-codes").json()["grant_codes"][0]["used"] == 1


def test_the_audit_log_and_overview_are_served_to_an_operator_over_the_api(world, admin_url, owner, op, alice):
    browser = operator_browser(world, admin_url)
    browser.client.post("/api/admin/v1/invites", json={"label": "Sam"}, headers=writes(browser))
    log = browser.client.get("/api/admin/v1/audit", params={"action": "admin_invite_created"}).json()
    assert [e["action"] for e in log["events"]] == ["admin_invite_created"] and log["next_before"] is None
    assert log["events"][0]["actor_email"] == "op@example.test" and "ip" not in json.dumps(log)
    overview = browser.client.get("/api/admin/v1/metrics/overview").json()
    assert overview["accounts"] == 2 and overview["open_invites"] == 1


def test_an_operator_who_is_not_verified_gets_none_of_it_over_the_api(world, admin_url, owner, op):
    browser = world(admin_database_url=admin_url)
    browser.sign_in(person(subject="idp|op@example.test", email="op@example.test"))  # no second factor
    for path in (
        "/api/admin/v1/invites",
        "/api/admin/v1/grant-codes",
        "/api/admin/v1/audit",
        "/api/admin/v1/metrics/overview",
    ):
        assert browser.client.get(path).status_code == 403
    assert browser.client.post("/api/admin/v1/invites", json={}, headers=writes(browser)).status_code == 403
    assert count(owner, "invites") == 0
