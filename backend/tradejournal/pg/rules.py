"""A person's prop-firm rules: picking a template, and the verified rule set of each account (migration 0019).

Every function takes a connection from `Database.tenant_session`. The templates are read-only here (the request role
may only SELECT them); a person's rule sets are theirs alone (row-level security). Saving a rule set copies every value
into `account_rules`: the template it came from is remembered, never followed, so a later edit of the template changes
nothing on the account. A new rule set supersedes the previous one; the old row stays as the account's history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg import sql

from ..prop_rules import FIELD_NAMES, RuleValues
from . import rule_templates

_COLUMNS = sql.SQL(", ").join(sql.Identifier(name) for name in FIELD_NAMES)


@dataclass(frozen=True)
class Offered:
    """A template the picker offers for one purchase date, and whether it is the one to preselect."""

    template: rule_templates.Template
    preselect: bool


@dataclass(frozen=True)
class RuleSet:
    id: int
    account_id: int
    source_template_id: int | None
    verified_at: datetime
    rules: RuleValues


def offered_templates(conn: psycopg.Connection, purchased_on: date | None) -> list[Offered]:
    """The versions that can apply to an account bought on `purchased_on`: those in force on or before that day, and those
    whose start is not known (the research could not date them, so they are offered rather than hidden). For each firm +
    program + size + phase the latest dated version is preselected (an undated one only when it is the only choice);
    the person still confirms it. Without a purchase date every version is offered and none is preselected."""
    templates = rule_templates.list_all(conn)
    offered = [
        t
        for t in templates
        if purchased_on is None
        or t.rules.values["effective_from"] is None
        or t.rules.values["effective_from"] <= purchased_on
    ]
    best: dict[tuple[str, str, str, str], rule_templates.Template] = {}
    if purchased_on is not None:
        for t in offered:
            v = t.rules.values
            key = (v["firm"], v["program"], v["account_size"], v["phase"])
            current = best.get(key)
            if current is None or _later(t, current):
                best[key] = t
    chosen = {t.id for t in best.values()}
    return [Offered(t, t.id in chosen) for t in offered]


def _later(a: rule_templates.Template, b: rule_templates.Template) -> bool:
    """Whether version `a` is a later choice than `b`: a known start beats an unknown one, a later start an earlier one,
    and between two unknowns the one added last (the research lists the current version after the older one)."""
    da, db = a.rules.values["effective_from"], b.rules.values["effective_from"]
    if da is not None and db is not None and da != db:
        return da > db
    if (da is None) != (db is None):
        return da is not None
    return a.id > b.id


def template(conn: psycopg.Connection, template_id: int) -> rule_templates.Template | None:
    return rule_templates.get(conn, template_id)


def _rule_set(row: dict) -> RuleSet:
    return RuleSet(
        id=row["id"],
        account_id=row["account_id"],
        source_template_id=row["source_template_id"],
        verified_at=row["verified_at"],
        rules=RuleValues(
            values={name: row[name] for name in FIELD_NAMES},
            flagged_fields=list(row["flagged_fields"]),
            flag_reasons=list(row["flag_reasons"]),
            source_notes=dict(row["source_notes"]),
        ),
    )


def active(conn: psycopg.Connection, account_id: int) -> RuleSet | None:
    row = conn.execute(
        "SELECT * FROM account_rules WHERE account_id = %s AND superseded_at IS NULL", (account_id,)
    ).fetchone()
    return _rule_set(row) if row else None


def all_active(conn: psycopg.Connection) -> dict[int, RuleSet]:
    """Every account's verified rule set, by account id."""
    rows = conn.execute("SELECT * FROM account_rules WHERE superseded_at IS NULL")
    return {row["account_id"]: _rule_set(row) for row in rows}


def history(conn: psycopg.Connection, account_id: int) -> list[RuleSet]:
    rows = conn.execute(
        "SELECT * FROM account_rules WHERE account_id = %s ORDER BY verified_at DESC, id DESC", (account_id,)
    )
    return [_rule_set(row) for row in rows]


def save(conn: psycopg.Connection, account_id: int, rules: RuleValues, *, source_template_id: int | None) -> RuleSet:
    """Verify and keep a rule set for the account: the previous one is superseded in the same transaction. The caller has
    checked the values (`prop_rules.coerce_all` and `check`). Flags and notes are kept only for fields that exist."""
    if conn.execute("SELECT 1 FROM accounts WHERE id = %s", (account_id,)).fetchone() is None:
        raise LookupError(f"no account with id {account_id}")
    if source_template_id is not None and rule_templates.get(conn, source_template_id) is None:
        raise LookupError(f"no rule template with id {source_template_id}")
    flags = [(f, r) for f, r in zip(rules.flagged_fields, rules.flag_reasons) if f in FIELD_NAMES]
    notes = {k: v for k, v in rules.source_notes.items() if k in FIELD_NAMES}
    conn.execute(
        "UPDATE account_rules SET superseded_at = now() WHERE account_id = %s AND superseded_at IS NULL", (account_id,)
    )
    values: list[Any] = [rules.values.get(name) for name in FIELD_NAMES]
    row = conn.execute(
        sql.SQL(
            """INSERT INTO account_rules (account_id, source_template_id, {columns}, flagged_fields, flag_reasons,
                   source_notes)
               VALUES (%s, %s, {values}, %s, %s, %s::jsonb)
               RETURNING *"""
        ).format(columns=_COLUMNS, values=sql.SQL(", ").join(sql.Placeholder() for _ in FIELD_NAMES)),
        [
            account_id,
            source_template_id,
            *values,
            [f for f, _ in flags],
            [r for _, r in flags],
            json.dumps(notes, sort_keys=True),
        ],
    ).fetchone()
    return _rule_set(row)
