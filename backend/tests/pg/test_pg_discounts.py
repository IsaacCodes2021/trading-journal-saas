"""Discount codes on Postgres (migration 0022; docs/design/promo-codes.md section 6, the Paddle guide section 5.8): the
table's rules, who may touch what, the operator's code and its copy at the provider (made, retried, adopted, archived), and
a person's code saved, carried to the checkout and forgotten once it has done its work. The fake provider stands in for
Paddle (tests/pg/test_pg_billing_paddle.py and tests/test_billing_paddle.py cover Paddle's own calls)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
from auth_world import ALICE, NEWCOMER, audit_rows
from billing_world import Clock, billing_app, close_browser, make_fake, open_browser

from tradejournal.api.admin_service import PgAdminService
from tradejournal.api.ports import AdminRefusal
from tradejournal.billing.types import DiscountSpec
from tradejournal.pg import invites


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake(clock):
    return make_fake(clock)


@pytest.fixture
def started():
    browsers = []
    yield browsers
    for browser in browsers:
        close_browser(browser)


@pytest.fixture
def app(db, app_url, login_url, fake, clock, trial):
    return billing_app(db, app_url, login_url, fake, clock)


@pytest.fixture
def browser(app, started):
    browser = open_browser(app)
    started.append(browser)
    return browser


@pytest.fixture
def operator(system, store, fake, clock):
    return PgAdminService(system, store, clock=clock, billing=fake, billing_enabled=True)


def make_discount(operator, op, code="LAUNCH20", **changes):
    values = {"percent_off": 20, "duration": "repeating", "duration_months": 3, "max_redemptions": None}
    values.update(changes)
    return operator.create_grant_code(
        op,
        label=values.pop("label", "Launch"),
        plan="pro",
        days=None,
        expires_in_days=values.pop("expires_in_days", None),
        code=code,
        kind="discount",
        **values,
    ).grant_code


def write(browser, method, path, body=None):
    token = browser.session().json()["csrf_token"]
    return browser.client.request(
        method, path, headers={"sec-fetch-site": "same-origin", "x-csrf-token": token}, json=body
    )


def type_code(browser, code):
    return write(browser, "POST", "/api/v1/grants/redeem", {"code": code})


def billing(browser):
    return browser.client.get("/api/v1/billing").json()


def pending_rows(owner):
    return owner.execute("SELECT user_id, grant_code_id FROM pending_promos").fetchall()


# ---- the table and who may touch it ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        ("kind, days, percent_off, duration, sync_status", "'discount', NULL, 20, 'once', 'pending'"),  # no code
        ("kind, code, days, percent_off, duration, sync_status", "'discount', 'X20', 30, 20, 'once', 'pending'"),
        ("kind, code, percent_off, duration, sync_status", "'discount', 'X20', 20, 'repeating', 'pending'"),
        (
            "kind, code, percent_off, duration, duration_months, sync_status",
            "'discount', 'X20', 20, 'once', 3, 'pending'",
        ),
        ("kind, code, percent_off, duration", "'discount', 'X20', 20, 'once'"),  # no sync state
        ("kind, code, days, percent_off", "'free_days', 'X20', 30, 20"),  # free days with a percentage
        ("kind, code, days, sync_status", "'free_days', 'X20', 30, 'pending'"),
        ("kind, code, percent_off, duration, sync_status", "'discount', 'X20', 101, 'once', 'pending'"),
    ],
)
def test_each_kind_of_code_holds_only_its_own_columns(owner, columns, values):
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(
            f"INSERT INTO grant_codes (code_hash, plan, {columns}) VALUES (sha256('x'::bytea), 'pro', {values})"
        )


def test_the_sign_in_role_reads_a_discounts_terms_but_never_a_code_and_writes_no_code(owner):
    def can(role, privilege, column=None, table="grant_codes"):
        if column is None:
            return owner.execute("SELECT has_table_privilege(%s, %s, %s) AS ok", (role, table, privilege)).fetchone()[
                "ok"
            ]
        return owner.execute(
            "SELECT has_column_privilege(%s, %s, %s, %s) AS ok", (role, table, column, privilege)
        ).fetchone()["ok"]

    assert not can("app_login", "SELECT", "code")
    assert all(
        can("app_login", "SELECT", c) for c in ("percent_off", "duration", "provider_discount_id", "sync_status")
    )
    assert not can("app_login", "UPDATE", "sync_status") and not can("app_login", "INSERT")
    assert can("app_login", "INSERT", table="pending_promos") and can("app_login", "DELETE", table="pending_promos")
    assert can("app_webhook", "DELETE", table="pending_promos") and not can(
        "app_webhook", "INSERT", table="pending_promos"
    )
    assert can("app_admin", "UPDATE", "sync_status") and can("app_admin", "UPDATE", "provider_discount_id")
    assert not can("app_admin", "UPDATE", "percent_off") and not can("app_admin", "UPDATE", "code")
    assert not can("app_rw", "SELECT", table="pending_promos")


# ---- the operator's code and its copy at the provider ----------------------------------------------------------------------


def test_a_discount_code_is_saved_then_given_to_the_provider_with_its_terms(operator, owner, op, fake):
    made = make_discount(operator, op, max_redemptions=50, expires_in_days=30, label="Launch week")
    assert (made.kind, made.days, made.percent_off, made.duration, made.duration_months) == (
        "discount",
        None,
        20,
        "repeating",
        3,
    )
    assert (made.words, made.sync_status, made.sync_error) == ("20% off your first 3 months", "synced", None)
    [(discount_id, spec)] = fake.discounts.items()
    assert (spec["code"], spec["percent_off"], spec["duration"], spec["months"], spec["max_uses"], spec["note"]) == (
        "LAUNCH20",
        20,
        "repeating",
        3,
        50,
        "Launch week",
    )
    row = owner.execute(
        "SELECT provider, provider_discount_id, synced_at FROM grant_codes WHERE id = %s", (made.id,)
    ).fetchone()
    assert (row["provider"], row["provider_discount_id"]) == ("fake", discount_id) and row["synced_at"] is not None
    created = [r["meta"] for r in audit_rows(owner) if r["action"] == "admin_grant_code_created"]
    assert created == [
        {
            "grant": made.id,
            "code": "LAUNCH20",
            "label": "Launch week",
            "plan": "pro",
            "kind": "discount",
            "percent_off": 20,
            "duration": "repeating",
            "duration_months": 3,
            "max_redemptions": 50,
        }
    ]
    synced = [r["meta"] for r in audit_rows(owner) if r["action"] == "admin_discount_synced"]
    assert synced == [{"grant": made.id, "code": "LAUNCH20", "status": "synced"}]


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"duration": "once", "duration_months": None}, "20% off your first payment"),
        ({"duration": "forever", "duration_months": None, "percent_off": 50}, "50% off every payment"),
        ({"duration_months": 1}, "20% off your first month"),
    ],
)
def test_each_duration_is_said_in_plain_words(operator, op, changes, words):
    assert make_discount(operator, op, **changes).words == words


def test_without_a_provider_a_discount_waits_as_pending_and_cannot_be_synced(system, store, owner, op, clock):
    offline = PgAdminService(system, store, clock=clock)
    made = make_discount(offline, op)
    assert made.sync_status == "pending"
    assert offline.list_grant_codes(limit=50, offset=0).discounts_live is False
    with pytest.raises(AdminRefusal) as refused:
        offline.sync_grant_code(op, made.id)
    assert refused.value.status == 409


def test_a_provider_that_is_down_leaves_the_code_failed_and_a_retry_syncs_it(operator, op, fake):
    fake.fail_next("down")
    made = make_discount(operator, op)
    assert (made.sync_status, made.sync_error) == ("failed", "fake_down")
    assert fake.discounts == {}
    again = operator.sync_grant_code(op, made.id)
    assert (again.sync_status, again.sync_error) == ("synced", None) and len(fake.discounts) == 1


def test_a_copy_the_provider_made_but_never_confirmed_is_adopted_not_made_twice(operator, op, fake):
    fake.fail_next("down")  # the lookup before the create fails: nothing is made
    first = make_discount(operator, op)
    assert first.sync_status == "failed"
    fake.calls.clear()
    fake._failures = [None, "act_then_timeout"]  # type: ignore[list-item]  # the lookup answers; the create acts, then times out
    assert operator.sync_grant_code(op, first.id).sync_status == "failed" and len(fake.discounts) == 1
    assert operator.sync_grant_code(op, first.id).sync_status == "synced"  # found by its code
    assert len(fake.discounts) == 1 and [c for c, _ in fake.calls].count("create_discount") == 1


def test_revoking_archives_the_providers_copy_and_a_failed_archive_can_be_retried(operator, op, fake):
    one = make_discount(operator, op, code="ONE20")
    revoked = operator.revoke_grant_code(op, one.id, "campaign over")
    [first] = fake.discounts.values()
    assert revoked.status == "revoked" and revoked.sync_status == "synced" and first["archived"] is True
    two = make_discount(operator, op, code="TWO20")
    fake.fail_next("down")
    half = operator.revoke_grant_code(op, two.id, "campaign over")
    assert (half.status, half.sync_status, half.sync_error) == ("revoked", "failed", "fake_down")
    assert operator.sync_grant_code(op, two.id).sync_status == "synced"
    assert all(d["archived"] for d in fake.discounts.values())


def test_a_code_the_provider_already_holds_is_adopted_rather_than_made_again(operator, op, fake):
    fake.create_discount(DiscountSpec("TAKEN20", 20, "once"))  # made at the provider by hand, say
    made = make_discount(operator, op, code="TAKEN20")
    assert made.sync_status == "synced" and len(fake.discounts) == 1


def test_a_free_days_code_is_never_given_to_the_provider_and_cannot_be_synced(operator, op, fake):
    made = operator.create_grant_code(
        op, label="", plan="pro", days=30, max_redemptions=None, expires_in_days=None, code="FREE30"
    ).grant_code
    assert made.sync_status is None and fake.discounts == {}
    with pytest.raises(AdminRefusal):
        operator.sync_grant_code(op, made.id)


# ---- a person's code ------------------------------------------------------------------------------------------------------


def test_a_discount_code_is_saved_shown_carried_to_the_checkout_and_forgotten_once_used(
    browser, operator, owner, op, fake, alice
):
    made = make_discount(operator, op)
    browser.sign_in(ALICE)
    typed = type_code(browser, "launch 20")
    assert typed.status_code == 200
    assert typed.json()["discount"] == {
        "percent_off": 20,
        "duration": "repeating",
        "duration_months": 3,
        "words": "20% off your first 3 months",
    }
    assert typed.json()["effective"] in ("pro", "free")  # the plan does not change until they subscribe
    assert billing(browser)["discount"]["words"] == "20% off your first 3 months"
    response = write(browser, "POST", "/api/v1/billing/checkout")
    checkout = response.json()["pay_url"].split("_ptxn=")[1]
    discount_id = owner.execute("SELECT provider_discount_id FROM grant_codes WHERE id = %s", (made.id,)).fetchone()[
        "provider_discount_id"
    ]
    assert fake.checkouts[checkout].discount_id == discount_id
    assert owner.execute("SELECT grant_code_id FROM billing_checkouts").fetchone()["grant_code_id"] == made.id
    delivery = fake.complete_checkout(checkout)
    assert browser.client.post("/webhooks/billing", content=delivery.body, headers=delivery.headers).status_code == 200
    assert pending_rows(owner) == []  # it has done its work
    assert operator.list_grant_codes(limit=50, offset=0).grant_codes[0].used == 1
    saved = [r["meta"] for r in audit_rows(owner) if r["action"] in ("discount_saved", "billing_checkout_started")]
    assert saved == [{"via": "settings"}, {"free_days": 0, "discount": made.id}]


def test_with_billing_off_a_discount_code_is_the_one_refusal(
    db, app_url, login_url, fake, clock, operator, op, alice, started
):
    make_discount(operator, op)
    off = open_browser(billing_app(db, app_url, login_url, fake, clock, enabled=False))
    started.append(off)
    off.sign_in(ALICE)
    refused = type_code(off, "LAUNCH20")
    assert refused.status_code == 422 and "did not work" in refused.json()["detail"]


def test_a_code_not_yet_at_the_provider_or_used_up_is_the_one_refusal(
    browser, system, store, operator, owner, op, fake, alice, bob, clock
):
    make_discount(PgAdminService(system, store, clock=clock), op, code="WAIT20")  # never pushed: pending
    full = make_discount(operator, op, code="ONCE20", max_redemptions=1)
    owner.execute(
        "INSERT INTO billing_checkouts (provider_checkout_id, provider, user_id, grant_code_id, completed_at) "
        "VALUES ('txn_used', 'fake', %s, %s, now())",
        (bob, full.id),
    )  # its one use: bob subscribed with it
    browser.sign_in(ALICE)
    for code in ("WAIT20", "ONCE20", "NOPE20"):
        assert type_code(browser, code).status_code == 422
    assert pending_rows(owner) == []


@pytest.mark.parametrize(
    "change",
    [
        "sync_status = 'failed', sync_error = 'fake_down'",  # the provider holds an id, but its copy is not confirmed
        "provider = 'paddle'",  # made at another provider than the one this server uses
    ],
)
def test_a_code_whose_provider_copy_is_not_this_servers_and_confirmed_is_the_one_refusal(
    browser, operator, owner, op, alice, change
):
    made = make_discount(operator, op)
    browser.sign_in(ALICE)
    assert type_code(browser, "LAUNCH20").status_code == 200  # it works as made
    owner.execute(f"UPDATE grant_codes SET {change} WHERE id = %s", (made.id,))  # noqa: S608 (fixed changes above)
    assert type_code(browser, "LAUNCH20").status_code == 422
    assert billing(browser)["discount"] is None and pending_rows(owner) == []  # the saved one is dropped too


def test_a_saved_code_that_stops_working_is_dropped_and_the_checkout_carries_none(
    browser, operator, owner, op, fake, alice
):
    made = make_discount(operator, op)
    browser.sign_in(ALICE)
    assert type_code(browser, "LAUNCH20").status_code == 200
    operator.revoke_grant_code(op, made.id, "ended early")
    assert billing(browser)["discount"] is None and pending_rows(owner) == []
    checkout = write(browser, "POST", "/api/v1/billing/checkout").json()["pay_url"].split("_ptxn=")[1]
    assert fake.checkouts[checkout].discount_id is None


def test_a_newer_code_replaces_the_one_saved(browser, operator, owner, op, alice):
    make_discount(operator, op, code="FIRST20")
    second = make_discount(operator, op, code="SECOND50", percent_off=50, duration="once", duration_months=None)
    browser.sign_in(ALICE)
    type_code(browser, "FIRST20")
    assert type_code(browser, "SECOND50").json()["discount"]["words"] == "50% off your first payment"
    assert [r["grant_code_id"] for r in pending_rows(owner)] == [second.id]


def test_a_newcomer_who_signs_up_with_a_discount_code_lands_on_the_plan_page_with_it_saved(
    browser, operator, owner, op
):
    make_discount(operator, op)
    response = browser.sign_in(NEWCOMER, invite=invites.create_invite(owner), promo="launch20")
    assert response.status_code == 303 and response.headers["location"] == "/settings/plan?promo=saved"
    assert billing(browser)["discount"]["words"] == "20% off your first 3 months"
    assert [r["meta"] for r in audit_rows(owner) if r["action"] == "discount_saved"] == [{"via": "signup"}]


def test_with_billing_off_a_discount_code_at_sign_up_is_caught_before_the_provider(
    db, app_url, login_url, fake, clock, operator, owner, op, started
):
    make_discount(operator, op)
    off = open_browser(billing_app(db, app_url, login_url, fake, clock, enabled=False))
    started.append(off)
    invite = invites.create_invite(owner)
    refused = off.client.get("/auth/login", params={"invite": invite, "promo": "LAUNCH20"})
    assert refused.status_code == 302
    assert parse_qs(urlsplit(refused.headers["location"]).query)["error"] == ["promo_invalid"]
