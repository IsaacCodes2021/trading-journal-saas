"""Adding and changing accounts, their rules, fees and ledger through the API, against a real Postgres."""

from __future__ import annotations

from decimal import Decimal

import pytest
from helpers import SAMPLE, fixture_bytes
from pg_helpers import api_client as api
from pg_helpers import make_account

from tradejournal.finance import to_money
from tradejournal.pg import accounts, importer

DETAILS = {
    "name": "Apex 11",
    "firm": "Apex",
    "account_type": "evaluation",
    "account_size": "50000.00",
    "starting_balance": "50000.00",
    "purchase_date": "2026-01-05",
    "phase": None,
}
NO_RULES = {
    "profit_target": None,
    "max_drawdown": None,
    "trailing_type": None,
    "trail_lock_enabled": False,
    "trail_lock_level": None,
    "daily_loss_limit": None,
}
TOO_BIG = 9_999_999_999_999_999_999


def make(client, **changes) -> dict:
    response = client.post("/api/v1/accounts", json={**DETAILS, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def names(client) -> list[str]:
    return [a["name"] for a in client.get("/api/v1/accounts").json()]


# ---- adding and changing --------------------------------------------------------------------------------------------


def test_a_new_account_has_the_details_given_and_no_rules_or_outcome(db, alice):
    client = api(db, alice)
    created = make(client)
    assert created["name"] == "Apex 11" and created["firm"] == "Apex" and created["account_type"] == "evaluation"
    assert created["account_size"] == "50000.00" and created["starting_balance"] == "50000.00"
    assert all(created[k] is None for k in ("max_drawdown", "profit_target", "daily_loss_limit", "outcome", "ended_on"))
    assert created["trail_lock_enabled"] is False and created["archive_note"] == ""
    assert names(client) == ["Apex 11"]
    assert make(client, name="Lucid 02", firm="Lucid", account_type="funded", account_size=None)["account_size"] is None


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"name": ""}, 422),
        ({"name": "   "}, 422),  # blank once trimmed: the domain refuses it in words
        ({"firm": "Nowhere Capital"}, 422),
        ({"account_type": "demo"}, 422),
        ({"starting_balance": "-1.00"}, 422),
        ({"account_size": "1000000000000.00"}, 422),  # more than the bound: refused before the database can choke
        ({"starting_balance": 50000}, 422),  # money is a string, never a JSON number
        ({"nonsense": "x"}, 422),  # a misspelled field is an error
    ],
)
def test_bad_account_details_are_refused_and_nothing_is_added(db, alice, changes, status):
    client = api(db, alice)
    response = client.post("/api/v1/accounts", json={**DETAILS, **changes})
    assert response.status_code == status and response.headers["content-type"] == "application/problem+json"
    assert names(client) == []


def test_two_accounts_cannot_share_a_name_in_any_capitalization_but_two_people_can(db, alice, bob):
    client = api(db, alice)
    make(client)
    clash = client.post("/api/v1/accounts", json={**DETAILS, "name": "APEX 11"})
    assert clash.status_code == 409 and clash.json()["detail"] == "You already have an account with that name."
    assert names(client) == ["Apex 11"]
    assert make(api(db, bob))["name"] == "Apex 11"  # another person's journal is a separate namespace


def test_changing_the_details_keeps_the_rules_and_a_type_change_reclassifies_the_trades(db, alice):
    client = api(db, alice)
    account = make(client)
    with db.tenant_session(alice) as conn:
        accounts.set_fee(conn, account["id"], "*", Decimal("4.00"))
        importer.import_files(conn, account["id"], [(SAMPLE, fixture_bytes(SAMPLE))])
    client.put(f"/api/v1/accounts/{account['id']}/rules", json={**NO_RULES, "profit_target": "3000.00"})
    changed = client.put(
        f"/api/v1/accounts/{account['id']}",
        json={**DETAILS, "name": "Apex 11 funded", "account_type": "funded", "firm": "Lucid"},
    )
    assert changed.status_code == 200
    body = changed.json()
    assert (body["name"], body["account_type"], body["firm"]) == ("Apex 11 funded", "funded", "Lucid")
    assert body["profit_target"] == "3000.00"  # the rules are edited elsewhere and survive
    funded = client.get("/api/v1/trades", params={"account_type": "funded", "limit": 200}).json()
    assert funded["total"] == 8  # the trades took the account's new type
    same_name = client.put(f"/api/v1/accounts/{account['id']}", json={**DETAILS, "name": "apex 11 FUNDED"})
    assert same_name.status_code == 200  # an account may differ from itself only by capitalization


