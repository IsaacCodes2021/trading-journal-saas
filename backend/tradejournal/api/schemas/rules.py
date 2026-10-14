"""Prop-firm rules, payouts and the Accounts tab as the API's shapes (migration 0019). Mirrors `lib/data/model.ts`.

A rule value crosses the API as its field's kind says (`GET /rule-fields`): money and percentages as decimal strings
("2000.00", "50.00"), whole numbers as JSON numbers, dates as "YYYY-MM-DD", and everything else as text. An empty field
is null. The values travel as one object keyed by field name, so a rule set is the same shape everywhere: a firm's
template, a person's verified rule set, and the operator's edit.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from ...prop_rules import FIELDS, MAX_TEXT
from .accounts import Amount, LedgerEntryOut, Percent, PositiveAmount, _In
from .base import ApiModel
from .journal import AccountOut, Phase, RowId
from .money import Money

RuleKind = Literal["text", "money", "int", "pct", "date", "choice"]
FlagKind = Literal["unknown", "conflict", "third-party only"]
RuleValue = str | int | None
RuleStatus = Literal["pass", "fail", "n/a", "not_verifiable"]
AccountStatus = Literal["ok", "at_risk", "breached", "passed", "payout_ready"]

FieldName = Annotated[str, Field(min_length=1, max_length=64)]
RuleValues = Annotated[dict[FieldName, RuleValue], Field(max_length=len(FIELDS))]
SourceNotes = Annotated[dict[FieldName, Annotated[str, Field(max_length=MAX_TEXT)]], Field(max_length=len(FIELDS))]


# ---- the fields ---------------------------------------------------------------------------------------------------


class RuleFieldOut(ApiModel):
    name: str
    kind: RuleKind
    group: str
    label: str
    choices: list[str]  # the allowed values of a `choice` field; empty for the others
    hint: str


class RuleFieldsOut(ApiModel):
    """Every rule field, in the form's order, and the form's groups in order."""

    groups: list[str]
    fields: list[RuleFieldOut]


# ---- flags ----------------------------------------------------------------------------------------------------------


class FlagIn(_In):
    field: FieldName
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class FlagOut(ApiModel):
    """A field the research could not confirm. `unknown` and `conflict` fields were left empty on purpose; a `third-party
    only` field keeps its value. The reason is the research's own words (a conflict names both values)."""

    field: str
    reason: str
    kind: FlagKind


# ---- templates --------------------------------------------------------------------------------------------------------


class RuleTemplateKeyOut(ApiModel):
    """What names a template, and how sure the research is about it."""

    id: RowId
    firm: str
    program: str
    account_size: str
    phase: Phase
    rule_version: str
    effective_from: date | None
    effective_from_note: str | None  # what the source says when the start is not a clean date ("before 2025-11-28")
    data_confidence: str | None
    flagged: int  # how many fields the research could not confirm


class RuleTemplateSummaryOut(RuleTemplateKeyOut):
    """One line of the picker."""

    preselect: bool  # the version to suggest for the purchase date asked about


class RuleTemplatesOut(ApiModel):
    templates: list[RuleTemplateSummaryOut]


class RuleTemplateOut(ApiModel):
    id: RowId
    values: dict[str, RuleValue]
    flags: list[FlagOut]
    source_notes: dict[str, str]  # the original text of cells that were not a clean value ("up to 1500")
    updated_at: datetime


# ---- a person's verified rule set ---------------------------------------------------------------------------------------


class RuleSetIn(_In):
    """What the rules form submits. Every field of `GET /rule-fields` may be given (a missing one is empty). The flags and
    source notes of the template it started from are sent back with it, so the account keeps showing what the research
    could not confirm."""

    values: RuleValues
    flags: Annotated[list[FlagIn], Field(max_length=len(FIELDS))]
    source_notes: SourceNotes
    source_template_id: RowId | None  # null for rules typed in by hand


class RuleSetOut(ApiModel):
    id: RowId
    account_id: RowId
    source_template_id: RowId | None
    verified_at: datetime
    values: dict[str, RuleValue]
    flags: list[FlagOut]
    source_notes: dict[str, str]


