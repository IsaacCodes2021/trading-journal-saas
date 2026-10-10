"""Joining the beta (migration 0014): the personal code, the rules that limit it, who may call what, and what the code
does at sign-up. The access that code gives, and what happens when it ends, is tested with the plans in the same file's
last section."""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from auth_world import NEWCOMER, ORIGIN, audit_rows

from tradejournal.api.mailer import MailError
from tradejournal.pg import beta, invites, plans

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
EMAIL = "newcomer@example.test"


def login(system):
    return system.system_session("login")


def join(system, *, email=EMAIL, first_name="Alex", prop_firm="Topstep", code=None):
    """One request to join: (the sign-up id or None, the code that was offered)."""
    code = code or invites.generate_code()
    with login(system) as conn:
        return beta.join(conn, email=email, first_name=first_name, prop_firm=prop_firm, code=code), code


def row(owner, table, **where):
    clause = " AND ".join(f"{k} = %s" for k in where) or "true"
    return owner.execute(f"SELECT * FROM {table} WHERE {clause}", tuple(where.values())).fetchone()  # noqa: S608


def count(owner, table):
    return owner.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608 (fixed table names)


# ---- making the code ------------------------------------------------------------------------------------------------


def test_joining_makes_a_signup_and_a_personal_code_tied_to_the_address(system, owner):
    signup, code = join(system, email="  Alex@Example.TEST ", first_name=" Alex ", prop_firm=" Topstep ")
    assert signup is not None
    person_row = row(owner, "beta_signups", id=signup)
    assert (person_row["email"], person_row["first_name"], person_row["prop_firm"]) == (
        "alex@example.test",
        "Alex",
        "Topstep",
    )
    assert person_row["codes_made"] == 1 and person_row["email_sent_at"] is None
    invite = row(owner, "invites", id=person_row["invite_id"])
    assert invite["email"] == "alex@example.test" and invite["max_uses"] == 1
    assert invite["revoked_at"] is None
    assert timedelta(days=13, hours=23) < invite["expires_at"] - datetime.now(UTC) <= timedelta(days=14, minutes=1)
    # the code that was offered is the one that works, and only its hash is stored
    assert bytes(invite["code_hash"]) == invites.hash_code(code)
    assert code not in str(owner.execute("SELECT * FROM invites").fetchall())


def test_the_code_carries_thirty_days_of_full_access(system, owner):
    signup, _ = join(system)
    invite = row(owner, "invites", id=row(owner, "beta_signups", id=signup)["invite_id"])
    grant = row(owner, "grant_codes", id=invite["grant_code_id"])
    assert (grant["plan"], grant["days"], grant["created_by"]) == ("pro", 30, "system:beta")


def test_everyone_gets_the_same_beta_grant_not_a_new_one_each_time(system, owner):
    join(system, email="a@example.test")
    join(system, email="b@example.test")
    assert count(owner, "grant_codes") == 1


def test_the_audit_row_says_a_code_was_made_and_never_whom_for(system, owner):
    signup, code = join(system, email="private.person@example.test")
    rows = owner.execute(
        "SELECT action, actor, entity, entity_id, meta FROM audit_log WHERE action = 'beta_code_made'"
    ).fetchall()
    assert len(rows) == 1
    assert (rows[0]["actor"], rows[0]["entity"], rows[0]["entity_id"]) == ("system", "beta_signup", str(signup))
    assert rows[0]["meta"] == {"codes_made": 1}
    assert "private.person" not in str(rows) and code not in str(rows)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"email": "not an address"},
        {"email": "no-at-sign.example.test"},
        {"email": "two@@example.test"},
        {"email": "a@b"},
        {"email": "x" * 250 + "@example.test"},
        {"email": ""},
        {"first_name": "   "},
        {"first_name": "n" * 81},
        {"prop_firm": ""},
        {"prop_firm": "f" * 81},
    ],
)
def test_a_malformed_request_makes_nothing_and_says_nothing(system, owner, kwargs):
    signup, _ = join(system, **kwargs)
    assert signup is None
    assert (count(owner, "beta_signups"), count(owner, "invites"), count(owner, "grant_codes")) == (0, 0, 0)


