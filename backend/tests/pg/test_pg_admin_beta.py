"""The operator's list of beta sign-ups and ending someone's access, on a real Postgres (migration 0015)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from auth_world import NEWCOMER
from fake_idp import person

from tradejournal.api.ports import AdminRefusal
from tradejournal.pg import admin_beta, beta, invites

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)  # what the operator service believes the time is (conftest)
DAY = timedelta(days=1)


def join(system, *, email, first_name="Alex", prop_firm="Topstep"):
    code = invites.generate_code()
    with system.system_session("login") as conn:
        signup = beta.join(conn, email=email, first_name=first_name, prop_firm=prop_firm, code=code)
    assert signup is not None
    return signup, code


def sign_up(world, system, email, **kw):
    """Joined, and signed in with the personal code: (the sign-up id, the browser)."""
    signup, code = join(system, email=email, **kw)
    browser = world()
    assert browser.sign_in(person(subject=f"idp|{email}", email=email), invite=code).status_code == 303
    return signup, browser


def ended_in_the_past(owner, email):
    owner.execute(
        """UPDATE grant_redemptions SET starts_at = %s, ends_at = %s
           WHERE user_id = (SELECT id FROM users WHERE email = %s)""",
        (NOW - 40 * DAY, NOW - 10 * DAY, email),
    )


def by_email(listing):
    return {p.email: p for p in listing.people}


# ---- who is in which state ------------------------------------------------------------------------------------------


def test_the_list_says_where_each_person_stands(world, system, owner, service, op):
    join(system, email="waiting@example.test", first_name="Wendy", prop_firm="Apex")
    sign_up(world, system, "active@example.test", first_name="Ada")
    sign_up(world, system, "ended@example.test", first_name="Eve")
    ended_in_the_past(owner, "ended@example.test")
    sign_up(world, system, "revoked@example.test", first_name="Rex")
    owner.execute(
        "UPDATE grant_redemptions SET revoked_at = %s WHERE user_id = (SELECT id FROM users WHERE email = %s)",
        (NOW - DAY, "revoked@example.test"),
    )
    listing = service.list_beta(state=None, q=None, limit=50, offset=0)
    people = by_email(listing)
    assert {e: (p.state, p.code) for e, p in people.items()} == {
        "waiting@example.test": ("waiting", "unused"),
        "active@example.test": ("active", "used"),
        "ended@example.test": ("ended", "used"),
        "revoked@example.test": ("revoked", "used"),
    }
    assert people["waiting@example.test"].first_name == "Wendy" and people["waiting@example.test"].prop_firm == "Apex"
    assert people["waiting@example.test"].access_ends_at is None
    assert people["active@example.test"].access_ends_at is not None
    assert listing.counts == {"waiting": 1, "active": 1, "ended": 1, "revoked": 1, "all": 4}
    assert listing.total == 4


def test_a_code_that_was_not_used_in_time_or_was_withdrawn_is_named_so(system, owner, service):
    expired, _ = join(system, email="slow@example.test")
    withdrawn, _ = join(system, email="pulled@example.test")
    owner.execute(
        "UPDATE invites SET expires_at = %s WHERE id = (SELECT invite_id FROM beta_signups WHERE id = %s)",
        (NOW - DAY, expired),
    )
    owner.execute(
        "UPDATE invites SET revoked_at = %s WHERE id = (SELECT invite_id FROM beta_signups WHERE id = %s)",
        (NOW - DAY, withdrawn),
    )
    people = by_email(service.list_beta(state=None, q=None, limit=50, offset=0))
    assert people["slow@example.test"].code == "expired" and people["pulled@example.test"].code == "withdrawn"
    assert {p.state for p in people.values()} == {"waiting"}


def test_the_list_filters_by_state_and_searches_name_address_and_firm(world, system, owner, service):
    join(system, email="a@example.test", first_name="Alice", prop_firm="Lucid")
    join(system, email="b@example.test", first_name="Bob", prop_firm="Apex")
    sign_up(world, system, "c@example.test", first_name="Carol", prop_firm="Topstep")
    assert [p.email for p in service.list_beta(state="active", q=None, limit=50, offset=0).people] == ["c@example.test"]
    assert {p.email for p in service.list_beta(state="waiting", q=None, limit=50, offset=0).people} == {
        "a@example.test",
        "b@example.test",
    }
    assert [p.first_name for p in service.list_beta(state=None, q="ali", limit=50, offset=0).people] == ["Alice"]
    assert [p.email for p in service.list_beta(state=None, q="APEX", limit=50, offset=0).people] == ["b@example.test"]
    assert [p.email for p in service.list_beta(state=None, q="c@example", limit=50, offset=0).people] == [
        "c@example.test"
    ]


def test_a_percent_or_underscore_in_the_search_box_is_taken_literally(system, owner, service):
    join(system, email="plain@example.test", first_name="Plain")
    join(system, email="odd_name@example.test", first_name="Odd")
    assert service.list_beta(state=None, q="%", limit=50, offset=0).total == 0
    assert [p.first_name for p in service.list_beta(state=None, q="d_n", limit=50, offset=0).people] == ["Odd"]
    assert service.list_beta(state=None, q="'; DROP TABLE beta_signups; --", limit=50, offset=0).total == 0
    assert service.list_beta(state=None, q=None, limit=50, offset=0).total == 2


def test_the_list_pages_newest_first_and_caps_the_page(system, owner, service):
    for n in range(5):
        join(system, email=f"p{n}@example.test", first_name=f"P{n}")
    first = service.list_beta(state=None, q=None, limit=2, offset=0)
    assert [p.first_name for p in first.people] == ["P4", "P3"] and first.total == 5
    assert [p.first_name for p in service.list_beta(state=None, q=None, limit=2, offset=4).people] == ["P0"]
    assert service.list_beta(state=None, q=None, limit=10_000, offset=0).limit == 100


# ---- ending access --------------------------------------------------------------------------------------------------


def test_revoking_an_active_person_ends_their_access_at_once_and_hands_back_the_email_to_send(
    world, system, owner, service, op
):
    signup, browser = sign_up(world, system, "newcomer@example.test", first_name="Nell")
    assert browser.session().json()["access"]["state"] == "active"
    done = service.revoke_beta(op, signup, "asked to leave", ip_hash="h")
    assert done.person.state == "revoked" and done.person.revoked_at == NOW
    assert (done.notice_to, done.notice_name) == ("newcomer@example.test", "Nell") and done.redemption_id is not None
    access = browser.session().json()["access"]
    assert (access["state"], access["reason"]) == ("ended", "revoked")


def test_the_audit_row_names_the_sign_up_and_the_reason_and_never_the_address(world, system, owner, service, op):
    signup, _ = sign_up(world, system, "private.person@example.test")
    service.revoke_beta(op, signup, "asked to leave", ip_hash="h")
    [event] = owner.execute("SELECT * FROM audit_log WHERE action = 'admin_beta_revoked'").fetchall()
    assert event["actor"] == "admin" and event["actor_user_id"] == op and event["ip_hash"] == "h"
    assert event["meta"] == {"signup": signup, "outcome": "access_ended", "reason": "asked to leave"}
    assert "private.person" not in str(event)


def test_revoking_someone_with_no_account_withdraws_their_code_and_there_is_nobody_to_email(
    world, system, owner, service, op
):
    signup, code = join(system, email="not.yet@example.test")
    done = service.revoke_beta(op, signup, "not a trader", ip_hash=None)
    assert done.person.code == "withdrawn" and done.notice_to is None and done.redemption_id is None
    # the code no longer gets anyone to the provider, or an account
    browser = world()
    refused = browser.client.get("/auth/login", params={"invite": code})
    assert refused.headers["location"] == "/invite?error=invite_invalid"


def test_there_is_nothing_to_revoke_twice_or_after_the_end(world, system, owner, service, op):
    signup, _ = sign_up(world, system, "x@example.test")
    service.revoke_beta(op, signup, "first", ip_hash=None)
    with pytest.raises(AdminRefusal) as twice:
        service.revoke_beta(op, signup, "again", ip_hash=None)
    assert twice.value.status == 409
    other, _ = sign_up(world, system, "y@example.test")
    ended_in_the_past(owner, "y@example.test")
    with pytest.raises(AdminRefusal) as already:
        service.revoke_beta(op, other, "too late", ip_hash=None)
    assert already.value.status == 409
    assert len(owner.execute("SELECT 1 FROM audit_log WHERE action = 'admin_beta_revoked'").fetchall()) == 1


def test_an_unknown_person_is_a_404(service, op):
    with pytest.raises(AdminRefusal) as unknown:
        service.revoke_beta(op, 9999, "x", ip_hash=None)
    assert unknown.value.status == 404


def test_the_notice_is_recorded_once(world, system, owner, service, op):
    signup, _ = sign_up(world, system, "n@example.test")
    done = service.revoke_beta(op, signup, "x", ip_hash=None)
    assert service.list_beta(state=None, q=None, limit=5, offset=0).people[0].notice_sent_at is None
    service.beta_notice_sent(done.redemption_id)
    stamped = service.list_beta(state=None, q=None, limit=5, offset=0).people[0].notice_sent_at
    assert stamped == NOW
    service.beta_notice_sent(done.redemption_id)  # a second call changes nothing
    assert service.list_beta(state=None, q=None, limit=5, offset=0).people[0].notice_sent_at == stamped


# ---- who may do what ------------------------------------------------------------------------------------------------


def test_the_operator_role_reads_sign_ups_and_changes_only_the_two_stamps(system, owner):
    signup, _ = join(system, email="who@example.test")
    with system.system_session("admin") as conn:
        got = conn.execute("SELECT email, first_name, prop_firm FROM beta_signups WHERE id = %s", (signup,)).fetchone()
        assert got["email"] == "who@example.test"
    for statement in (
        "UPDATE beta_signups SET first_name = 'x'",
        "DELETE FROM beta_signups",
        "INSERT INTO beta_signups (email, first_name, prop_firm) VALUES ('a@b.cc', 'A', 'B')",
        "UPDATE grant_redemptions SET ends_at = ends_at + interval '10 years'",
        "UPDATE grant_redemptions SET user_id = gen_random_uuid()",
        "SELECT code_hash FROM invites",
    ):
        with system.system_session("admin") as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


@pytest.mark.parametrize("profile", ["webhook", "sweep"])
def test_roles_with_no_business_in_the_beta_cannot_read_the_sign_ups(system, profile):
    with system.system_session(profile) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM beta_signups")


# ---- over the API on the real app -----------------------------------------------------------------------------------


def operator_browser(world, admin_url):
    browser = world(admin_database_url=admin_url)
    identity = person(subject="idp|op@example.test", email="op@example.test", acr="mfa", auth_time=datetime.now(UTC))
    assert browser.sign_in(identity).status_code == 303
    return browser


def writes(browser):
    return {"sec-fetch-site": "same-origin", "x-csrf-token": browser.session().json()["csrf_token"]}


def test_an_operator_lists_the_beta_and_revokes_someone_over_the_api_and_the_email_goes(
    world, admin_url, system, owner, op
):
    class Mailer:
        def __init__(self):
            self.sent = []

        def send(self, email):
            self.sent.append(email)

    signup, _ = sign_up(world, system, NEWCOMER.email, first_name="Nia")
    operator = operator_browser(world, admin_url)
    mailer = operator.client.app.state.mailer = Mailer()
    listing = operator.client.get("/api/admin/v1/beta").json()
    assert listing["total"] == 1 and listing["people"][0]["state"] == "active" and listing["counts"]["active"] == 1
    assert operator.client.get("/api/admin/v1/beta", params={"state": "ended"}).json()["total"] == 0
    revoked = operator.client.post(
        f"/api/admin/v1/beta/{signup}/revoke", json={"reason": "asked to leave"}, headers=writes(operator)
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["notice"] == "sent" and revoked.json()["person"]["state"] == "revoked"
    [email] = mailer.sent
    assert email.to == NEWCOMER.email and email.subject == "Your Roundturn beta access has ended"
    assert "Nia" in email.text
    assert operator.client.get("/api/admin/v1/beta").json()["people"][0]["notice_sent_at"] is not None
    again = operator.client.post(f"/api/admin/v1/beta/{signup}/revoke", json={"reason": "x"}, headers=writes(operator))
    assert again.status_code == 409 and len(mailer.sent) == 1


def test_when_the_email_cannot_be_sent_the_access_still_ends_and_the_notice_stays_pending(
    world, admin_url, system, owner, op
):
    class Down:
        def send(self, email):
            from tradejournal.api.mailer import MailError

            raise MailError("mail_unavailable_503")

    signup, browser = sign_up(world, system, NEWCOMER.email)
    operator = operator_browser(world, admin_url)
    operator.client.app.state.mailer = Down()
    revoked = operator.client.post(
        f"/api/admin/v1/beta/{signup}/revoke", json={"reason": "x"}, headers=writes(operator)
    )
    assert revoked.status_code == 200 and revoked.json()["notice"] == "pending"
    assert browser.session().json()["access"]["state"] == "ended"
    assert operator.client.get("/api/admin/v1/beta").json()["people"][0]["notice_sent_at"] is None


def test_an_ordinary_person_gets_nothing_from_the_operator_beta_routes(world, system, owner, alice):
    from auth_world import ALICE

    browser = world()
    assert browser.sign_in(ALICE).status_code == 303
    assert browser.client.get("/api/admin/v1/beta").status_code in (403, 503)
    assert browser.client.post(
        "/api/admin/v1/beta/1/revoke", json={"reason": "x"}, headers=writes(browser)
    ).status_code in (403, 503)


# ---- the rules that hold when two things happen at once, or when the list is long ------------------------------------


def test_a_long_list_comes_back_in_pages_of_at_most_a_hundred(system, owner, service):
    owner.execute(
        "INSERT INTO beta_signups (email, first_name, prop_firm) "
        "SELECT 'p' || n || '@example.test', 'P' || n, 'Apex' FROM generate_series(1, 130) AS n"
    )
    page = service.list_beta(state=None, q=None, limit=10_000, offset=0)
    assert len(page.people) == 100 and page.total == 130 and page.limit == 100
    assert len(service.list_beta(state=None, q=None, limit=10_000, offset=100).people) == 30
    assert len(service.list_beta(state=None, q=None, limit=0, offset=0).people) == 1  # never an empty page by accident


def test_a_stale_view_cannot_end_the_same_access_twice(world, system, owner, service, op):
    signup, _ = sign_up(world, system, "stale@example.test")
    with system.system_session("admin") as conn:
        stale = admin_beta.get_person(conn, signup, now=NOW)
    assert stale is not None and stale.state == "active"
    service.revoke_beta(op, signup, "first", ip_hash=None)
    with system.system_session("admin") as conn:
        assert admin_beta.revoke(conn, stale, now=NOW + DAY) is None  # it was already ended by someone else
    stamped = owner.execute("SELECT revoked_at FROM grant_redemptions").fetchone()["revoked_at"]
    assert stamped == NOW


def test_a_code_that_is_not_usable_any_more_cannot_be_withdrawn_again(system, owner, service, op):
    signup, _ = join(system, email="late@example.test")
    owner.execute("UPDATE invites SET expires_at = %s", (NOW - DAY,))
    with pytest.raises(AdminRefusal) as caught:
        service.revoke_beta(op, signup, "x", ip_hash=None)
    assert caught.value.status == 409
    assert owner.execute("SELECT revoked_at FROM invites").fetchone()["revoked_at"] is None  # left as it was


def test_the_notice_stamp_is_set_once_and_keeps_its_first_value(world, system, owner, service, op):
    signup, _ = sign_up(world, system, "stamp@example.test")
    done = service.revoke_beta(op, signup, "x", ip_hash=None)
    with system.system_session("admin") as conn:
        assert admin_beta.mark_notice_sent(conn, done.redemption_id, now=NOW + DAY) is True
        assert admin_beta.mark_notice_sent(conn, done.redemption_id, now=NOW + 2 * DAY) is False
    assert (
        owner.execute("SELECT ended_notice_sent_at FROM grant_redemptions").fetchone()["ended_notice_sent_at"]
        == NOW + DAY
    )
