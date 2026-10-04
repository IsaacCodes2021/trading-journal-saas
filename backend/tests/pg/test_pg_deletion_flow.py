"""Deleting an account through the real app: proving who you are again, asking, changing your mind, and the sweep.

A pretend provider plays Kinde, so a test can answer a re-authentication prompt freshly, stale (a provider that ignored
`max_age=0`), as someone else, or not at all. The server must be right in every one of those cases, because it does not take
the provider's word that it asked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from auth_world import ALICE, audit_rows
from fake_idp import person
from pg_helpers import make_account, make_trades

from tradejournal.pg import sweep

EMAIL = {"email": "alice@example.test"}
WRITE = {"sec-fetch-site": "same-origin"}


def at(seconds_ago: float):
    return datetime.now(UTC) - timedelta(seconds=seconds_ago)


def identity(seconds_ago: float | None, **who):
    changes = {"auth_time": None if seconds_ago is None else at(seconds_ago)}
    return person(subject=who.get("subject", ALICE.subject), email=who.get("email", ALICE.email), **changes)


def headers(browser):
    return {**WRITE, "x-csrf-token": browser.session().json()["csrf_token"]}


def delete_request(browser, **body):
    return browser.client.post("/api/v1/account/deletion", json=body or EMAIL, headers=headers(browser))


def user_row(owner, user):
    return owner.execute("SELECT status, deletion_scheduled_at FROM users WHERE id = %s", (user,)).fetchone()


def sessions_of(owner, user):
    return owner.execute(
        "SELECT authenticated_at, created_at, revoked_at FROM sessions WHERE user_id = %s ORDER BY id", (user,)
    ).fetchall()


class Directory:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def delete_user(self, subject: str) -> bool:
        self.removed.append(subject)
        return True


# ---- proving who you are again ------------------------------------------------------------------------------------------


def test_a_silent_sign_in_is_not_recent_so_the_deletion_is_refused_until_the_person_signs_in_again(world, owner, alice):
    browser = world()
    browser.sign_in(identity(3 * 86400))  # the provider signed them in with a session from three days ago
    assert browser.session().json()["flags"]["recently_authenticated"] is False
    refused = delete_request(browser)
    assert refused.status_code == 403 and "Sign in again" in refused.json()["detail"]
    assert user_row(owner, alice)["status"] == "active"


def test_a_fresh_authentication_is_asked_for_by_name_and_makes_the_session_recent(world, owner, alice):
    browser = world()
    browser.sign_in(identity(3 * 86400))
    url, state = browser.start(reauth="true", next="/settings/privacy")
    assert "reauth=1" in url
    code = browser.idp.sign_in(url, identity(5))  # they typed their password just now
    response = browser.come_back(code=code, state=state)
    assert response.status_code == 303 and response.headers["location"] == "/settings/privacy"
    assert browser.session().json()["flags"]["recently_authenticated"] is True
    [dead, live] = sessions_of(owner, alice)
    assert dead["revoked_at"] is not None and live["revoked_at"] is None  # a new sign-in is a new session
    assert abs((live["authenticated_at"] - at(5)).total_seconds()) < 2  # remembered as the provider's `auth_time`
    assert [r["meta"] for r in audit_rows(owner) if r["action"] == "login"][-1] == {"mfa": False, "reauth": True}


def test_the_window_for_a_fresh_authentication_is_two_minutes(world, owner, alice):
    for ago, ok in ((100, True), (140, False)):
        browser = world()
        browser.sign_in(identity(3 * 86400))
        url, state = browser.start(reauth="true")
        outcome = browser.come_back(code=browser.idp.sign_in(url, identity(ago)), state=state)
        assert (outcome.headers["location"] == "/") is ok, ago
        if not ok:
            assert outcome.headers["location"] == "/invite?error=reauth_failed"


def test_a_provider_that_ignored_the_request_gets_no_credit_the_old_session_stays_and_no_new_one_starts(
    world, owner, alice
):
    browser = world()
    browser.sign_in(identity(3 * 86400))
    old_token = browser.token
    url, state = browser.start(reauth="true")
    outcome = browser.come_back(
        code=browser.idp.sign_in(url, identity(3 * 86400)), state=state
    )  # silently signed in again
    assert outcome.headers["location"] == "/invite?error=reauth_failed"
    assert browser.token == old_token and browser.session().status_code == 200  # still signed in, as before
    assert len(sessions_of(owner, alice)) == 1
    assert browser.session().json()["flags"]["recently_authenticated"] is False
    reasons = [r["meta"].get("reason") for r in audit_rows(owner) if r["action"] == "login_failed"]
    assert reasons == ["reauth_stale"]


@pytest.mark.parametrize(("seconds_ago", "reason"), [(None, "reauth_no_auth_time"), (-600, "reauth_future")])
def test_an_answer_with_no_time_or_a_time_in_the_future_is_not_believed(world, owner, alice, seconds_ago, reason):
    browser = world()
    browser.sign_in(identity(3 * 86400))
    url, state = browser.start(reauth="true")
    outcome = browser.come_back(code=browser.idp.sign_in(url, identity(seconds_ago)), state=state)
    assert outcome.headers["location"] == "/invite?error=reauth_failed"
    assert [r["meta"].get("reason") for r in audit_rows(owner) if r["action"] == "login_failed"] == [reason]


def test_someone_else_completing_the_prompt_does_not_count_and_signs_nobody_in(world, owner, alice, bob):
    browser = world()
    browser.sign_in(identity(3 * 86400))
    old_token = browser.token
    url, state = browser.start(reauth="true")
    stranger = identity(5, subject="idp|bob@example.test", email="bob@example.test")  # an existing, different person
    outcome = browser.come_back(code=browser.idp.sign_in(url, stranger), state=state)
    assert outcome.headers["location"] == "/invite?error=reauth_failed"
    assert browser.token == old_token  # Alice's browser is not bob's now, nor bob's session Alice's
    assert sessions_of(owner, bob) == []
    assert [r["meta"].get("reason") for r in audit_rows(owner) if r["action"] == "login_failed"] == [
        "reauth_other_person"
    ]


def test_a_stranger_with_no_account_cannot_use_a_prompt_to_sign_up(world, owner, alice):
    browser = world()
    browser.sign_in(identity(3 * 86400))
    url, state = browser.start(reauth="true")
    nobody = identity(5, subject="kp_nobody", email="nobody@example.test")
    assert (
        browser.come_back(code=browser.idp.sign_in(url, nobody), state=state).headers["location"]
        == "/invite?error=reauth_failed"
    )
    assert owner.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1


def test_with_no_session_to_re_authenticate_there_is_nothing_to_confirm(world, owner, alice):
    browser = world()  # never signed in
    url, state = browser.start(reauth="true")
    outcome = browser.come_back(code=browser.idp.sign_in(url, identity(5)), state=state)
    assert outcome.headers["location"] == "/invite?error=reauth_failed" and browser.token is None
    assert sessions_of(owner, alice) == []


def test_a_plain_sign_in_with_an_old_auth_time_is_fine_and_remembers_how_old(world, owner, alice):
    browser = world()
    assert browser.sign_in(identity(3 * 86400)).headers["location"] == "/"
    [row] = sessions_of(owner, alice)
    assert timedelta(days=2, hours=23) < row["created_at"] - row["authenticated_at"] < timedelta(days=3, minutes=1)


def test_a_provider_time_in_the_future_is_never_stored_later_than_the_session_itself(world, owner, alice):
    browser = world()
    browser.sign_in(identity(-3600))
    [row] = sessions_of(owner, alice)
    assert row["authenticated_at"] <= row["created_at"]


# ---- asking, and what happens ----------------------------------------------------------------------------------------------


def recent_session(browser):
    url, state = browser.start(reauth="true")
    browser.come_back(code=browser.idp.sign_in(url, identity(5)), state=state)


def test_asking_schedules_a_week_ends_every_session_clears_the_cookie_and_is_recorded(world, owner, alice):
    laptop = world()
    laptop.sign_in(identity(3 * 86400))
    phone = laptop.another("Mozilla/5.0 (iPhone)")
    phone.sign_in(identity(3 * 86400))
    recent_session(laptop)
    assert phone.session().status_code == 200

    response = delete_request(laptop)
    assert response.status_code == 200
    row = user_row(owner, alice)
    assert (
        row["status"] == "pending_deletion"
        and abs((row["deletion_scheduled_at"] - (datetime.now(UTC) + timedelta(days=7))).total_seconds()) < 5
    )
    assert response.json()["scheduled_for"].startswith(row["deletion_scheduled_at"].strftime("%Y-%m-%dT%H:%M"))
    assert laptop.session().status_code == 401 and phone.session().status_code == 401  # every device, at once
    assert all(r["revoked_at"] is not None for r in sessions_of(owner, alice))
    assert "max-age=0" in response.headers["set-cookie"].lower()
    [event] = [r for r in audit_rows(owner) if r["action"] == "deletion_requested"]
    assert event["user_id"] == alice and event["ip_hash"] and event["meta"]["scheduled_for"]


def test_the_wrong_email_or_a_second_request_changes_nothing(world, owner, alice):
    browser = world()
    browser.sign_in(identity(3 * 86400))
    recent_session(browser)
    assert delete_request(browser, email="bob@example.test").status_code == 422
    assert user_row(owner, alice)["status"] == "active" and browser.session().status_code == 200
    assert delete_request(browser).status_code == 200
    again = world()  # a request from a session that somehow still exists cannot reschedule it
    assert again.client.post("/api/v1/account/deletion", json=EMAIL).status_code == 401


# ---- changing your mind: signing in again --------------------------------------------------------------------------------


def test_signing_in_before_the_date_cancels_the_deletion_and_everything_is_still_there(world, db, owner, alice):
    with db.tenant_session(alice) as conn:
        make_trades(conn, make_account(conn, "Apex 07").id, 4)
    browser = world()
    browser.sign_in(identity(3 * 86400))
    recent_session(browser)
    assert delete_request(browser).status_code == 200

    back = world()
    outcome = back.sign_in(identity(3 * 86400), next="/trades")
    assert outcome.status_code == 303 and outcome.headers["location"] == "/settings/privacy?restored=1"  # and says why
    assert user_row(owner, alice) == {"status": "active", "deletion_scheduled_at": None}
    assert back.session().status_code == 200
    with db.tenant_session(alice) as conn:
        assert conn.execute("SELECT count(*) AS n FROM trades").fetchone()["n"] == 4  # nothing was touched
    assert [r["action"] for r in audit_rows(owner) if r["action"].startswith("deletion")] == [
        "deletion_requested",
        "deletion_cancelled",
    ]


def test_a_suspended_account_is_still_turned_away_and_is_not_restored_by_signing_in(world, owner, alice):
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (alice,))
    browser = world()
    assert browser.sign_in(identity(3 * 86400)).headers["location"] == "/invite?error=account_unavailable"
    assert user_row(owner, alice)["status"] == "suspended" and browser.token is None


# ---- after the date ------------------------------------------------------------------------------------------------------


def test_after_the_sweep_the_person_and_their_data_are_gone_and_signing_in_again_starts_nothing_old(
    world, db, system, owner, alice
):
    with db.tenant_session(alice) as conn:
        make_trades(conn, make_account(conn, "Apex 07").id, 4)
    browser = world()
    browser.sign_in(identity(3 * 86400))
    recent_session(browser)
    assert delete_request(browser).status_code == 200
    owner.execute(
        "UPDATE users SET deletion_scheduled_at = now() - interval '1 hour' WHERE id = %s", (alice,)
    )  # the week has passed

    directory = Directory()
    report = sweep.run(system, directory, apply=True)
    assert (report.deleted, report.identities_removed) == (1, 1) and directory.removed == [ALICE.subject]
    assert owner.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 0

    back = world()
    outcome = back.sign_in(
        identity(5)
    )  # the provider no longer knows them, but suppose they come back: it is a new person
    assert outcome.headers["location"] == "/invite?error=invite_invalid" and back.token is None  # needs an invite again
    assert owner.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 0