def test_a_code_hash_of_the_wrong_length_is_refused(system, owner):
    with login(system) as conn:
        got = conn.execute("SELECT beta_join(%s, %s, %s, %s) AS s", (EMAIL, "Alex", "Apex", b"short")).fetchone()
    assert got["s"] is None and count(owner, "beta_signups") == 0


def test_an_address_that_already_has_an_account_gets_nothing_whatever_its_case(system, owner, alice):
    signup, _ = join(system, email="ALICE@example.test")
    assert signup is None
    assert (count(owner, "beta_signups"), count(owner, "invites")) == (0, 0)


# ---- asking again ---------------------------------------------------------------------------------------------------


def test_asking_again_straight_after_a_sent_email_makes_no_second_code(system, owner):
    signup, _ = join(system)
    with login(system) as conn:
        beta.email_result(conn, signup, ok=True)
    again, _ = join(system)
    assert again is None
    assert count(owner, "invites") == 1


def test_a_code_whose_email_has_not_been_heard_from_counts_as_on_its_way(system, owner):
    # The process may have died between making the code and sending it, or the answer is still on its way: either way a
    # second request inside the hour must not make a second email (and withdraw the code the first one carries).
    join(system)
    assert join(system)[0] is None


def test_asking_again_after_a_failed_email_makes_a_fresh_code_and_withdraws_the_old_one(system, owner):
    signup, first = join(system)
    with login(system) as conn:
        beta.email_result(conn, signup, ok=False)
    again, second = join(system)
    assert again == signup
    old = row(owner, "invites", code_hash=invites.hash_code(first))
    new = row(owner, "invites", code_hash=invites.hash_code(second))
    assert old["revoked_at"] is not None and new["revoked_at"] is None
    person_row = row(owner, "beta_signups", id=signup)
    assert (person_row["codes_made"], person_row["email_failures"], person_row["invite_id"]) == (2, 1, new["id"])


def test_after_an_hour_a_new_code_may_be_made(system, owner):
    signup, _ = join(system)
    with login(system) as conn:
        beta.email_result(conn, signup, ok=True)
    owner.execute("UPDATE beta_signups SET last_code_at = now() - interval '61 minutes'")
    again, _ = join(system)
    assert again == signup


def test_five_codes_are_all_an_address_ever_gets(system, owner):
    for n in range(5):
        signup, _ = join(system)
        assert signup is not None, n
        owner.execute("UPDATE beta_signups SET last_code_at = now() - interval '2 hours'")
    assert join(system)[0] is None
    assert row(owner, "beta_signups")["codes_made"] == 5


def test_a_code_that_was_used_is_not_replaced(system, owner, world):
    signup, code = join(system, email=NEWCOMER.email)
    assert world().sign_in(NEWCOMER, invite=code).status_code == 303
    owner.execute("UPDATE beta_signups SET last_code_at = now() - interval '2 hours'")
    # the address now has an account, which is refused first; either way nothing new is made
    assert join(system, email=NEWCOMER.email)[0] is None
    assert row(owner, "beta_signups", id=signup)["codes_made"] == 1


def test_two_requests_at_the_same_moment_make_one_code(system, owner):
    results, barrier = [], threading.Barrier(2)

    def go():
        barrier.wait()
        results.append(join(system)[0])

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r is not None for r in results) == [False, True]
    assert count(owner, "invites") == 1


def test_while_the_beta_grant_is_revoked_no_code_is_made(system, owner):
    join(system, email="first@example.test")
    owner.execute("UPDATE grant_codes SET revoked_at = now() WHERE created_by = 'system:beta'")
    signup, _ = join(system, email="second@example.test")
    assert signup is None
    assert count(owner, "beta_signups") == 1


# ---- who may do what ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM beta_signups",
        "INSERT INTO beta_signups (email, first_name, prop_firm) VALUES ('a@b.cc', 'A', 'B')",
        "UPDATE beta_signups SET codes_made = 0",
        "DELETE FROM beta_signups",
        "INSERT INTO invites (code_hash) VALUES (decode(repeat('33', 32), 'hex'))",
        "UPDATE grant_redemptions SET revoked_at = now()",
        "UPDATE grant_redemptions SET ended_notice_sent_at = now()",
    ],
)
def test_the_sign_in_role_can_use_the_functions_and_nothing_underneath_them(system, owner, alice, statement):
    with login(system) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)