def test_renaming_onto_another_accounts_name_is_refused_and_an_archived_outcome_must_still_fit(db, alice):
    client = api(db, alice)
    first, second = make(client), make(client, name="Apex 12")
    assert client.put(f"/api/v1/accounts/{second['id']}", json={**DETAILS, "name": "apex 11"}).status_code == 409
    client.post(
        f"/api/v1/accounts/{first['id']}/archive", json={"outcome": "passed", "ended_on": "2024-03-01", "note": ""}
    )
    refused = client.put(f"/api/v1/accounts/{first['id']}", json={**DETAILS, "account_type": "funded"})
    assert refused.status_code == 422 and "restore it first" in refused.json()["detail"]


def test_a_new_account_needs_its_purchase_date_and_takes_a_phase_that_fits_its_type(db, alice):
    client = api(db, alice)
    for missing in ({k: v for k, v in DETAILS.items() if k != "purchase_date"}, {**DETAILS, "purchase_date": None}):
        refused = client.post("/api/v1/accounts", json=missing)
        assert refused.status_code == 422 and refused.json()["errors"][0]["field"] == "purchase_date"
    created = make(client)
    assert (created["purchase_date"], created["phase"], created["pass_prompt_dismissed_at"]) == (
        "2026-01-05",
        "evaluation",
        None,
    )
    assert make(client, name="Lucid Live", account_type="funded", phase="live")["phase"] == "live"
    wrong = client.post("/api/v1/accounts", json={**DETAILS, "name": "X", "phase": "live"})
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == "The phase must be evaluation for evaluation accounts."
    future = client.post("/api/v1/accounts", json={**DETAILS, "name": "Y", "purchase_date": "2999-01-01"})
    assert future.status_code == 422 and future.json()["detail"] == "The purchase date cannot be in the future."
    assert names(client) == ["Apex 11", "Lucid Live"]


def test_changing_the_details_sets_or_clears_the_purchase_date_and_phase(db, alice):
    client = api(db, alice)
    account = make(client, account_type="funded")
    url = f"/api/v1/accounts/{account['id']}"
    changed = client.put(url, json={**DETAILS, "account_type": "funded", "purchase_date": None, "phase": "live"})
    assert (changed.json()["purchase_date"], changed.json()["phase"]) == (None, "live")
    back = client.put(url, json={**DETAILS, "account_type": "funded"}).json()
    assert (back["purchase_date"], back["phase"]) == ("2026-01-05", "funded")
    # Both are required keys (null allowed), so a client that does not know them cannot clear them by leaving them out.
    for key in ("purchase_date", "phase"):
        missing = client.put(url, json={k: v for k, v in {**DETAILS, "account_type": "funded"}.items() if k != key})
        assert missing.status_code == 422 and missing.json()["errors"][0]["field"] == key


# ---- rules ----------------------------------------------------------------------------------------------------------


def test_rules_are_set_cleared_and_kept_consistent(db, alice):
    client = api(db, alice)
    account = make(client)
    url = f"/api/v1/accounts/{account['id']}/rules"
    full = {
        "profit_target": "3000.00",
        "max_drawdown": "2500.00",
        "trailing_type": "eod",
        "trail_lock_enabled": True,
        "trail_lock_level": "52600.00",
        "daily_loss_limit": "1000.00",
    }
    body = client.put(url, json=full).json()
    assert {k: body[k] for k in full} == full
    # Switching the lock off drops its level, so the stored rules never contradict each other.
    off = client.put(url, json={**full, "trail_lock_enabled": False}).json()
    assert off["trail_lock_enabled"] is False and off["trail_lock_level"] is None and off["max_drawdown"] == "2500.00"
    cleared = client.put(url, json=NO_RULES).json()
    assert all(cleared[k] is None for k in ("profit_target", "max_drawdown", "trailing_type", "daily_loss_limit"))
    lock_without_level = client.put(url, json={**NO_RULES, "trail_lock_enabled": True})
    assert (
        lock_without_level.status_code == 422
        and "level at which the trailing stops" in lock_without_level.json()["detail"]
    )
    assert client.put(url, json={**full, "trailing_type": "weekly"}).status_code == 422
    assert client.put(url, json={**full, "profit_target": "-5.00"}).status_code == 422
    assert (
        client.put(url, json={"profit_target": "1.00"}).status_code == 422
    )  # every rule must be stated (null or a value)


