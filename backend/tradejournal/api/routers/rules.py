"""Prop-firm rules for the signed-in person: the firms' templates, each account's verified rule set, payouts with gross
and split, the evaluation-passed prompt, and the Accounts tab's overview (migration 0019, `docs/prop-rules/PLAN.md`)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ...finance import ZERO
from ...pg import accounts as pg_accounts
from ...pg import ledger as pg_ledger
from ...pg import queries as pg_queries
from ...pg import rules as pg_rules
from ...queries import TradeFilter
from ..accounts_view import account_out, entry_out
from ..deps import ERROR_RESPONSES, ClockDep, TenantDep, WriteTenantDep
from ..overview import overview
from ..refusals import refusals
from ..rules_view import (
    checked_rules,
    payouts_out,
    rule_fields,
    rule_set_out,
    rules_split,
    template_out,
    template_summary,
)
from ..schemas import Problem
from ..schemas.accounts import LedgerEntryOut
from ..schemas.journal import AccountOut, PathId
from ..schemas.rules import (
    AccountsOverviewOut,
    PayoutIn,
    PayoutsOut,
    RuleFieldsOut,
    RuleSetIn,
    RuleSetOut,
    RuleTemplateOut,
    RuleTemplatesOut,
)

router = APIRouter(prefix="/api/v1", tags=["rules"], responses={**ERROR_RESPONSES, 404: {"model": Problem}})

NO_SPLIT = "This account's rules give no profit split: enter the split."


# ---- the Accounts tab ---------------------------------------------------------------------------------------------------


@router.get("/accounts/overview", response_model=AccountsOverviewOut)
def get_accounts_overview(tenant: TenantDep, clock: ClockDep) -> AccountsOverviewOut:
    """Every active account against its verified rules (status, breach-to-payout bar, payout readiness, consistency,
    inactivity, each rule's result) and the totals. Closed trades only: see `compliance.py` for what that means."""
    with tenant.session(snapshot=True) as conn:  # one moment across accounts, trades, payouts and rules
        accounts = pg_accounts.list_accounts(conn, include_archived=False)
        ids = tuple(a.id for a in accounts)
        trades = pg_queries.load_trades(conn, TradeFilter(account_ids=ids)) if ids else []
        entries = pg_ledger.entries(conn)
        rule_sets = pg_rules.all_active(conn)
    return overview(accounts, {k: r.rules for k, r in rule_sets.items()}, trades, entries, clock())


@router.post("/accounts/{account_id}/pass-prompt/dismiss", response_model=AccountOut)
def dismiss_pass_prompt(account_id: PathId, tenant: WriteTenantDep) -> AccountOut:
    """Stop offering "Evaluation passed: set up your funded account" on the Accounts tab. The account's settings still
    offer it."""
    with refusals(what="account"), tenant.session() as conn:
        account = pg_accounts.dismiss_pass_prompt(conn, account_id)
    return account_out(account)


# ---- the firms' templates -----------------------------------------------------------------------------------------------


@router.get("/rule-fields", response_model=RuleFieldsOut)
def get_rule_fields(_: TenantDep) -> RuleFieldsOut:
    """Every rule field (name, kind, group, label, choices, hint) in the form's order, and the groups in order."""
    return rule_fields()


@router.get("/rule-templates", response_model=RuleTemplatesOut)
def list_rule_templates(
    tenant: TenantDep,
    purchased_on: Annotated[date | None, Query(description="The account's purchase date")] = None,
) -> RuleTemplatesOut:
    """The firms' rule sets that can apply to an account bought on `purchased_on`: the versions in force by then, and
    those the research could not date. The latest version of each firm + program + size + phase is preselected. Without
    a date every version is listed and none is preselected."""
    with tenant.session() as conn:
        offered = pg_rules.offered_templates(conn, purchased_on)
    return RuleTemplatesOut(templates=[template_summary(o) for o in offered])


@router.get("/rule-templates/{template_id}", response_model=RuleTemplateOut)
def get_rule_template(template_id: PathId, tenant: TenantDep) -> RuleTemplateOut:
    """One firm's rule set in full, with what the research could not confirm (`flags`) and the source's own words for
    cells that were not a clean value (`source_notes`)."""
    with tenant.session() as conn:
        template = pg_rules.template(conn, template_id)
    if template is None:
        raise HTTPException(404, "That rule template does not exist.")
    return template_out(template)


# ---- an account's verified rule set -----------------------------------------------------------------------------------


@router.get("/accounts/{account_id}/rule-set", response_model=RuleSetOut | None)
def get_rule_set(account_id: PathId, tenant: TenantDep) -> RuleSetOut | None:
    """The rules the person verified for this account; null when there are none yet."""
    with refusals(what="account"), tenant.session() as conn:
        pg_accounts.get_account(conn, account_id)
        active = pg_rules.active(conn, account_id)
    return None if active is None else rule_set_out(active)


@router.put("/accounts/{account_id}/rule-set", response_model=RuleSetOut)
def put_rule_set(account_id: PathId, body: RuleSetIn, tenant: WriteTenantDep) -> RuleSetOut:
    """Verify the account's rules: every value is copied in, so a later change to the template never changes them. The
    previous rule set is kept as history. Each field that is wrong is named in the 422's `errors` (`values.<field>`)."""
    with refusals(what="account"), tenant.session() as conn:
        pg_accounts.get_account(conn, account_id)  # someone else's account is a 404 before anything is checked
        rules = checked_rules(body.values, body.flags, body.source_notes, whole=True)
        if body.source_template_id is not None and pg_rules.template(conn, body.source_template_id) is None:
            raise HTTPException(422, "That rule template does not exist.")
        saved = pg_rules.save(conn, account_id, rules, source_template_id=body.source_template_id)
    return rule_set_out(saved)


# ---- payouts ------------------------------------------------------------------------------------------------------------


@router.get("/accounts/{account_id}/payouts", response_model=PayoutsOut)
def get_payouts(account_id: PathId, tenant: TenantDep) -> PayoutsOut:
    """The account's payouts, oldest first, with their numbers and the lifetime totals, and the split its rules promise
    (the default for the next one)."""
    with refusals(what="account"), tenant.session() as conn:
        pg_accounts.get_account(conn, account_id)
        entries = pg_ledger.payouts(conn, account_id)
        active = pg_rules.active(conn, account_id)
    return payouts_out(entries, active)


@router.post("/accounts/{account_id}/payouts", response_model=LedgerEntryOut, status_code=201)
def add_payout(account_id: PathId, body: PayoutIn, tenant: WriteTenantDep) -> LedgerEntryOut:
    """Log a payout: the gross the firm paid out and the trader's split (net = gross x split, to the cent). Without a
    split, the one in the account's rules is used; a fee is counted as a cost of the business."""
    with refusals(what="account"), tenant.session() as conn:
        pg_accounts.get_account(conn, account_id)
        split = body.split_pct if body.split_pct is not None else rules_split(pg_rules.active(conn, account_id))
        if split is None:
            raise ValueError(NO_SPLIT)
        entry = pg_ledger.add_payout(
            conn,
            account_id,
            entry_date=body.entry_date,
            gross=body.gross,
            split_pct=split,
            fee=body.fee if body.fee is not None else ZERO,
            note=body.note,
        )
    return entry_out(entry)