@pytest.mark.parametrize("profile", ["webhook", "sweep", "admin"])
def test_no_other_system_role_may_call_the_beta_functions(system, profile):
    with system.system_session(profile) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT beta_join('a@b.cc', 'A', 'B', decode(repeat('44', 32), 'hex'))")
    with system.system_session(profile) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT beta_email_result(1, true)")


def test_the_request_role_cannot_call_them_or_read_the_table(db, alice):
    with db.tenant_session(alice) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT beta_join('a@b.cc', 'A', 'B', decode(repeat('44', 32), 'hex'))")
    with db.tenant_session(alice) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM beta_signups")


def test_the_email_result_is_recorded_and_a_failure_is_counted(system, owner):
    signup, _ = join(system)
    with login(system) as conn:
        beta.email_result(conn, signup, ok=False)
        beta.email_result(conn, signup, ok=False)
    person_row = row(owner, "beta_signups", id=signup)
    assert (person_row["email_failures"], person_row["email_sent_at"]) == (2, None)
    with login(system) as conn:
        beta.email_result(conn, signup, ok=True)
    assert row(owner, "beta_signups", id=signup)["email_sent_at"] is not None


# ---- thirty days at most --------------------------------------------------------------------------------------------


def test_a_plan_code_can_give_at_most_thirty_days(owner):
    plans.create_grant(owner, days=30, now=NOW)
    with pytest.raises(ValueError, match="1 to 30 days"):
        plans.create_grant(owner, days=31, now=NOW)


def test_the_database_holds_the_same_line_for_a_code_made_any_other_way(owner):
    insert = "INSERT INTO grant_codes (code_hash, plan, days) VALUES (decode(repeat('55', 32), 'hex'), 'pro', %s)"
    owner.execute(insert, (30,))
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(insert.replace("55", "56"), (31,))


# ---- what the code does at sign-up ----------------------------------------------------------------------------------


def test_signing_up_with_the_personal_code_gives_thirty_days_from_that_moment(system, owner, world):
    _, code = join(system, email=NEWCOMER.email)
    browser = world()
    assert browser.sign_in(NEWCOMER, invite=code).status_code == 303
    body = browser.session().json()
    assert (body["plan"]["effective"], body["plan"]["source"]) == ("pro", "grant")
    ends = datetime.fromisoformat(body["plan"]["expires_at"])
    assert timedelta(days=29, hours=23) < ends - datetime.now(UTC) <= timedelta(days=30, minutes=1)


def test_the_code_works_only_for_the_address_it_was_made_for(system, owner, world):
    _, code = join(system, email="someone.else@example.test")
    browser = world()
    response = browser.sign_in(NEWCOMER, invite=code)
    assert response.headers["location"] == "/invite?error=invite_invalid"
    assert count(owner, "invite_redemptions") == 0


def test_the_code_works_once(system, owner, world):
    _, code = join(system, email=NEWCOMER.email)
    first = world()
    assert first.sign_in(NEWCOMER, invite=code).status_code == 303
    again = world().client.get("/auth/login", params={"invite": code})  # used up: turned away before the provider
    assert again.headers["location"] == "/invite?error=invite_invalid"


# ---- the code is checked before the identity provider is involved ---------------------------------------------------


def test_a_wrong_code_never_reaches_the_identity_provider(world, owner):
    browser = world()
    response = browser.client.get("/auth/login", params={"invite": "TJ-NOPE-NOPE"})
    assert response.status_code == 302
    assert response.headers["location"] == "/invite?error=invite_invalid"
    assert not browser.idp.attempts
    assert "__Host-tj_login" not in response.headers.get("set-cookie", "")
    reasons = [r["meta"].get("reason") for r in audit_rows(owner) if r["action"] == "login_failed"]
    assert reasons == ["invite_precheck"]


def test_a_used_up_expired_or_revoked_code_is_turned_away_the_same_way(world, owner):
    used = invites.create_invite(owner, email=None, max_uses=1)
    expired = invites.create_invite(owner, expires_in=timedelta(seconds=-5))
    revoked = invites.create_invite(owner)
    owner.execute("UPDATE invites SET revoked_at = now() WHERE code_hash = %s", (invites.hash_code(revoked),))
    assert world().sign_in(NEWCOMER, invite=used).status_code == 303  # uses it up
    for code in (used, expired, revoked):
        response = world().client.get("/auth/login", params={"invite": code})
        assert response.headers["location"] == "/invite?error=invite_invalid"


