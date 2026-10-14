"""Rule sets, templates and payouts as the API's shapes, and a submitted rule set checked field by field."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.exceptions import RequestValidationError

from .. import compliance
from ..finance import ZERO
from ..ledger import LedgerEntry
from ..pg.rule_templates import Template
from ..pg.rules import Offered, RuleSet
from ..prop_rules import BY_NAME, FIELD_NAMES, FIELDS, GROUPS, KEY_FIELDS, RuleValues, check, coerce_all, flag_kind
from .accounts_view import entry_out
from .cents import cents
from .schemas.rules import (
    AdminRuleTemplateOut,
    FlagIn,
    FlagOut,
    PayoutsOut,
    RuleFieldOut,
    RuleFieldsOut,
    RuleSetOut,
    RuleTemplateKeyOut,
    RuleTemplateOut,
    RuleTemplateSummaryOut,
    RuleValue,
)

_PCT = Decimal("0.01")


# ---- out ------------------------------------------------------------------------------------------------------------


def rule_fields() -> RuleFieldsOut:
    return RuleFieldsOut(
        groups=list(GROUPS),
        fields=[
            RuleFieldOut(name=f.name, kind=f.kind, group=f.group, label=f.label, choices=list(f.choices), hint=f.hint)
            for f in FIELDS
        ],
    )


def value_out(name: str, value: Any) -> RuleValue:
    """A typed rule value as it crosses the API: money and percentages as decimal strings, dates as ISO text."""
    if value is None:
        return None
    kind = BY_NAME[name].kind
    if kind in ("money", "pct"):
        return format(value, "f")
    if kind == "date":
        return value.isoformat() if isinstance(value, date) else str(value)
    if kind == "int":
        return int(value)
    return str(value)


def values_out(values: Mapping[str, Any]) -> dict[str, RuleValue]:
    return {name: value_out(name, values.get(name)) for name in FIELD_NAMES}


def flags_out(rules: RuleValues) -> list[FlagOut]:
    return [
        FlagOut(field=f, reason=r, kind=flag_kind(r))  # type: ignore[arg-type]  # flag_kind returns one of the three
        for f, r in zip(rules.flagged_fields, rules.flag_reasons)
    ]


def _key(t: Template) -> dict[str, Any]:
    v = t.rules.values
    return {
        "id": t.id,
        "firm": v["firm"],
        "program": v["program"],
        "account_size": v["account_size"],
        "phase": v["phase"],
        "rule_version": v["rule_version"],
        "effective_from": v["effective_from"],
        "effective_from_note": t.rules.source_notes.get("effective_from"),
        "data_confidence": v["data_confidence"],
        "flagged": len(t.rules.flagged_fields),
    }


def template_key(t: Template) -> RuleTemplateKeyOut:
    return RuleTemplateKeyOut(**_key(t))


def template_summary(offered: Offered) -> RuleTemplateSummaryOut:
    return RuleTemplateSummaryOut(**_key(offered.template), preselect=offered.preselect)


def template_out(t: Template) -> RuleTemplateOut:
    return RuleTemplateOut(
        id=t.id,
        values=values_out(t.rules.values),
        flags=flags_out(t.rules),
        source_notes=dict(t.rules.source_notes),
        updated_at=t.updated_at,
    )


def admin_template_out(t: Template) -> AdminRuleTemplateOut:
    return AdminRuleTemplateOut(**template_out(t).model_dump(), updated_by=t.updated_by)


def rule_set_out(r: RuleSet) -> RuleSetOut:
    return RuleSetOut(
        id=r.id,
        account_id=r.account_id,
        source_template_id=r.source_template_id,
        verified_at=r.verified_at,
        values=values_out(r.rules.values),
        flags=flags_out(r.rules),
        source_notes=dict(r.rules.source_notes),
    )


def rules_split(rule_set: RuleSet | None) -> Decimal | None:
    """The trader's share the account's rules promise, in percent (the payout form's default), or None."""
    if rule_set is None:
        return None
    share = compliance.split_share(rule_set.rules.values.get("profit_split"))
    return None if share is None else share.quantize(_PCT)


def payouts_out(entries: Iterable[LedgerEntry], rule_set: RuleSet | None) -> PayoutsOut:
    payouts = [e for e in entries if e.is_payout]
    return PayoutsOut(
        payouts=[entry_out(e) for e in payouts],
        split_pct=rules_split(rule_set),
        next_number=len(payouts) + 1,
        lifetime_gross=cents(sum((e.gross_amount if e.gross_amount is not None else e.amount for e in payouts), ZERO)),
        lifetime_net=cents(sum((e.amount for e in payouts), ZERO)),
        lifetime_fees=cents(sum((e.payout_fee or ZERO for e in payouts), ZERO)),
    )


# ---- in -------------------------------------------------------------------------------------------------------------


def _refuse(errors: list[tuple[tuple[str | int, ...], str]]) -> RequestValidationError:
    """The same 422 a malformed body gets (`errors.py`): one entry per field, `values.max_loss_usd` and the like."""
    return RequestValidationError(
        [{"loc": ("body", *loc), "msg": message, "type": "value_error"} for loc, message in errors]
    )


def checked_rules(
    raw: Mapping[str, RuleValue],
    flags: list[FlagIn],
    source_notes: Mapping[str, str] | None = None,
    *,
    whole: bool,
) -> RuleValues:
    """A submitted rule set, typed, or a 422 naming every field that is wrong. `whole` applies `prop_rules.check` (a
    person's rule set must make sense as a whole); a firm's template is only typed, because the research leaves cells
    empty on purpose, and needs only its name."""
    errors: list[tuple[tuple[str | int, ...], str]] = []
    errors += [(("values", name), "Not a rule field.") for name in raw if name not in BY_NAME]
    values, typing_errors = coerce_all({k: v for k, v in raw.items() if k in BY_NAME})
    problems = dict(typing_errors)
    if whole:
        problems = {**check(values), **typing_errors}  # a value of the wrong kind says so, not "it is missing"
    else:
        for name in KEY_FIELDS:
            if values.get(name) in (None, "") and name not in problems:
                problems[name] = f"{BY_NAME[name].label} is required."
    errors += [(("values", name), message) for name, message in problems.items()]
    for n, flag in enumerate(flags):
        if flag.field not in BY_NAME:
            errors.append((("flags", n, "field"), "Not a rule field."))
    errors += [(("source_notes", name), "Not a rule field.") for name in source_notes or {} if name not in BY_NAME]
    if errors:
        raise _refuse(errors)
    return RuleValues(
        values=values,
        flagged_fields=[f.field for f in flags],
        flag_reasons=[f.reason for f in flags],
        source_notes=dict(source_notes or {}),
    )