# ---- archive and restore --------------------------------------------------------------------------------------------


def test_an_account_is_archived_with_an_outcome_that_fits_its_type_and_can_be_restored(db, alice):
    client = api(db, alice)
    evaluation = make(client)
    funded = make(client, name="Lucid 01", firm="Lucid", account_type="funded")
    archive = f"/api/v1/accounts/{evaluation['id']}/archive"
    done = client.post(archive, json={"outcome": "passed", "ended_on": "2024-03-01", "note": "  Hit the target  "})
    assert done.status_code == 200
    assert (done.json()["outcome"], done.json()["ended_on"], done.json()["archive_note"]) == (
        "passed",
        "2024-03-01",
        "Hit the target",
    )
    cannot = client.post(
        f"/api/v1/accounts/{funded['id']}/archive", json={"outcome": "passed", "ended_on": "2024-03-01", "note": ""}
    )
    assert cannot.status_code == 422 and "ends as Failed or Closed" in cannot.json()["detail"]
    assert client.post(archive, json={"outcome": "retired", "ended_on": "2024-03-01", "note": ""}).status_code == 422
    assert client.post(archive, json={"outcome": "failed", "ended_on": "March", "note": ""}).status_code == 422
    assert [a["name"] for a in client.get("/api/v1/accounts", params={"include_archived": "false"}).json()] == [
        "Lucid 01"
    ]
    restored = client.post(f"/api/v1/accounts/{evaluation['id']}/restore").json()
    assert restored["outcome"] is None and restored["ended_on"] is None and restored["archive_note"] == ""


# ---- fees -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def traded(db, alice):
    """An account with the sample export imported at $4.00 a contract (8 trades, 50 contracts, net 835.00)."""
    client = api(db, alice)
    account = make(client)
    with db.tenant_session(alice) as conn:
        accounts.set_fee(conn, account["id"], "*", Decimal("4.00"))
        importer.import_files(conn, account["id"], [(SAMPLE, fixture_bytes(SAMPLE))])
    return client, account["id"]


def net(client) -> str:
    return client.get("/api/v1/dashboard").json()["summary"]["net_pnl"]


def test_fees_are_listed_as_written_and_changing_one_recalculates_the_stored_trades(traded):
    client, account_id = traded
    assert client.get(f"/api/v1/accounts/{account_id}/fees").json() == {"*": "4.00"}
    assert net(client) == "835.00"
    changed = client.put(f"/api/v1/accounts/{account_id}/fees/*", json={"rate": "1.00"})
    assert changed.status_code == 200 and changed.json() == {"*": "1.00"}
    assert net(client) == "985.00"  # 1,035.00 gross less 50 contracts at $1.00
    root = client.get("/api/v1/trades", params={"limit": 1}).json()["items"][0]["symbol_root"]
    own = client.put(f"/api/v1/accounts/{account_id}/fees/{root.lower()}", json={"rate": "0.625"}).json()
    assert own == {"*": "1.00", root: "0.625"}  # a root is stored in capitals; more than cents only when it has them
    # Each trade is charged its own contracts at its own rate, rounded to cents trade by trade.
    for trade in client.get("/api/v1/trades", params={"limit": 200}).json()["items"]:
        rate = Decimal("0.625") if trade["symbol_root"] == root else Decimal("1.00")
        assert Decimal(trade["fees"]) == to_money(trade["total_qty"] * rate), trade["symbol_root"]
    gone = client.delete(f"/api/v1/accounts/{account_id}/fees/{root}")
    assert gone.json() == {"*": "1.00"} and net(client) == "985.00"


@pytest.mark.parametrize(
    ("root", "body", "status"),
    [
        ("*", {"rate": "-1.00"}, 422),
        ("*", {"rate": 4}, 422),
        ("*", {"rate": "10001.00"}, 422),
        ("*", {}, 422),
        ("N Q", {"rate": "1.00"}, 422),
        ("TOOLONGROOT123", {"rate": "1.00"}, 422),
    ],
)
def test_a_bad_fee_is_refused_and_changes_nothing(traded, root, body, status):
    client, account_id = traded
    assert client.put(f"/api/v1/accounts/{account_id}/fees/{root}", json=body).status_code == status
    assert client.get(f"/api/v1/accounts/{account_id}/fees").json() == {"*": "4.00"} and net(client) == "835.00"


# ---- the ledger -----------------------------------------------------------------------------------------------------