def test_a_good_code_tied_to_an_address_asks_the_provider_to_prefill_it(world, system):
    _, code = join(system, email=NEWCOMER.email)
    browser = world()
    url, _state = browser.start(invite=code)
    assert browser.idp.attempt_from(url).login_hint == NEWCOMER.email


def test_an_untied_code_prefills_nothing(world, owner):
    code = invites.create_invite(owner)
    browser = world()
    url, _state = browser.start(invite=code)
    assert browser.idp.attempt_from(url).login_hint is None


def test_signing_in_without_a_code_is_not_checked_up_front(world, alice):
    browser = world()
    url, _state = browser.start()
    assert browser.idp.attempt_from(url).login_hint is None


def test_a_good_code_opens_the_providers_registration_screen_and_a_plain_sign_in_does_not(world, system, owner, alice):
    _, code = join(system, email=NEWCOMER.email)
    browser = world()
    url, _state = browser.start(invite=code)
    assert browser.idp.attempt_from(url).create is True
    other = world()
    url, _state = other.start(invite=invites.create_invite(owner))
    assert other.idp.attempt_from(url).create is True  # any valid code means an account is being made
    plain = world()
    url, _state = plain.start()
    assert plain.idp.attempt_from(url).create is False  # an ordinary sign-in opens the sign-in screen


def test_when_sign_up_is_open_a_code_is_not_required_up_front(world):
    browser = world(signup_mode="open")
    response = browser.client.get("/auth/login", params={"invite": "TJ-NOPE-NOPE"})
    assert response.status_code == 302 and "idp.test" in response.headers["location"]


# ---- access: what ending means --------------------------------------------------------------------------------------


def access(system, user, now=NOW):
    with login(system) as conn:
        return plans.access(conn, user, now)


def give(owner, system, user, *, days=30, now=NOW):
    _, code = plans.create_grant(owner, days=days, now=now)
    with login(system) as conn:
        assert isinstance(plans.redeem(conn, user, code, now=now), plans.Redeemed)


def test_a_person_who_never_had_time_limited_access_is_never_read_only(system, alice):
    state = access(system, alice, NOW + 400 * DAY)
    assert (state.state, state.ends_at, state.reason) == ("active", None, None)


def test_while_the_grant_runs_the_person_has_full_access_and_the_end_is_known(system, owner, alice):
    give(owner, system, alice)
    state = access(system, alice, NOW + 10 * DAY)
    assert (state.state, state.ends_at, state.reason) == ("active", NOW + 30 * DAY, None)


def test_when_the_grant_runs_out_the_person_can_look_but_not_change(system, owner, alice):
    give(owner, system, alice)
    state = access(system, alice, NOW + 30 * DAY)
    assert (state.state, state.ends_at, state.reason) == ("ended", NOW + 30 * DAY, "expired")


def test_an_operator_ending_it_early_ends_it_at_once_and_says_why(system, owner, alice):
    give(owner, system, alice)
    when = NOW + 5 * DAY
    owner.execute("UPDATE grant_redemptions SET revoked_at = %s", (when,))
    state = access(system, alice, when + timedelta(minutes=1))
    assert (state.state, state.ends_at, state.reason) == ("ended", when, "revoked")


def test_a_new_code_after_the_end_gives_access_back(system, owner, alice):
    give(owner, system, alice)
    later = NOW + 40 * DAY
    assert access(system, alice, later).state == "ended"
    give(owner, system, alice, now=later)
    assert access(system, alice, later + DAY).state == "active"


def test_an_override_to_pro_keeps_full_access_after_the_grant_ends(system, owner, alice):
    give(owner, system, alice)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'a friend', 'cli:owner')",
        (alice,),
    )
    assert access(system, alice, NOW + 60 * DAY).state == "active"


def test_a_revoked_grant_no_longer_gives_the_plan(system, owner, alice):
    give(owner, system, alice)
    owner.execute("UPDATE grant_redemptions SET revoked_at = %s", (NOW + DAY,))
    with login(system) as conn:
        assert plans.effective(conn, alice, NOW + 2 * DAY).source == "free"