# ---- payouts ----------------------------------------------------------------------------------------------------------


class PayoutIn(_In):
    entry_date: date
    gross: PositiveAmount  # what the firm paid out of the account
    split_pct: Percent | None  # the trader's share; null takes the split from the account's rules
    fee: Amount | None  # kept by the firm or the bank; null for none
    note: Annotated[str, Field(max_length=500)]


class PayoutsOut(ApiModel):
    payouts: list[LedgerEntryOut]  # oldest first
    split_pct: Money | None  # the share the account's rules promise (the form's default); null when they give none
    next_number: int
    lifetime_gross: Money  # a payout recorded before the gross was kept counts its net
    lifetime_net: Money
    lifetime_fees: Money


# ---- the Accounts tab ---------------------------------------------------------------------------------------------------


class RuleResultOut(ApiModel):
    key: str
    group: str
    label: str
    status: RuleStatus
    current: str | None
    limit: str | None
    message: str


class SpectrumOut(ApiModel):
    """One bar from breach (left) to goal (right)."""

    breach_level: Money | None
    breach_kind: Literal["drawdown", "daily_loss"] | None
    target_level: Money | None
    target_kind: Literal["profit_target", "payout"] | None
    balance: Money
    to_breach: Money | None  # dollars above the breach level
    to_target: Money | None  # dollars still to go (0 once reached)
    position: float | None  # 0 at the breach level, 1 at the target


class PayoutReadinessOut(ApiModel):
    ready: bool
    gross: Money | None  # what can be withdrawn now, when ready
    net: Money | None  # gross x split
    split_pct: Money | None
    next_number: int
    progress: str  # "3/5 qualifying days · best day 38% of 50% max · $640 to buffer"
    blockers: list[str]


class InactivityOut(ApiModel):
    days_left: int | None
    deadline: date | None
    message: str


class AccountRowOut(ApiModel):
    """One active account on the Accounts tab. Without verified rules only the money is known: `rules_set` is false and
    the rule fields are empty."""

    account: AccountOut
    program: str | None  # from the verified rules
    phase: Phase
    rules_set: bool
    balance: Money  # start + net P&L of closed trades - what payouts took out
    net_pnl: Money
    status: AccountStatus | None
    rules: list[RuleResultOut]
    spectrum: SpectrumOut | None
    payout: PayoutReadinessOut | None  # funded and live accounts with rules
    consistency_headroom: Money | None
    inactivity: InactivityOut | None
    floor: Money | None  # the drawdown floor now
    progress: str
    withdrawn_gross: Money  # lifetime, what the payouts took out of the account
    withdrawn_net: Money  # lifetime, what the trader received before fees
    pass_prompt: bool  # an evaluation passed: offer to set up the funded account


class AccountsTotalsOut(ApiModel):
    balance: Money
    net_pnl: Money
    withdrawable_gross: Money  # over the payout-ready rows
    withdrawable_net: Money
    withdrawn_gross: Money  # lifetime, over the rows shown
    withdrawn_net: Money


class AccountsOverviewOut(ApiModel):
    accounts: list[AccountRowOut]
    totals: AccountsTotalsOut


# ---- the operator's templates -------------------------------------------------------------------------------------------


class AdminRuleTemplateOut(RuleTemplateOut):
    updated_by: str  # "seed", "cli:owner" or "admin:<operator id>"


class AdminRuleTemplateListOut(ApiModel):
    templates: list[RuleTemplateKeyOut]
    total: int
    limit: int
    offset: int


class RuleTemplateEditIn(_In):
    """An operator's edit of one template: every value and flag. Its source notes are the research's and stay, except for
    a field that now has a value."""

    values: RuleValues
    flags: Annotated[list[FlagIn], Field(max_length=len(FIELDS))]


class RuleTemplateImportOut(ApiModel):
    """What an import did, or with `applied` false (a dry run) what it would do: the same counts, nothing kept."""

    applied: bool
    inserted: int
    updated: int
    unchanged: int
    changed: list[str]  # the updated rule sets by name ("Apex · EOD 50K · 50K · funded · 4.0"): edits they replace
    problems: list[str]  # the rows that were refused, by line
