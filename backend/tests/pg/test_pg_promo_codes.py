"""Promo codes on a real Postgres (migration 0020, docs/design/promo-codes.md Phase 1): a code the operator types (FREE30),
kept readable, redeemed in any case, at sign-up or in Settings, and never reused."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
from auth_world import NEWCOMER, audit_rows, users_with_subject

from tradejournal.api.ports import AdminRefusal
from tradejournal.pg import admin_codes, invites, plans

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)  # the operator service's clock (conftest OPERATOR_NOW)
DAY = timedelta(days=1)


def make_promo(service, op, code="FREE30", **kwargs):
    return service.create_grant_code(
        op,
        **{
            "label": "",
            "plan": "pro",
            "days": 30,
            "max_redemptions": None,
            "expires_in_days": None,
            "code": code,
            **kwargs,
        },
    )


def redeem(system, user, code, now=NOW):
    with system.system_session("login") as conn:
        return plans.redeem(conn, user, code, now=now)


def plan_of(system, user, now=NOW):
    with system.system_session("login") as conn:
        return plans.effective(conn, user, now)


# ---- making one ---------------------------------------------------------------------------------------------------------


def test_a_typed_code_is_kept_readable_in_capitals_beside_its_hash(service, owner, op):
    made = make_promo(service, op, code="free30", label="Launch post")
    assert made.code == "FREE30" and made.grant_code.code == "FREE30" and made.grant_code.kind == "free_days"
    row = owner.execute("SELECT code, code_hash, kind, label FROM grant_codes").fetchone()
    assert (row["code"], bytes(row["code_hash"]), row["kind"], row["label"]) == (
        "FREE30",
        invites.hash_code("FREE30"),
        "free_days",
        "Launch post",
    )


def test_a_random_code_still_keeps_only_its_hash(service, owner, op):
    made = make_promo(service, op, code=None, label="Beta testers")
    assert made.code.startswith("GR-") and made.grant_code.code is None
    assert owner.execute("SELECT code FROM grant_codes").fetchone()["code"] is None


@pytest.mark.parametrize("again", ["FREE30", "free30", "Free30"])
def test_a_code_is_never_made_twice_in_any_case(service, op, again):
    make_promo(service, op)
    with pytest.raises(AdminRefusal) as refused:
        make_promo(service, op, code=again)
    assert refused.value.status == 422 and "already in use" in refused.value.detail


def test_a_revoked_or_expired_code_is_still_never_reused(service, owner, op):
    first = make_promo(service, op, code="SPRING")
    service.revoke_grant_code(op, first.grant_code.id, "campaign over")
    with pytest.raises(AdminRefusal):
        make_promo(service, op, code="spring")
    owner.execute("UPDATE grant_codes SET expires_at = %s WHERE code = 'SPRING'", (NOW - DAY,))
    with pytest.raises(AdminRefusal):
        make_promo(service, op, code="SPRING")
    assert owner.execute("SELECT count(*) AS n FROM grant_codes").fetchone()["n"] == 1


def test_the_database_itself_refuses_a_duplicate_or_a_code_of_the_wrong_shape(owner):
    plans.create_grant(owner, days=7, code="WELCOME", now=NOW)
    with pytest.raises(psycopg.errors.UniqueViolation):
        plans.create_grant(owner, days=7, code="welcome", now=NOW)
    for bad in (
        "ab",
        "FREE-30",
        "free30",
        "X" * 21,
    ):  # the table stores capitals only, and the shape is checked there too
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(
                "INSERT INTO grant_codes (code_hash, code, plan, days) VALUES (%s, %s, 'pro', 7)",
                (invites.hash_code(bad + "-unique"), bad),
            )


def test_the_owner_command_refuses_a_code_of_the_wrong_shape(owner):
    for bad in ("AB", "FREE 30", "FREE-30", "X" * 21):
        with pytest.raises(ValueError, match="3 to 20 letters and digits"):
            plans.create_grant(owner, days=7, code=bad, now=NOW)


def test_a_code_that_ends_on_a_day_works_to_the_end_of_that_day_utc(service, system, op, alice, bob):
    made = make_promo(service, op, expires_on=date(2026, 10, 10))
    assert made.grant_code.expires_at == datetime(2026, 10, 11, tzinfo=UTC)
    assert isinstance(
        redeem(system, alice, "FREE30", now=datetime(2026, 10, 10, 23, 59, 59, tzinfo=UTC)), plans.Redeemed
    )
    assert isinstance(redeem(system, bob, "FREE30", now=datetime(2026, 10, 11, tzinfo=UTC)), plans.Refused)


def test_a_code_cannot_be_made_to_end_on_a_day_already_past(service, op):
    with pytest.raises(AdminRefusal) as refused:
        make_promo(service, op, expires_on=date(2026, 10, 3))
    assert refused.value.status == 422


def test_making_a_typed_code_is_logged_with_the_code_and_a_random_one_without(service, owner, op):
    typed = make_promo(service, op, code="FREE30")
    random = make_promo(service, op, code=None, label="Testers")
    service.revoke_grant_code(op, typed.grant_code.id, "done")
    rows = owner.execute("SELECT action, meta FROM audit_log WHERE actor = 'admin' ORDER BY id").fetchall()
    assert [(r["action"], r["meta"].get("code")) for r in rows] == [
        ("admin_grant_code_created", "FREE30"),
        ("admin_grant_code_created", None),
        ("admin_grant_code_revoked", "FREE30"),
    ]
    assert random.code not in str(rows)  # a random code is never written down anywhere


# ---- who may read it ----------------------------------------------------------------------------------------------------


def test_the_operator_reads_the_typed_code_but_still_never_a_hash(system):
    with system.system_session("admin") as conn:
        conn.execute("SELECT id, code, kind, label FROM grant_codes")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("admin") as conn:
        conn.execute("SELECT code_hash FROM grant_codes")


def test_the_sign_in_role_has_no_need_to_read_the_code(owner):
    for column in ("code", "kind"):
        assert not owner.execute(
            "SELECT has_column_privilege('app_login', 'grant_codes', %s, 'SELECT') AS ok", (column,)
        ).fetchone()["ok"]
    for column in ("code", "kind"):
        assert owner.execute(
            "SELECT has_column_privilege('app_admin', 'grant_codes', %s, 'INSERT') AS ok", (column,)
        ).fetchone()["ok"]
    assert not owner.execute(
        "SELECT has_column_privilege('app_admin', 'grant_codes', 'code', 'UPDATE') AS ok"
    ).fetchone()["ok"]  # a code cannot be edited: revoke it and make another


# ---- redeeming it -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["FREE30", "free30", " Free30 ", "free 30", "FREE_30", "fr ee30"])
def test_a_promo_code_works_however_it_is_typed(service, system, op, alice, typed):
    make_promo(service, op)
    assert redeem(system, alice, typed) == plans.Redeemed("pro", 30, NOW + 30 * DAY)
    plan = plan_of(system, alice)
    assert (plan.plan, plan.source, plan.expires_at) == ("pro", "grant", NOW + 30 * DAY)


@pytest.mark.parametrize("typed", ["FREE3", "FREE300", "FREE30X", "", "F", "FREE30" * 10, "GR-FREE-30"])
def test_anything_else_is_the_one_refusal(service, system, op, alice, typed):
    make_promo(service, op)
    assert redeem(system, alice, typed) == plans.Refused()


def test_a_person_uses_a_promo_code_once_and_its_limit_holds(service, system, op, alice, bob):
    make_promo(service, op, max_redemptions=1, days=7)
    assert isinstance(redeem(system, alice, "free30"), plans.Redeemed)
    assert redeem(system, alice, "FREE30", now=NOW + 8 * DAY) == plans.Refused()  # used once already
    assert redeem(system, bob, "FREE30") == plans.Refused()  # the one use is taken


def test_old_random_codes_keep_working_beside_typed_ones(service, system, owner, op, alice, bob):
    make_promo(service, op, code="FREE30")
    _, old = plans.create_grant(owner, days=10, now=NOW)
    assert redeem(system, alice, old.lower()) == plans.Redeemed("pro", 10, NOW + 10 * DAY)
    assert redeem(system, bob, "free30") == plans.Redeemed("pro", 30, NOW + 30 * DAY)


def test_the_check_before_sign_up_says_only_whether_somebody_could_still_use_it(service, system, owner, op, alice):
    make_promo(service, op, code="ONCE", max_redemptions=1)
    revoked = make_promo(service, op, code="GONE")
    service.revoke_grant_code(op, revoked.grant_code.id, "x")
    make_promo(service, op, code="LATE", expires_in_days=1)
    with system.system_session("login") as conn:
        assert plans.usable_by_anyone(conn, "once", now=NOW)
        assert not plans.usable_by_anyone(conn, "GONE", now=NOW)
        assert not plans.usable_by_anyone(conn, "LATE", now=NOW + 2 * DAY)
        assert not plans.usable_by_anyone(conn, "LATE", now=NOW + DAY)  # the instant it ends, it has ended
        assert plans.usable_by_anyone(conn, "LATE", now=NOW + DAY - timedelta(microseconds=1))
        assert not plans.usable_by_anyone(conn, "NOPE", now=NOW)
        assert not plans.usable_by_anyone(conn, None, now=NOW)
    redeem(system, alice, "ONCE")
    with system.system_session("login") as conn:
        assert not plans.usable_by_anyone(conn, "ONCE", now=NOW)


# ---- the operator's list ------------------------------------------------------------------------------------------------


def test_the_list_can_show_working_ended_or_revoked_codes(service, system, owner, op, alice):
    works = make_promo(service, op, code="WORKS")
    used = make_promo(service, op, code="USEDUP", max_redemptions=1)
    redeem(system, alice, "USEDUP")
    expired = make_promo(service, op, code="EXPIRED")
    owner.execute("UPDATE grant_codes SET expires_at = %s WHERE code = 'EXPIRED'", (NOW - DAY,))
    revoked = make_promo(service, op, code="REVOKED")
    service.revoke_grant_code(op, revoked.grant_code.id, "x")

    def ids(show):
        page = service.list_grant_codes(limit=25, offset=0, show=show)
        assert page.total == len(page.grant_codes)
        return {g.id for g in page.grant_codes}

    assert ids("works") == {works.grant_code.id}
    assert ids("ended") == {used.grant_code.id, expired.grant_code.id}
    assert ids("revoked") == {revoked.grant_code.id}
    assert ids(None) == {works.grant_code.id, used.grant_code.id, expired.grant_code.id, revoked.grant_code.id}
    statuses = {g.code: g.status for g in service.list_grant_codes(limit=25, offset=0).grant_codes}
    assert statuses == {"WORKS": "works", "USEDUP": "used_up", "EXPIRED": "expired", "REVOKED": "revoked"}


def test_the_list_filters_agree_with_the_status_words_at_the_last_second(owner, system):
    """The SQL filter and `GrantRow.status` are two spellings of one rule: at the instant a code ends, both say it ended."""
    plans.create_grant(owner, days=7, code="EDGE", expires_in=timedelta(days=1), now=NOW)
    with system.system_session("admin") as conn:
        at_end = NOW + DAY
        [row], _ = admin_codes.list_grant_codes(conn, show="ended", now=at_end)
        assert row.status(at_end) == "expired"
        assert admin_codes.list_grant_codes(conn, show="works", now=at_end) == ([], 0)
        just_before = at_end - timedelta(microseconds=1)
        [row], _ = admin_codes.list_grant_codes(conn, show="works", now=just_before)
        assert row.status(just_before) == "works"


def test_the_overview_counts_people_whose_free_days_end_this_week(service, system, owner, op, alice, bob):
    make_promo(service, op, code="SHORT", days=5)
    make_promo(service, op, code="LONG", days=30)
    redeem(system, alice, "SHORT")  # ends in 5 days: counted
    redeem(system, bob, "LONG")  # ends in 30: not
    assert service.overview().grants_ending_7d == 1
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'friend', 'cli:owner')",
        (alice,),
    )
    assert service.overview().grants_ending_7d == 0  # an override keeps her on Pro: she is not about to lose anything


def test_the_overview_does_not_count_someone_whose_later_grant_runs_on(service, system, owner, op, bob):
    make_promo(service, op, code="SHORT", days=5)
    redeem(system, bob, "SHORT")  # ends in 5 days
    later = plans.create_grant(owner, days=30, label="Beta", now=NOW)[0]
    owner.execute(
        "INSERT INTO grant_redemptions (grant_code_id, user_id, plan, starts_at, ends_at) VALUES (%s, %s, 'pro', %s, %s)",
        (later, bob, NOW + 5 * DAY, NOW + 35 * DAY),
    )  # a second grant that carries on after the first (an invite's, say)
    assert service.overview().grants_ending_7d == 0


# ---- signing up with one ------------------------------------------------------------------------------------------------


def test_a_newcomer_who_signs_up_with_a_promo_code_starts_with_its_days(world, service, owner, op):
    make_promo(service, op, code="FREE30")
    invite = invites.create_invite(owner)
    browser = world()
    response = browser.sign_in(NEWCOMER, invite=invite, promo="free 30", next="/trades")
    assert response.status_code == 303 and response.headers["location"] == "/trades" and browser.token
    plan = browser.session().json()["plan"]
    assert (plan["effective"], plan["source"]) == ("pro", "grant")
    redeemed = [r for r in audit_rows(owner) if r["action"] == "grant_redeemed"]
    assert [r["meta"] for r in redeemed] == [{"plan": "pro", "days": 30, "via": "signup"}]


def test_a_promo_code_that_would_not_work_is_caught_before_the_provider_and_both_boxes_come_back(world, owner):
    invite = invites.create_invite(owner)
    browser = world()
    refused = browser.client.get("/auth/login", params={"invite": invite, "promo": "nope99"})
    assert refused.status_code == 302 and not browser.idp.attempts
    target = urlsplit(refused.headers["location"])
    assert target.path == "/invite"
    assert parse_qs(target.query) == {"error": ["promo_invalid"], "code": [invite], "promo": ["nope99"]}
    assert users_with_subject(owner, "kp_newcomer") == []
    [row] = [r for r in audit_rows(owner) if r["action"] == "login_failed"]
    assert row["meta"] == {"reason": "promo_precheck"} and "NOPE" not in str(row).upper()


def test_a_bad_invite_is_still_the_invite_error_even_with_a_good_promo(world, service, op):
    make_promo(service, op, code="FREE30")
    refused = world().client.get("/auth/login", params={"invite": "TJ-ZZZZ-ZZZZ", "promo": "FREE30"})
    assert refused.headers["location"] == "/invite?error=invite_invalid"


def test_a_promo_whose_last_use_goes_meanwhile_still_makes_the_account_and_says_so_once(
    world, service, system, owner, op, bob
):
    make_promo(service, op, code="LASTONE", max_redemptions=1)
    invite = invites.create_invite(owner)
    browser = world()
    url, state = browser.start(invite=invite, promo="LASTONE")  # checked: one use left
    with system.system_session("login") as conn:  # someone else takes it while she is at the provider
        assert isinstance(plans.redeem(conn, bob, "LASTONE"), plans.Redeemed)
    code = browser.idp.sign_in(url, NEWCOMER)
    response = browser.come_back(code=code, state=state)
    assert response.status_code == 303 and browser.token  # the account is made all the same
    assert response.headers["location"] == "/settings/plan?promo=not_applied"
    assert browser.session().json()["plan"]["source"] == "free"
    refusals = [r["meta"] for r in audit_rows(owner) if r["action"] == "grant_refused"]
    assert refusals == [{"reason": "refused", "via": "signup"}]
    assert "LASTONE" not in str(
        [r for r in audit_rows(owner) if r["actor"] == "user"]
    )  # only the operator's own row names it


def test_an_invite_that_brings_its_own_days_wins_and_the_promo_waits(world, service, owner, op):
    beta_id, _ = plans.create_grant(owner, days=30, label="Beta", now=NOW)
    invite = invites.create_invite(owner, grant_code_id=beta_id)
    make_promo(service, op, code="FREE7", days=7)
    browser = world()
    response = browser.sign_in(NEWCOMER, invite=invite, promo="FREE7")
    assert response.headers["location"] == "/settings/plan?promo=not_applied"
    assert [r["meta"].get("via") for r in audit_rows(owner) if r["action"] == "grant_redeemed"] == ["invite"]
    assert [r["meta"] for r in audit_rows(owner) if r["action"] == "grant_refused"] == [
        {"reason": "active", "via": "signup"}
    ]
    # The promo was not spent, so it can be used in Settings once the beta days end.
    assert owner.execute("SELECT count(*) AS n FROM grant_redemptions").fetchone()["n"] == 1


def test_a_returning_person_who_brings_a_promo_is_signed_in_and_nothing_is_redeemed(world, service, owner, op, alice):
    from auth_world import ALICE

    make_promo(service, op, code="FREE30")
    response = world().sign_in(ALICE, promo="FREE30")
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert owner.execute("SELECT count(*) AS n FROM grant_redemptions").fetchone()["n"] == 0