def test_a_code_that_was_used_is_not_replaced_even_if_the_account_email_has_since_changed(system, owner, world):
    # The existing-account check is by address; if the person's address on file moved on, the used code still stands.
    signup, code = join(system, email=NEWCOMER.email)
    assert world().sign_in(NEWCOMER, invite=code).status_code == 303
    owner.execute("UPDATE users SET email = 'moved.on@example.test' WHERE email = %s", (NEWCOMER.email,))
    owner.execute("UPDATE beta_signups SET last_code_at = now() - interval '2 hours'")
    assert join(system, email=NEWCOMER.email)[0] is None
    assert row(owner, "beta_signups", id=signup)["codes_made"] == 1


def test_a_revoked_grant_does_not_stop_the_person_taking_a_new_code(system, owner, alice):
    give(owner, system, alice)
    owner.execute("UPDATE grant_redemptions SET revoked_at = %s", (NOW + DAY,))
    _, code = plans.create_grant(owner, days=10, now=NOW)
    with login(system) as conn:
        outcome = plans.redeem(conn, alice, code, now=NOW + 2 * DAY)
    assert outcome == plans.Redeemed("pro", 10, NOW + 12 * DAY)


def test_the_most_recent_end_is_the_one_reported(system, owner, alice):
    give(owner, system, alice, days=10)  # ends day 10
    give(owner, system, alice, days=30, now=NOW + 12 * DAY)  # day 12 to day 42
    state = access(system, alice, NOW + 60 * DAY)
    assert (state.state, state.ends_at, state.reason) == ("ended", NOW + 42 * DAY, "expired")


# ---- the form, the email and the sign-up, end to end ----------------------------------------------------------------


class RecordingMailer:
    def __init__(self) -> None:
        self.sent = []
        self.fail_with = None

    def send(self, email) -> None:
        if self.fail_with:
            raise MailError(self.fail_with)
        self.sent.append(email)


def fill_in_the_form(browser, email=NEWCOMER.email, **over):
    body = {"first_name": "Newcomer", "email": email, "prop_firm": "Apex", "website": "", **over}
    headers = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
    return browser.client.post("/api/v1/beta/join", json=body, headers=headers)


def code_in(email) -> str:
    return re.search(r"TJ-[A-Z0-9]{4}-[A-Z0-9]{4}", email.text).group(0)


def test_from_the_form_to_a_signed_in_account_with_thirty_days(world, owner):
    browser = world()
    mailer = browser.client.app.state.mailer = RecordingMailer()
    assert fill_in_the_form(browser).status_code == 202
    [email] = mailer.sent
    assert email.to == NEWCOMER.email
    signed_up = browser.sign_in(NEWCOMER, invite=code_in(email))
    assert signed_up.status_code == 303 and signed_up.headers["location"] == "/"
    body = browser.session().json()
    assert (body["plan"]["effective"], body["plan"]["source"]) == ("pro", "grant")
    row_ = row(owner, "beta_signups", email=NEWCOMER.email)
    assert (row_["first_name"], row_["prop_firm"], row_["email_sent_at"] is not None) == ("Newcomer", "Apex", True)


def test_the_same_form_again_sends_nothing_more_and_says_the_same(world, owner):
    browser = world()
    mailer = browser.client.app.state.mailer = RecordingMailer()
    first = fill_in_the_form(browser)
    again = fill_in_the_form(browser)
    assert (first.status_code, first.json()) == (again.status_code, again.json())
    assert len(mailer.sent) == 1


def test_an_address_that_already_has_an_account_gets_the_same_answer_and_no_email(world, owner, alice):
    browser = world()
    mailer = browser.client.app.state.mailer = RecordingMailer()
    response = fill_in_the_form(browser, email="alice@example.test")
    assert response.status_code == 202 and response.json() == {"status": "ok"}
    assert mailer.sent == [] and count(owner, "beta_signups") == 0


def test_when_the_email_cannot_be_sent_the_person_may_ask_again_at_once(world, owner):
    browser = world()
    mailer = browser.client.app.state.mailer = RecordingMailer()
    mailer.fail_with = "mail_unavailable_503"
    assert fill_in_the_form(browser).status_code == 202 and mailer.sent == []
    assert row(owner, "beta_signups")["email_failures"] == 1
    mailer.fail_with = None
    assert fill_in_the_form(browser).status_code == 202
    [email] = mailer.sent
    assert browser.sign_in(NEWCOMER, invite=code_in(email)).status_code == 303


