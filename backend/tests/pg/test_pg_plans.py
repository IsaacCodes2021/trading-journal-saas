"""Plans and plan codes on a real Postgres (migration 0010): the effective plan, redeeming, bundled grants, the owner's CLI."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from auth_world import NEWCOMER, audit_rows
from test_pg_tenancy import SYSTEM_TABLES

from tradejournal.api.pg_services import PgAccountService
from tradejournal.pg import invites, plans

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def login(system):
    return system.system_session("login")


def code_for(owner, **kwargs):
    """A plan code made as the owner: (its id, the code as it would be sent)."""
    kwargs.setdefault("days", 90)
    kwargs.setdefault("now", NOW)  # a code that expires does so relative to the test's clock, not the wall clock
    return plans.create_grant(owner, **kwargs)


def redeem(system, user, code, now=NOW):
    with login(system) as conn:
        return plans.redeem(conn, user, code, now=now)


def effective(system, user, now=NOW):
    with login(system) as conn:
        return plans.effective(conn, user, now)


def count(owner, table):
    return owner.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608 (fixed table names)


# ---- the effective plan from the rows -------------------------------------------------------------------------------


def test_a_person_with_nothing_is_on_the_free_plan(system, alice):
    plan = effective(system, alice)
    assert (plan.plan, plan.source, plan.expires_at) == ("free", "free", None)


def test_redeeming_a_code_gives_its_plan_from_now_for_its_days(system, owner, alice):
    _, code = code_for(owner, days=90)
    result = redeem(system, alice, code)
    assert result == plans.Redeemed("pro", 90, NOW + 90 * DAY)
    plan = effective(system, alice)
    assert (plan.plan, plan.source, plan.expires_at) == ("pro", "grant", NOW + 90 * DAY)
    row = owner.execute("SELECT starts_at, ends_at, plan, user_id FROM grant_redemptions").fetchone()
    assert (row["starts_at"], row["ends_at"], row["plan"], row["user_id"]) == (NOW, NOW + 90 * DAY, "pro", alice)


def test_the_plan_ends_when_the_days_are_up_and_not_a_moment_later(system, owner, alice):
    _, code = code_for(owner, days=30)
    redeem(system, alice, code)
    assert effective(system, alice, NOW + 30 * DAY - timedelta(seconds=1)).source == "grant"
    assert effective(system, alice, NOW + 30 * DAY).source == "free"


def test_someone_elses_grant_is_not_yours(system, owner, alice, bob):
    _, code = code_for(owner)
    redeem(system, alice, code)
    assert effective(system, bob).source == "free"


def test_an_override_wins_even_when_it_lowers_the_plan(system, owner, alice):
    _, code = code_for(owner)
    redeem(system, alice, code)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'free', 'chargeback', 'cli:owner')",
        (alice,),
    )
    plan = effective(system, alice)
    assert (plan.plan, plan.source) == ("free", "override")


def test_an_override_can_raise_the_plan_and_ends_when_it_expires(system, owner, alice):
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, expires_at, reason, created_by) VALUES (%s, 'pro', %s, 'press', 'cli:owner')",
        (alice, NOW + 5 * DAY),
    )
    assert (effective(system, alice).plan, effective(system, alice).source) == ("pro", "override")
    assert effective(system, alice, NOW + 5 * DAY).source == "free"


def test_a_revoked_override_is_ignored_and_a_new_one_may_follow_it(system, owner, alice):
    first = owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'trial', 'cli:owner') RETURNING id",
        (alice,),
    ).fetchone()["id"]
    owner.execute("UPDATE plan_overrides SET revoked_at = %s, revoked_by = 'cli:owner' WHERE id = %s", (NOW, first))
    assert effective(system, alice).source == "free"
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'again', 'cli:owner')",
        (alice,),
    )
    assert effective(system, alice).source == "override"
    assert count(owner, "plan_overrides") == 2  # the history is kept


def test_only_one_override_can_be_active_for_a_person_and_it_needs_a_reason(owner, alice, bob):
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'a', 'x')", (alice,)
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner.execute(
            "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'free', 'b', 'x')", (alice,)
        )
    for reason in ("", "   ", "\t\n", "   ", " 　", "​", "\f\v\r"):  # nothing, spaces, tab and newline, Unicode spaces
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(
                "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', %s, 'x')",
                (bob, reason),
            )


# ---- redeeming: one answer for every failure ----------------------------------------------------------------------------


def test_a_code_is_tidied_the_way_the_page_and_invites_do(system, owner, alice):
    _, code = code_for(owner)
    assert isinstance(redeem(system, alice, "  " + code.lower().replace("-", " ") + " "), plans.Redeemed)


@pytest.mark.parametrize(
    "junk", [None, "", "   ", "x", "TJ", "GR-", "GR-ABCD-EFGH-" + "X" * 40, "'; DROP TABLE users; --", "GR-ABCD-EFGH"]
)
def test_a_code_that_is_not_one_is_refused_and_nothing_is_written(system, owner, alice, junk):
    assert redeem(system, alice, junk) == plans.Refused()
    assert count(owner, "grant_redemptions") == 0


def test_a_revoked_code_a_code_that_expired_and_one_that_is_used_up_all_give_the_same_answer(system, owner, alice, bob):
    revoked_id, revoked = code_for(owner)
    owner.execute("UPDATE grant_codes SET revoked_at = %s WHERE id = %s", (NOW - DAY, revoked_id))
    _, expired = code_for(owner, expires_in=timedelta(days=1))
    _, once = code_for(owner, max_redemptions=1)
    redeem(system, bob, once)  # the one use
    answers = [
        redeem(system, alice, revoked),
        redeem(system, alice, expired, NOW + 2 * DAY),
        redeem(system, alice, once),
    ]
    assert answers == [plans.Refused()] * 3
    assert count(owner, "grant_redemptions") == 1  # only bob's


def test_a_code_works_right_up_to_its_expiry(system, owner, alice, bob):
    _, code = code_for(owner, expires_in=timedelta(days=1))
    created = owner.execute("SELECT expires_at FROM grant_codes").fetchone()["expires_at"]
    assert redeem(system, alice, code, created - timedelta(seconds=1)).__class__ is plans.Redeemed
    assert redeem(system, bob, code, created) == plans.Refused()


def test_the_same_person_cannot_use_the_same_code_twice(system, owner, alice):
    _, code = code_for(owner, days=10)
    redeem(system, alice, code)
    assert redeem(system, alice, code, NOW + 11 * DAY) == plans.Refused()  # even after the first period has ended
    assert count(owner, "grant_redemptions") == 1


def test_a_code_may_be_used_by_many_people_up_to_its_limit(system, owner, alice, bob):
    _, code = code_for(owner, max_redemptions=2)
    carol = owner.execute(
        "INSERT INTO users (idp_subject, email) VALUES ('c', 'c@example.test') RETURNING id"
    ).fetchone()["id"]
    assert [redeem(system, u, code).__class__ for u in (alice, bob, carol)] == [
        plans.Redeemed,
        plans.Redeemed,
        plans.Refused,
    ]


def test_a_code_with_no_limit_may_be_used_by_everyone(system, owner, alice, bob):
    _, code = code_for(owner)
    assert [redeem(system, u, code).__class__ for u in (alice, bob)] == [plans.Redeemed, plans.Redeemed]


def test_a_person_with_a_plan_from_a_code_cannot_take_another_until_it_ends(system, owner, alice):
    _, first = code_for(owner, days=30)
    _, second = code_for(owner, days=60)
    redeem(system, alice, first)
    assert redeem(system, alice, second, NOW + 5 * DAY) == plans.AlreadyHave(NOW + 30 * DAY)
    assert count(owner, "grant_redemptions") == 1
    assert redeem(system, alice, second, NOW + 30 * DAY) == plans.Redeemed(
        "pro", 60, NOW + 90 * DAY
    )  # once it has ended


def test_a_bad_code_is_refused_even_to_someone_who_already_has_a_plan_so_it_cannot_be_probed(system, owner, alice):
    _, first = code_for(owner)
    redeem(system, alice, first)
    assert redeem(system, alice, "GR-AAAA-BBBB", NOW + DAY) == plans.Refused()  # not "you already have one"


def test_two_people_cannot_both_take_the_last_use_even_at_the_same_moment(db, system, owner, alice, bob):
    _, code = code_for(owner, max_redemptions=1)
    results = {}
    holding = threading.Event()

    def take(name, user, hold):
        with login(system) as conn:
            results[name] = plans.redeem(conn, user, code, now=NOW)
            if hold:
                holding.set()
                time.sleep(0.4)  # the first keeps its transaction open, so the second starts while it is uncommitted

    first = threading.Thread(target=take, args=("first", alice, True))
    second = threading.Thread(target=take, args=("second", bob, False))
    first.start()
    assert holding.wait(5)
    second.start()
    first.join()
    second.join()
    assert isinstance(results["first"], plans.Redeemed) and results["second"] == plans.Refused()
    assert count(owner, "grant_redemptions") == 1


def test_two_sign_ups_cannot_both_take_the_last_use_of_a_grant_an_invite_carries(system, owner, alice, bob):
    grant_id, _ = code_for(owner, max_redemptions=1)
    results = {}
    holding = threading.Event()

    def sign_up(name, user, hold):
        with login(system) as conn:
            results[name] = plans.give_bundled(conn, grant_id, user, now=NOW)
            if hold:
                holding.set()
                time.sleep(0.4)  # the first sign-up has not committed yet when the second looks

    first = threading.Thread(target=sign_up, args=("first", alice, True))
    second = threading.Thread(target=sign_up, args=("second", bob, False))
    first.start()
    assert holding.wait(5)
    second.start()
    first.join()
    second.join()
    assert isinstance(results["first"], plans.Redeemed) and results["second"] is None
    assert count(owner, "grant_redemptions") == 1


# ---- bundled with an invite -----------------------------------------------------------------------------------------


def test_a_grant_carried_by_an_invite_is_given_inside_the_sign_up(system, owner, alice):
    grant_id, _ = code_for(owner, days=90)
    with login(system) as conn:
        given = plans.give_bundled(conn, grant_id, alice, now=NOW)
    assert given == plans.Redeemed("pro", 90, NOW + 90 * DAY)
    assert effective(system, alice).source == "grant"


@pytest.mark.parametrize("how", ["revoked", "expired", "used_up", "again"])
def test_a_bundled_grant_that_cannot_be_given_gives_nothing_and_does_not_fail(system, owner, alice, bob, how):
    grant_id, code = code_for(owner, max_redemptions=1, expires_in=timedelta(days=1))
    if how == "revoked":
        owner.execute("UPDATE grant_codes SET revoked_at = %s WHERE id = %s", (NOW, grant_id))
    if how == "used_up":
        redeem(system, bob, code)
    if how == "again":
        redeem(system, alice, code)
    moment = NOW + 3 * DAY if how == "expired" else NOW
    with login(system) as conn:
        assert plans.give_bundled(conn, grant_id, alice, now=moment) is None


# ---- who may do what ----------------------------------------------------------------------------------------------------


def test_the_new_tables_are_account_tables_not_journal_tables_and_the_lint_knows_it():
    assert {"grant_redemptions", "plan_overrides"} <= set(SYSTEM_TABLES)


@pytest.mark.parametrize("table", ["grant_codes", "grant_redemptions", "plan_overrides"])
def test_the_request_role_has_no_access_at_all(db, alice, table):
    with db.tenant_session(alice) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(f"SELECT * FROM {table}")  # noqa: S608 (fixed table names)


@pytest.mark.parametrize("profile", ["webhook", "sweep", "admin"])
@pytest.mark.parametrize("table", ["grant_codes", "grant_redemptions", "plan_overrides"])
def test_the_other_system_roles_cannot_read_them_yet(system, profile, table):
    with system.system_session(profile) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(f"SELECT * FROM {table}")  # noqa: S608 (fixed table names)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO grant_codes (code_hash, plan, days) VALUES (decode(repeat('22', 32), 'hex'), 'pro', 3650)",
        "UPDATE grant_codes SET days = 3650, max_redemptions = NULL, revoked_at = NULL",
        "DELETE FROM grant_codes",
        "UPDATE grant_redemptions SET ends_at = ends_at + interval '10 years'",
        "DELETE FROM grant_redemptions",
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (gen_random_uuid(), 'pro', 'x', 'y')",
        "UPDATE plan_overrides SET plan = 'pro'",
        "DELETE FROM plan_overrides",
        "UPDATE invites SET grant_code_id = 1",
    ],
)
def test_the_sign_in_role_can_read_plans_and_record_a_redemption_but_not_make_or_change_one(
    system, owner, alice, statement
):
    code_for(owner)
    with login(system) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)


def test_the_sign_in_role_cannot_see_an_overrides_reason_or_who_made_it(system, owner, alice):
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'secret', 'x')", (alice,)
    )
    for column in ("reason", "created_by", "revoke_reason"):
        with login(system) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SELECT {column} FROM plan_overrides")  # noqa: S608 (fixed column names)
    with login(system) as conn:
        assert conn.execute("SELECT plan FROM plan_overrides").fetchone()["plan"] == "pro"


def test_the_checks_on_a_code_hold_in_the_database_too(owner):
    base = "INSERT INTO grant_codes (code_hash, plan, days, max_redemptions) VALUES (decode(repeat('33', 32), 'hex'), %s, %s, %s)"
    for plan, days, limit in (
        ("free", 30, None),
        ("platinum", 30, None),
        ("pro", 0, None),
        ("pro", 3651, None),
        ("pro", 30, 0),
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(base, (plan, days, limit))
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            "INSERT INTO grant_codes (code_hash, plan, days) VALUES (decode('abcd', 'hex'), 'pro', 30)"
        )  # not a sha-256


def test_deleting_an_account_takes_its_plan_rows_and_leaves_the_code_and_everyone_elses(system, owner, alice, bob):
    _, code = code_for(owner)
    redeem(system, alice, code)
    redeem(system, bob, code)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'x', 'y')", (alice,)
    )
    owner.execute("DELETE FROM users WHERE id = %s", (alice,))
    assert owner.execute("SELECT user_id FROM grant_redemptions").fetchall() == [{"user_id": bob}]
    assert count(owner, "plan_overrides") == 0 and count(owner, "grant_codes") == 1


# ---- creating codes (the owner's CLI) --------------------------------------------------------------------------------


def test_a_code_is_stored_hashed_and_looks_different_from_an_invite(owner):
    _, code = code_for(owner, label="Beta testers")
    assert code.startswith("GR-") and len(code) == 12 and not code.startswith("TJ-")
    row = owner.execute("SELECT * FROM grant_codes").fetchone()
    assert bytes(row["code_hash"]) == invites.hash_code(code)
    assert code not in str(row) and row["label"] == "Beta testers" and row["created_by"] == "cli:owner"
    assert plans.generate_code() != plans.generate_code()


@pytest.mark.parametrize("kwargs", [{"days": 0}, {"days": 3651}, {"days": 30, "max_redemptions": 0}])
def test_a_code_that_makes_no_sense_is_not_made(owner, kwargs):
    with pytest.raises(ValueError):
        plans.create_grant(owner, **kwargs)
    assert count(owner, "grant_codes") == 0


def test_the_command_makes_lists_and_revokes_codes_and_needs_the_owners_connection(owner_url, monkeypatch, capsys):
    monkeypatch.delenv("TJ_OWNER_URL", raising=False)
    assert plans.main(["list-grants"]) == 2
    monkeypatch.setenv("TJ_OWNER_URL", owner_url)
    with psycopg.connect(owner_url, autocommit=True) as conn:
        conn.execute("TRUNCATE grant_codes RESTART IDENTITY CASCADE")
    capsys.readouterr()
    assert (
        plans.main(
            ["create-grant", "--label", "Testers", "--days", "90", "--max-redemptions", "5", "--expires-days", "30"]
        )
        == 0
    )
    printed = capsys.readouterr().out
    code = next(word for word in printed.replace(":", " ").split() if word.startswith("GR-"))
    assert "#1" in printed and "90 days of pro" in printed and "shown once" in printed
    assert plans.main(["list-grants"]) == 0
    listing = capsys.readouterr().out
    assert (
        "#1 Testers: 90 days of pro, used 0 (up to 5), works" in listing and code not in listing
    )  # the code is never listed
    assert plans.main(["revoke-grant", "--id", "1"]) == 0 and "revoked" in capsys.readouterr().out
    assert plans.main(["revoke-grant", "--id", "1"]) == 0 and "already" in capsys.readouterr().out
    assert plans.main(["list-grants"]) == 0 and "revoked" in capsys.readouterr().out.splitlines()[0]


def test_the_invite_command_can_attach_a_plan_code(owner_url, monkeypatch, owner, capsys):
    grant_id, _ = code_for(owner)
    monkeypatch.setenv("TJ_OWNER_URL", owner_url)
    assert invites.main(["create", "--grant-id", str(grant_id)]) == 0
    assert owner.execute("SELECT grant_code_id FROM invites").fetchone()["grant_code_id"] == grant_id


# ---- through the account service and the real app -------------------------------------------------------------------


@pytest.fixture
def service(db, system):
    return PgAccountService(db, system, clock=lambda: NOW)


def test_the_account_reports_the_effective_plan_and_its_limits(service, system, owner, alice):
    assert (service.load(alice).plan.effective, service.load(alice).plan.source) == ("free", "free")
    _, code = code_for(owner, days=90)
    redeem(system, alice, code)
    account = service.load(alice)
    assert (account.plan.effective, account.plan.source, account.plan.expires_at) == ("pro", "grant", NOW + 90 * DAY)
    assert account.entitlements == {}  # what a plan includes is not decided yet: nothing is limited


def test_what_a_plan_includes_reaches_the_account_with_the_plan(service, system, owner, alice, monkeypatch):
    """Nothing is limited yet, so the real limits are empty; this proves the path for when the first one is decided."""
    from tradejournal import entitlements

    monkeypatch.setitem(entitlements.LIMITS, "free", {"max_active_accounts": 2})
    monkeypatch.setitem(entitlements.LIMITS, "pro", {"max_active_accounts": 10, "can_export": True})
    assert service.load(alice).entitlements == {"max_active_accounts": 2}
    redeem(system, alice, code_for(owner)[1])
    assert service.load(alice).entitlements == {"max_active_accounts": 10, "can_export": True}


def test_redeeming_through_the_service_records_what_happened_and_never_the_code(service, owner, alice):
    _, code = code_for(owner, days=45)
    result = service.redeem_grant(alice, code, ip_hash="hash-1")
    assert result.status == "ok" and (result.plan.effective, result.plan.source) == ("pro", "grant")
    assert service.redeem_grant(alice, "GR-NOPE-NOPE", ip_hash="hash-2").status == "refused"
    other = code_for(owner)[1]
    assert service.redeem_grant(alice, other).status == "active"
    log = audit_rows(owner)
    assert [(r["action"], r["meta"]) for r in log] == [
        ("grant_redeemed", {"plan": "pro", "days": 45, "via": "code"}),
        ("grant_refused", {"reason": "refused"}),
        ("grant_refused", {"reason": "active"}),
    ]
    assert all(r["user_id"] == alice for r in log)
    assert code not in str(log) and other not in str(log) and "NOPE" not in str(log)


def test_a_refused_redemption_changes_nothing(service, owner, alice):
    assert service.redeem_grant(alice, "GR-NOPE-NOPE").status == "refused"
    assert count(owner, "grant_redemptions") == 0 and service.load(alice).plan.source == "free"


def test_redeeming_on_the_real_app_changes_the_session_and_needs_the_csrf_token(world, owner, alice):
    from auth_world import ALICE

    _, code = code_for(owner, days=90)
    browser = world()
    assert browser.sign_in(ALICE).status_code == 303
    assert browser.session().json()["plan"]["source"] == "free"
    token = browser.session().json()["csrf_token"]
    path = "/api/v1/grants/redeem"
    sent = {"sec-fetch-site": "same-origin"}
    assert browser.client.post(path, json={"code": code}, headers=sent).status_code == 403  # no CSRF token
    assert count(owner, "grant_redemptions") == 0
    response = browser.client.post(path, json={"code": code}, headers={**sent, "x-csrf-token": token})
    assert (
        response.status_code == 200 and response.json()["source"] == "grant" and response.json()["effective"] == "pro"
    )
    assert browser.session().json()["plan"]["source"] == "grant"
    again = browser.client.post(path, json={"code": code}, headers={**sent, "x-csrf-token": token})
    assert again.status_code == 409 or again.status_code == 422  # the same code twice, or a plan already running


def test_a_second_code_while_one_is_running_says_so_on_the_real_app(world, owner, alice):
    from auth_world import ALICE

    _, first = code_for(owner, days=90)
    _, second = code_for(owner, days=30)
    browser = world()
    browser.sign_in(ALICE)
    headers = {"sec-fetch-site": "same-origin", "x-csrf-token": browser.session().json()["csrf_token"]}
    assert browser.client.post("/api/v1/grants/redeem", json={"code": first}, headers=headers).status_code == 200
    response = browser.client.post("/api/v1/grants/redeem", json={"code": second}, headers=headers)
    assert response.status_code == 409 and "You already have a plan from a code, until" in response.json()["detail"]


def test_a_stranger_cannot_redeem_anything_on_the_real_app(world, owner):
    _, code = code_for(owner)
    stranger = world()
    response = stranger.client.post(
        "/api/v1/grants/redeem", json={"code": code}, headers={"sec-fetch-site": "same-origin"}
    )
    assert response.status_code == 401 and count(owner, "grant_redemptions") == 0


# ---- signing up with an invite that carries a plan --------------------------------------------------------------------


def test_signing_up_with_an_invite_that_carries_a_plan_starts_on_that_plan(world, owner):
    grant_id, _ = code_for(owner, days=90)
    invite = invites.create_invite(owner, grant_code_id=grant_id)
    browser = world()
    assert browser.sign_in(NEWCOMER, invite=invite).status_code == 303
    plan = browser.session().json()["plan"]
    assert (plan["effective"], plan["source"]) == ("pro", "grant")
    assert [(r["action"], r["meta"].get("via")) for r in audit_rows(owner) if r["action"] == "grant_redeemed"] == [
        ("grant_redeemed", "invite")
    ]


def test_an_invite_without_a_plan_gives_the_free_plan(world, owner):
    invite = invites.create_invite(owner)
    browser = world()
    browser.sign_in(NEWCOMER, invite=invite)
    assert browser.session().json()["plan"]["source"] == "free"
    assert count(owner, "grant_redemptions") == 0


def test_an_invite_whose_plan_has_run_out_still_signs_the_person_up_without_it(world, owner):
    grant_id, _ = code_for(owner)
    owner.execute("UPDATE grant_codes SET revoked_at = now() WHERE id = %s", (grant_id,))
    invite = invites.create_invite(owner, grant_code_id=grant_id)
    browser = world()
    assert browser.sign_in(NEWCOMER, invite=invite).status_code == 303
    assert browser.session().json()["plan"]["source"] == "free"
    assert not [r for r in audit_rows(owner) if r["action"] == "grant_redeemed"]