def test_the_ledger_records_costs_and_payouts_oldest_first_and_entries_can_be_deleted(db, alice):
    client = api(db, alice)
    account = make(client)
    base = f"/api/v1/accounts/{account['id']}/ledger"
    assert client.get(base).json() == []
    payout = client.post(
        base, json={"kind": "payout", "amount": "1500.00", "entry_date": "2024-03-20", "note": " March "}
    )
    cost = client.post(
        base, json={"kind": "evaluation_fee", "amount": "167.00", "entry_date": "2024-03-01", "note": ""}
    )
    assert payout.status_code == 201 and payout.json()["amount"] == "1500.00" and payout.json()["note"] == "March"
    assert [e["kind"] for e in client.get(base).json()] == ["evaluation_fee", "payout"]
    left = client.delete(f"{base}/{cost.json()['id']}")
    assert left.status_code == 200 and [e["kind"] for e in left.json()] == ["payout"]
    assert client.delete(f"{base}/{cost.json()['id']}").status_code == 404  # already gone


@pytest.mark.parametrize(
    "entry",
    [
        {"kind": "payout", "amount": "0.00", "entry_date": "2024-03-20", "note": ""},
        {"kind": "payout", "amount": "-5.00", "entry_date": "2024-03-20", "note": ""},
        {"kind": "bribe", "amount": "5.00", "entry_date": "2024-03-20", "note": ""},
        {"kind": "payout", "amount": "1000000000000.00", "entry_date": "2024-03-20", "note": ""},
        {"kind": "payout", "amount": "5.00", "entry_date": "20 March", "note": ""},
        {"kind": "payout", "amount": "5.00", "entry_date": "2024-03-20", "note": "x" * 501},
        {"kind": "payout", "amount": 5, "entry_date": "2024-03-20", "note": ""},
    ],
)
def test_a_bad_ledger_entry_is_refused_and_not_recorded(db, alice, entry):
    client = api(db, alice)
    account = make(client)
    assert client.post(f"/api/v1/accounts/{account['id']}/ledger", json=entry).status_code == 422
    assert client.get(f"/api/v1/accounts/{account['id']}/ledger").json() == []


def test_an_entry_can_only_be_deleted_through_its_own_account(db, alice):
    client = api(db, alice)
    one, two = make(client), make(client, name="Apex 12")
    entry = client.post(
        f"/api/v1/accounts/{one['id']}/ledger",
        json={"kind": "payout", "amount": "5.00", "entry_date": "2024-03-20", "note": ""},
    ).json()
    assert client.delete(f"/api/v1/accounts/{two['id']}/ledger/{entry['id']}").status_code == 404
    assert len(client.get(f"/api/v1/accounts/{one['id']}/ledger").json()) == 1


# ---- whose accounts these are ---------------------------------------------------------------------------------------


def test_nobody_can_read_or_change_another_persons_account(db, alice, bob):
    mine = make(api(db, alice))
    with db.tenant_session(alice) as conn:
        accounts.set_fee(conn, mine["id"], "*", Decimal("4.00"))
    as_bob = api(db, bob)
    base = f"/api/v1/accounts/{mine['id']}"
    attempts = [
        ("put", base, DETAILS),
        ("put", f"{base}/rules", NO_RULES),
        ("post", f"{base}/archive", {"outcome": "failed", "ended_on": "2024-03-01", "note": ""}),
        ("post", f"{base}/restore", None),
        ("get", f"{base}/fees", None),
        ("put", f"{base}/fees/*", {"rate": "0.01"}),
        ("delete", f"{base}/fees/*", None),
        ("get", f"{base}/ledger", None),
        ("post", f"{base}/ledger", {"kind": "payout", "amount": "5.00", "entry_date": "2024-03-20", "note": ""}),
        ("delete", f"{base}/ledger/1", None),
    ]
    for method, url, body in attempts:
        response = getattr(as_bob, method)(url, **({"json": body} if body is not None else {}))
        assert response.status_code == 404, (method, url, response.status_code)
        assert response.json()["detail"] in ("That account does not exist.", "That ledger entry does not exist.")
    assert names(as_bob) == []
    after = api(db, alice).get("/api/v1/accounts").json()[0]
    assert after["name"] == "Apex 11" and after["profit_target"] is None and after["outcome"] is None
    assert api(db, alice).get(f"{base}/fees").json() == {"*": "4.00"}