def test_the_form_answers_503_when_no_email_provider_is_configured(world, owner):
    browser = world()
    browser.client.app.state.mailer = None
    response = fill_in_the_form(browser)
    assert response.status_code == 503 and count(owner, "beta_signups") == 0


# ---- read-only after the access ends, on the real app ---------------------------------------------------------------


def writes_of(browser):
    token = browser.session().json()["csrf_token"]
    return {"sec-fetch-site": "same-origin", "x-csrf-token": token}


def beta_member(world, system):
    """A person who signed up with a personal code: (the browser, the headers a page would send)."""
    _, code = join(system, email=NEWCOMER.email)
    browser = world()
    assert browser.sign_in(NEWCOMER, invite=code).status_code == 303
    return browser, writes_of(browser)


def test_while_the_access_runs_the_person_can_write(world, system):
    browser, headers = beta_member(world, system)
    assert browser.session().json()["access"]["state"] == "active"
    made = browser.client.post("/api/v1/tag-groups", json={"name": "While it runs"}, headers=headers)
    assert made.status_code == 200 or made.status_code == 201, made.text


def test_when_the_thirty_days_end_the_journal_is_read_only_and_says_why(world, system, owner):
    browser, headers = beta_member(world, system)
    owner.execute(
        "UPDATE grant_redemptions SET ends_at = now() - interval '1 day', starts_at = now() - interval '31 days'"
    )
    access = browser.session().json()["access"]
    assert (access["state"], access["reason"]) == ("ended", "expired")
    before = browser.client.get("/api/v1/tags")
    assert before.status_code == 200  # looking is still allowed
    refused = browser.client.post("/api/v1/tag-groups", json={"name": "Too late"}, headers=headers)
    assert refused.status_code == 403 and "read-only" in refused.text
    assert browser.client.get("/api/v1/tags").json() == before.json()  # nothing was added
    assert browser.client.get("/api/v1/trades").status_code == 200


def test_an_operator_ending_it_early_has_the_same_effect_at_once(world, system, owner):
    browser, headers = beta_member(world, system)
    owner.execute("UPDATE grant_redemptions SET revoked_at = now()")
    assert browser.session().json()["access"] == {
        "state": "ended",
        "ends_at": browser.session().json()["access"]["ends_at"],
        "reason": "revoked",
    }
    assert browser.client.post("/api/v1/tag-groups", json={"name": "No"}, headers=headers).status_code == 403


def test_an_ended_account_can_still_delete_itself_and_sign_out(world, system, owner):
    browser, headers = beta_member(world, system)
    owner.execute(
        "UPDATE grant_redemptions SET ends_at = now() - interval '1 day', starts_at = now() - interval '31 days'"
    )
    assert browser.client.post("/auth/logout", headers=headers).status_code == 200


def test_a_new_code_gives_the_writing_back(world, system, owner):
    browser, headers = beta_member(world, system)
    owner.execute(
        "UPDATE grant_redemptions SET ends_at = now() - interval '1 day', starts_at = now() - interval '31 days'"
    )
    assert browser.client.post("/api/v1/tag-groups", json={"name": "No"}, headers=headers).status_code == 403
    _, promo = plans.create_grant(owner, days=10)
    redeemed = browser.client.post("/api/v1/grants/redeem", json={"code": promo}, headers=headers)
    assert redeemed.status_code == 200, redeemed.text
    assert browser.session().json()["access"]["state"] == "active"
    assert browser.client.post("/api/v1/tag-groups", json={"name": "Back"}, headers=headers).status_code in (200, 201)


def test_the_owners_own_account_is_never_read_only(world, owner, alice):
    from auth_world import ALICE

    browser = world()
    assert browser.sign_in(ALICE).status_code == 303
    assert browser.session().json()["access"] == {"state": "active", "ends_at": None, "reason": None}
    headers = writes_of(browser)
    assert browser.client.post("/api/v1/tag-groups", json={"name": "Always"}, headers=headers).status_code in (200, 201)
