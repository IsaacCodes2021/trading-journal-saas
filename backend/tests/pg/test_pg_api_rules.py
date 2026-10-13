"""The prop-firm rules routes over HTTP, on a real Postgres: the rule fields, the picker, verified rule sets, payouts, the
Accounts tab, the evaluation-passed prompt, and the operator's templates (migration 0019)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pg_helpers import add_trade, make_account
from pg_helpers import api_client as api
from test_compliance import APEX_EOD_50K_EVAL, LUCID_DAYS, LUCID_PRO_50K
from test_pg_admin_codes import admin_events, operator_browser, writes
from test_prop_rules import ROW, csv_text

from tradejournal import prop_rules as pr
from tradejournal.api.ports import AdminRefusal
from tradejournal.pg import ledger, rule_templates
from tradejournal.pg import rules as pg_rules
from tradejournal.pg.accounts import archive_account
from tradejournal.prop_rules import RuleValues

D = Decimal
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)  # noon in New York, the engine tests' "now"
NAMED = {"firm": "Apex", "program": "EOD", "account_size": "50K", "phase": "funded", "rule_version": "v1"}
ROW_FLAGS = [
    {"field": "activation_fee_usd", "reason": "conflict: ETF says $99; PTV says $129", "kind": "conflict"},
    {"field": "drawdown_after_payout", "reason": "unknown: not stated", "kind": "unknown"},
    {"field": "flat_by_time", "reason": "third-party only (Tanto)", "kind": "third-party only"},
]


def save_rules(conn, account_id: int, values: dict):
    return pg_rules.save(conn, account_id, RuleValues(values=dict(values)), source_template_id=None)


def rule_set_body(**changes) -> dict:
    return {"values": NAMED, "flags": [], "source_notes": {}, "source_template_id": None, **changes}


# ---- the fields and the picker ------------------------------------------------------------------------------------


def test_the_rule_fields_come_in_the_forms_order_with_their_groups_and_choices(db, alice):
    body = api(db, alice).get("/api/v1/rule-fields").json()
    assert [f["name"] for f in body["fields"]] == list(pr.FIELD_NAMES) and body["groups"] == list(pr.GROUPS)
    drawdown = next(f for f in body["fields"] if f["name"] == "drawdown_type")
    assert (drawdown["kind"], drawdown["group"], drawdown["choices"]) == ("choice", "Drawdown", list(pr.DRAWDOWN_TYPES))


def test_the_picker_offers_versions_by_purchase_date_and_a_template_comes_in_full(owner, db, alice):
    legacy_row = {**ROW, "rule_version": "legacy", "effective_from": "before 2026-03-01"}
    rule_templates.import_csv(owner, csv_text(ROW, legacy_row), by="seed")
    client = api(db, alice)
    picked = {
        t["rule_version"]: t for t in client.get("/api/v1/rule-templates?purchased_on=2026-04-01").json()["templates"]
    }
    current, legacy = picked["Apex 4.0 (new accounts)"], picked["legacy"]
    assert (current["preselect"], legacy["preselect"]) == (True, False)
    assert (current["effective_from"], current["flagged"]) == ("2026-03-01", 3)
    assert (legacy["effective_from"], legacy["effective_from_note"]) == (None, "before 2026-03-01")
    early = client.get("/api/v1/rule-templates", params={"purchased_on": "2026-02-01"}).json()["templates"]
    assert [(t["rule_version"], t["preselect"]) for t in early] == [("legacy", True)]  # the current one was not out yet

    full = client.get(f"/api/v1/rule-templates/{current['id']}").json()
    values = full["values"]
    assert set(values) == set(pr.FIELD_NAMES)
    assert (values["max_loss_usd"], values["max_contracts_minis"], values["consistency_pct"]) == ("2000.00", 4, "50.00")
    assert (values["effective_from"], values["price_usd"], values["activation_fee_usd"]) == ("2026-03-01", None, None)
    assert full["flags"] == ROW_FLAGS
    assert full["source_notes"] == {"price_usd": "79.99 (59.99 with DLL add-on)"}
    missing = client.get("/api/v1/rule-templates/999")
    assert missing.status_code == 404 and missing.json()["detail"] == "That rule template does not exist."


# ---- a verified rule set --------------------------------------------------------------------------------------------


def test_verifying_rules_copies_the_template_with_its_flags_and_notes_and_a_new_one_replaces_it(owner, db, alice):
    rule_templates.import_csv(owner, csv_text(ROW), by="seed")
    client = api(db, alice)
    [summary] = client.get("/api/v1/rule-templates").json()["templates"]
    template = client.get(f"/api/v1/rule-templates/{summary['id']}").json()
    with db.tenant_session(alice) as conn:
        account = make_account(conn, account_type="funded", purchase_date=date(2026, 4, 1))
    url = f"/api/v1/accounts/{account.id}/rule-set"
    assert client.get(url).json() is None

    body = {
        "values": {**template["values"], "max_loss_usd": "1900.00"},  # the person checked the firm's page
        "flags": [{"field": f["field"], "reason": f["reason"]} for f in template["flags"]],
        "source_notes": template["source_notes"],
        "source_template_id": template["id"],
    }
    saved = client.put(url, json=body)
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["values"] == {**template["values"], "max_loss_usd": "1900.00"}
    assert (out["flags"], out["source_notes"], out["source_template_id"]) == (
        ROW_FLAGS,
        template["source_notes"],
        template["id"],
    )
    assert client.get(url).json() == out

    again = client.put(url, json={**body, "values": {**body["values"], "max_loss_usd": "2000.00"}}).json()
    assert again["id"] != out["id"] and client.get(url).json()["values"]["max_loss_usd"] == "2000.00"
    with db.tenant_session(alice) as conn:
        assert len(pg_rules.history(conn, account.id)) == 2


@pytest.mark.parametrize(
    ("change", "errors"),
    [
        ({"values": {**NAMED, "max_loss_usd": "-5"}}, {"values.max_loss_usd": "Max loss cannot be negative."}),
        ({"values": {**NAMED, "max_loss_usd": 2000}}, {"values.max_loss_usd": "Max loss must be a number."}),
        (
            {"values": {**NAMED, "drawdown_type": "static"}},
            {"values.max_loss_usd": "Give the max loss, or clear the drawdown type."},
        ),
        (
            {"values": {**NAMED, "drawdown_type": "static", "max_loss_usd": "-5"}},
            {"values.max_loss_usd": "Max loss cannot be negative."},  # the kind is wrong: not "give the max loss"
        ),
        ({"values": {**NAMED, "firm": ""}}, {"values.firm": "Firm is required."}),
        ({"values": {**NAMED, "lunch_budget": "20"}}, {"values.lunch_budget": "Not a rule field."}),
        ({"flags": [{"field": "lunch_budget", "reason": "unknown"}]}, {"flags.0.field": "Not a rule field."}),
        ({"source_notes": {"lunch_budget": "x"}}, {"source_notes.lunch_budget": "Not a rule field."}),
    ],
)
def test_a_rule_set_that_does_not_make_sense_is_refused_field_by_field_and_nothing_is_kept(db, alice, change, errors):
    client = api(db, alice)
    with db.tenant_session(alice) as conn:
        account = make_account(conn)
    url = f"/api/v1/accounts/{account.id}/rule-set"
    response = client.put(url, json=rule_set_body(**change))
    assert response.status_code == 422 and response.headers["content-type"] == "application/problem+json"
    assert {e["field"]: e["message"] for e in response.json()["errors"]} == errors
    assert client.get(url).json() is None


def test_a_rule_set_cannot_name_a_template_that_does_not_exist(db, alice):
    client = api(db, alice)
    with db.tenant_session(alice) as conn:
        account = make_account(conn)
    response = client.put(f"/api/v1/accounts/{account.id}/rule-set", json=rule_set_body(source_template_id=999))
    assert response.status_code == 422 and response.json()["detail"] == "That rule template does not exist."


# ---- payouts ------------------------------------------------------------------------------------------------------


def test_a_payout_takes_the_split_from_the_rules_unless_given_and_the_history_adds_up(db, alice):
    client = api(db, alice, now=NOW)
    with db.tenant_session(alice) as conn:
        account = make_account(conn, account_type="funded")
    url = f"/api/v1/accounts/{account.id}/payouts"
    body = {"entry_date": "2026-10-01", "gross": "1000.00", "split_pct": None, "fee": None, "note": ""}
    refused = client.post(url, json=body)
    assert refused.status_code == 422
    assert refused.json()["detail"] == "This account's rules give no profit split: enter the split."
    for bad in ("0", "100.01", 90):
        assert client.post(url, json={**body, "split_pct": bad}).status_code == 422

    with db.tenant_session(alice) as conn:
        save_rules(conn, account.id, LUCID_PRO_50K)  # 90/10
    first = client.post(url, json=body)
    assert first.status_code == 201, first.text
    first = first.json()
    assert (first["amount"], first["gross_amount"], first["profit_split_pct"], first["payout_fee"]) == (
        "900.00",
        "1000.00",
        "90.00",
        "0.00",
    )
    second = client.post(url, json={**body, "entry_date": "2026-10-08", "split_pct": "80", "fee": "25.00"}).json()
    assert (second["amount"], second["payout_number"], first["payout_number"]) == ("800.00", 2, 1)

    history = client.get(url).json()
    assert [p["id"] for p in history["payouts"]] == [first["id"], second["id"]]
    assert (history["split_pct"], history["next_number"]) == ("90.00", 3)
    assert (history["lifetime_gross"], history["lifetime_net"], history["lifetime_fees"]) == (
        "2000.00",
        "1700.00",
        "25.00",
    )
    assert [e["kind"] for e in client.get(f"/api/v1/accounts/{account.id}/ledger").json()] == ["payout", "payout"]
    totals = client.get("/api/v1/business-totals").json()
    assert (totals["payouts"], totals["costs"]) == ("1700.00", "25.00")  # the fee is a cost of the business


# ---- the Accounts tab -----------------------------------------------------------------------------------------------


def test_the_accounts_tab_shows_each_active_account_against_its_rules_and_adds_up_the_ready_payouts(db, alice):
    with db.tenant_session(alice) as conn:
        lucid = make_account(conn, "Lucid PA", firm="Lucid", account_type="funded", purchase_date=date(2026, 9, 1))
        save_rules(conn, lucid.id, {**LUCID_PRO_50K, "payout_cap_schedule": "all:1500"})  # the person's own cap
        for day, net in LUCID_DAYS:
            add_trade(conn, lucid.id, day, net)
        plain = make_account(conn, "Plain", account_type="funded", starting_balance=D("25000"))
        add_trade(conn, plain.id, date(2026, 10, 1), "250")
        ledger.add_payout(conn, plain.id, entry_date=date(2026, 10, 2), gross=D("100"), split_pct=D("80"))
        gone = make_account(conn, "Gone", account_type="funded")
        archive_account(conn, gone.id, outcome="closed", ended_on=date(2026, 10, 1))
    body = api(db, alice, now=NOW).get("/api/v1/accounts/overview").json()
    rows = {r["account"]["name"]: r for r in body["accounts"]}
    assert list(rows) == ["Lucid PA", "Plain"]  # archived accounts are not on the tab

    ready = rows["Lucid PA"]
    assert (ready["status"], ready["rules_set"], ready["program"], ready["phase"]) == (
        "payout_ready",
        True,
        "LucidPro",
        "funded",
    )
    assert (ready["balance"], ready["net_pnl"], ready["floor"]) == ("54000.00", "4000.00", "50100.00")
    payout = ready["payout"]
    assert payout["ready"] and (payout["gross"], payout["net"], payout["split_pct"]) == ("1500.00", "1350.00", "90.00")
    assert {"drawdown", "consistency", "payout_cap"} <= {r["key"] for r in ready["rules"]}
    assert ready["spectrum"]["target_kind"] == "payout" and ready["pass_prompt"] is False

    unruled = rows["Plain"]
    assert (unruled["rules_set"], unruled["status"], unruled["rules"], unruled["payout"]) == (False, None, [], None)
    assert (unruled["balance"], unruled["net_pnl"]) == ("25150.00", "250.00")  # 25,000 + 250 - the 100 paid out
    assert (unruled["withdrawn_gross"], unruled["withdrawn_net"]) == ("100.00", "80.00")
    assert body["totals"] == {
        "balance": "79150.00",
        "net_pnl": "4250.00",
        "withdrawable_gross": "1500.00",
        "withdrawable_net": "1350.00",
        "withdrawn_gross": "100.00",
        "withdrawn_net": "80.00",
    }


def test_an_empty_journal_has_an_empty_tab(db, alice):
    body = api(db, alice).get("/api/v1/accounts/overview").json()
    assert body["accounts"] == [] and set(body["totals"].values()) == {"0.00"}


def test_a_passed_evaluation_offers_the_funded_setup_until_it_is_dismissed(db, alice):
    with db.tenant_session(alice) as conn:
        evaluation = make_account(conn, "Apex eval", purchase_date=date(2026, 9, 30))
        save_rules(conn, evaluation.id, APEX_EOD_50K_EVAL)
        for day, net in [(date(2026, 10, 1), "1200"), (date(2026, 10, 2), "900"), (date(2026, 10, 5), "1100")]:
            add_trade(conn, evaluation.id, day, net)
    client = api(db, alice, now=NOW)
    [row] = client.get("/api/v1/accounts/overview").json()["accounts"]
    assert (row["status"], row["pass_prompt"], row["spectrum"]["target_kind"]) == ("passed", True, "profit_target")

    dismissed = client.post(f"/api/v1/accounts/{evaluation.id}/pass-prompt/dismiss")
    assert dismissed.status_code == 200 and dismissed.json()["pass_prompt_dismissed_at"] is not None
    [row] = client.get("/api/v1/accounts/overview").json()["accounts"]
    assert (row["status"], row["pass_prompt"]) == ("passed", False)
    missing = client.post("/api/v1/accounts/999/pass-prompt/dismiss")
    assert missing.status_code == 404 and missing.json()["detail"] == "That account does not exist."


# ---- the operator's templates ---------------------------------------------------------------------------------------


def test_the_operator_lists_reads_and_edits_templates_and_each_edit_is_recorded(service, owner, op):
    rule_templates.import_csv(owner, csv_text(ROW, {**ROW, "account_size": "100K"}), by="seed")
    page = service.list_rule_templates(q="100k", limit=10, offset=0)
    assert page.total == 1 and page.templates[0].account_size == "100K"
    template_id = page.templates[0].id
    read = service.rule_template(template_id)
    assert (read.updated_by, read.values["max_loss_usd"], read.flags[0].kind) == ("seed", "2000.00", "conflict")

    current = rule_templates.get(owner, template_id).rules
    changed = RuleValues({**current.values, "max_loss_usd": D("2500")}, ["flat_by_time"], ["third-party only (Tanto)"])
    edited = service.edit_rule_template(op, template_id, changed)
    assert (edited.values["max_loss_usd"], edited.updated_by) == ("2500.00", f"admin:{op}")
    assert [f.field for f in edited.flags] == ["flat_by_time"]
    [event] = admin_events(owner, "admin_rule_template_edited")
    assert (event["meta"], event["actor_user_id"], event["user_id"]) == ({"template": template_id}, op, None)

    for call, status in (
        (lambda: service.rule_template(999), 404),
        (lambda: service.edit_rule_template(op, 999, changed), 404),
        (
            lambda: service.edit_rule_template(op, template_id, RuleValues({**current.values, "account_size": "50K"})),
            409,
        ),
    ):
        with pytest.raises(AdminRefusal) as refused:
            call()
        assert refused.value.status == status
    assert len(admin_events(owner, "admin_rule_template_edited")) == 1  # a refusal records nothing


def test_the_operator_imports_the_csv_and_only_an_import_that_changes_something_is_recorded(service, owner, op):
    text = csv_text(ROW, {**ROW, "phase": "sim"})
    first = service.import_rule_templates(op, text)
    assert (first.inserted, first.updated, first.unchanged) == (1, 0, 0)
    assert first.problems == ["Line 3: has no phase it can use ('sim')."]
    again = service.import_rule_templates(op, text)
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 1)
    [event] = admin_events(owner, "admin_rule_templates_imported")
    assert event["meta"] == {"inserted": 1, "updated": 0, "unchanged": 0, "refused": 1}
    assert {t.updated_by for t in rule_templates.list_all(owner)} == {f"admin:{op}"}


def test_an_operator_imports_and_edits_templates_over_the_api(world, admin_url, owner, op):
    browser = operator_browser(world, admin_url)
    imported = browser.client.post(
        "/api/admin/v1/rule-templates/import",
        files={"file": ("rules.csv", csv_text(ROW).encode(), "text/csv")},
        headers=writes(browser),
    )
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"inserted": 1, "updated": 0, "unchanged": 0, "problems": []}
    [summary] = browser.client.get("/api/admin/v1/rule-templates").json()["templates"]
    url = f"/api/admin/v1/rule-templates/{summary['id']}"
    full = browser.client.get(url).json()

    edited = browser.client.put(
        url, json={"values": {**full["values"], "max_loss_usd": "2500.00"}, "flags": []}, headers=writes(browser)
    )
    assert edited.status_code == 200, edited.text
    assert (edited.json()["values"]["max_loss_usd"], edited.json()["flags"]) == ("2500.00", [])
    # A template keeps the research's gaps: a drawdown type with no max loss is not an error here (it is for a person).
    gap = browser.client.put(
        url, json={"values": {**full["values"], "max_loss_usd": None}, "flags": []}, headers=writes(browser)
    )
    assert gap.status_code == 200 and gap.json()["values"]["max_loss_usd"] is None
    bad = browser.client.put(
        url,
        json={"values": {**full["values"], "firm": None, "max_loss_usd": "-1"}, "flags": []},
        headers=writes(browser),
    )
    assert bad.status_code == 422
    assert {e["field"] for e in bad.json()["errors"]} == {"values.firm", "values.max_loss_usd"}

    not_text = browser.client.post(
        "/api/admin/v1/rule-templates/import",
        files={"file": ("rules.csv", b"\xff\xfe\x00bad", "text/csv")},
        headers=writes(browser),
    )
    assert not_text.status_code == 422 and not_text.json()["detail"] == "That file is not UTF-8 text."