def test_a_change_without_the_csrf_token_adds_nothing(db, alice):
    client = api(db, alice)
    assert client.post("/api/v1/accounts", json=DETAILS, headers={"x-csrf-token": "wrong"}).status_code == 403
    assert names(client) == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", f"/api/v1/accounts/{TOO_BIG}", DETAILS),
        ("put", f"/api/v1/accounts/{TOO_BIG}/rules", NO_RULES),
        ("post", f"/api/v1/accounts/{TOO_BIG}/restore", None),
        ("get", f"/api/v1/accounts/{TOO_BIG}/fees", None),
        ("put", f"/api/v1/accounts/{TOO_BIG}/fees/*", {"rate": "1.00"}),
        ("get", f"/api/v1/accounts/{TOO_BIG}/ledger", None),
        ("delete", f"/api/v1/accounts/1/ledger/{TOO_BIG}", None),
        ("get", "/api/v1/accounts/0/ledger", None),
    ],
)
def test_an_id_too_big_for_the_database_is_a_bad_request(db, alice, method, path, body):
    response = getattr(api(db, alice), method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == 422, (path, response.status_code)


def test_an_account_made_by_the_database_helper_is_listed_like_one_made_through_the_api(db, alice):
    with db.tenant_session(alice) as conn:
        make_account(conn, "From a script", firm="Tradeify")
    assert names(api(db, alice)) == ["From a script"]


# ---- progress and the business ---------------------------------------------------------------------------------------


def test_the_overviews_work_out_progress_from_the_accounts_closed_trades(traded):
    client, account_id = traded
    client.put(
        f"/api/v1/accounts/{account_id}/rules",
        json={**NO_RULES, "profit_target": "3000.00", "max_drawdown": "2500.00"},
    )
    empty = make(client, name="Apex 12", firm="Apex")
    overviews = {o["account"]["id"]: o["progress"] for o in client.get("/api/v1/account-overviews").json()}
    progress = overviews[account_id]
    assert progress["net_pnl"] == "835.00" and progress["trade_count"] == 8
    assert progress["target_progress"] == pytest.approx(835 / 3000)
    # Cross-check against the dashboard's own drawdown series: the last point is how far below its peak it ended.
    series = client.get("/api/v1/dashboard").json()["equity_by_trade"]
    assert Decimal(progress["drawdown_left"]) == Decimal("2500.00") + Decimal(series[-1]["drawdown"])
    assert progress["last_trading_day"] == "2024-01-05"
    assert overviews[empty["id"]]["trade_count"] == 0 and overviews[empty["id"]]["last_trading_day"] is None
    archived = client.post(
        f"/api/v1/accounts/{account_id}/archive", json={"outcome": "passed", "ended_on": "2024-01-06", "note": ""}
    )
    assert archived.status_code == 200
    assert account_id in {
        o["account"]["id"] for o in client.get("/api/v1/account-overviews").json()
    }  # archived ones stay


def test_the_business_totals_follow_the_ledger_and_how_evaluations_ended(db, alice, bob):
    client = api(db, alice)
    one, two = make(client), make(client, name="Apex 12")
    client.post(
        f"/api/v1/accounts/{one['id']}/ledger",
        json={"kind": "evaluation_fee", "amount": "167.00", "entry_date": "2024-03-01", "note": ""},
    )
    client.post(
        f"/api/v1/accounts/{one['id']}/ledger",
        json={"kind": "payout", "amount": "1500.00", "entry_date": "2024-04-01", "note": ""},
    )
    client.post(
        f"/api/v1/accounts/{one['id']}/archive", json={"outcome": "passed", "ended_on": "2024-03-30", "note": ""}
    )
    client.post(
        f"/api/v1/accounts/{two['id']}/archive", json={"outcome": "failed", "ended_on": "2024-03-30", "note": ""}
    )
    totals = client.get("/api/v1/business-totals").json()
    assert (totals["evaluations_passed"], totals["evaluations_failed"], totals["pass_rate"]) == (1, 1, 0.5)
    assert (totals["costs"], totals["payouts"], totals["net"]) == ("167.00", "1500.00", "1333.00")
    assert totals["accounts_without_cost"] == ["Apex 12"]
    nothing = api(db, bob).get("/api/v1/business-totals").json()  # another person's journal is separate
    assert nothing["costs"] == "0.00" and nothing["accounts_without_cost"] == [] and nothing["pass_rate"] is None
    assert api(db, bob).get("/api/v1/account-overviews").json() == []
