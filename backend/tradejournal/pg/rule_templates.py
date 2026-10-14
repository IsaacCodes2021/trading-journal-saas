"""The firms' rule templates: loading them from the research CSV, and the operator's list and edits (migration 0019).

    TJ_OWNER_URL=postgresql://tj_owner:...@host/db python -m tradejournal.pg.rule_templates import --csv rules.csv
    ... import --csv rules.csv --apply

The import is an upsert keyed on firm + program + account size + phase + rule version, so running it twice changes
nothing the second time, and a corrected CSV updates only the rows that differ. Without `--apply` it is a dry run: the
same work in a transaction that is rolled back, so the counts are exactly what `--apply` would do. The CSV is not part
of the repository (`docs/prop-rules/` is the owner's), so the command is always given a file.

Templates belong to nobody. Every function here takes a connection that may write them: the schema owner (this
command) or the operator's role (`system_session("admin")`, migration 0019's grant). A person's own copy of a rule set
(`account_rules`) is never touched here: editing a template does not change anyone's account.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from ..prop_rules import FIELD_NAMES, KEY_FIELDS, RuleValues, parse_csv

PAGE_MAX = 500
_COLUMNS = sql.SQL(", ").join(sql.Identifier(name) for name in FIELD_NAMES)
_EXTRA = ("flagged_fields", "flag_reasons", "source_notes")


@dataclass(frozen=True)
class Template:
    id: int
    rules: RuleValues
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True)
class ImportCounts:
    inserted: int
    updated: int
    unchanged: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


def _params(rules: RuleValues) -> list[Any]:
    return [
        *(rules.values.get(name) for name in FIELD_NAMES),
        rules.flagged_fields,
        rules.flag_reasons,
        json.dumps(rules.source_notes, sort_keys=True),
    ]


def upsert(conn: psycopg.Connection, rows: list[RuleValues], *, by: str) -> ImportCounts:
    """Insert new rule sets and update changed ones, in the caller's transaction. A row whose every value already matches
    is left alone (its `updated_at` does not move)."""
    every = [*FIELD_NAMES, *_EXTRA]
    changed = sql.SQL(" OR ").join(
        sql.SQL("rule_templates.{c} IS DISTINCT FROM EXCLUDED.{c}").format(c=sql.Identifier(name)) for name in every
    )
    statement = sql.SQL(
        """INSERT INTO rule_templates ({columns}, flagged_fields, flag_reasons, source_notes, updated_by)
           VALUES ({values}, %s, %s, %s::jsonb, %s)
           ON CONFLICT (firm, program, account_size, phase, rule_version) DO UPDATE SET
               {assignments}, updated_at = now(), updated_by = EXCLUDED.updated_by
           WHERE {changed}
           RETURNING (xmax = 0) AS inserted"""
    ).format(
        columns=_COLUMNS,
        values=sql.SQL(", ").join(sql.Placeholder() for _ in FIELD_NAMES),
        assignments=sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(name)) for name in every),
        changed=changed,
    )
    inserted = updated = unchanged = 0
    for rules in rows:
        result = conn.execute(statement, [*_params(rules), by]).fetchone()
        if result is None:
            unchanged += 1
        elif result["inserted"]:
            inserted += 1
        else:
            updated += 1
    return ImportCounts(inserted, updated, unchanged)


def import_csv(conn: psycopg.Connection, text: str, *, by: str) -> tuple[ImportCounts, list[str]]:
    """Read the CSV and upsert every row it could read. The second value lists the rows it refused, in words with their
    line number; a file with any refused row is still imported for the rest (the caller decides what to do about it)."""
    parsed = parse_csv(text)
    problems = [f"Line {p.line}: {p.message}" for p in parsed.problems]
    if not parsed.rows:
        return ImportCounts(0, 0, 0), problems or ["The file has no rule sets."]
    return upsert(conn, parsed.rows, by=by), problems


def _template(row: dict) -> Template:
    return Template(
        id=row["id"],
        rules=RuleValues(
            values={name: row[name] for name in FIELD_NAMES},
            flagged_fields=list(row["flagged_fields"]),
            flag_reasons=list(row["flag_reasons"]),
            source_notes=dict(row["source_notes"]),
        ),
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


_ORDER = sql.SQL(" ORDER BY lower(firm), lower(program), account_size, phase, effective_from DESC NULLS LAST, id")


def get(conn: psycopg.Connection, template_id: int) -> Template | None:
    row = conn.execute("SELECT * FROM rule_templates WHERE id = %s", (template_id,)).fetchone()
    return _template(row) if row else None


def list_all(conn: psycopg.Connection) -> list[Template]:
    """Every template, by firm, program, size and phase, the newest version first."""
    return [_template(row) for row in conn.execute(sql.SQL("SELECT * FROM rule_templates") + _ORDER)]


def search(conn: psycopg.Connection, q: str | None, *, limit: int, offset: int) -> tuple[list[Template], int]:
    """A page of templates whose firm, program, size, phase or version contains every word of `q` (any case)."""
    words = [w for w in (q or "").lower().split() if w][:8]
    where = sql.SQL(" AND ").join(
        sql.SQL("lower(firm || ' ' || program || ' ' || account_size || ' ' || phase || ' ' || rule_version) LIKE %s")
        for _ in words
    )
    clause = sql.SQL(" WHERE ") + where if words else sql.SQL("")
    params = [f"%{_escape_like(w)}%" for w in words]
    total = conn.execute(sql.SQL("SELECT count(*) AS n FROM rule_templates") + clause, params).fetchone()["n"]
    rows = conn.execute(
        sql.SQL("SELECT * FROM rule_templates") + clause + _ORDER + sql.SQL(" LIMIT %s OFFSET %s"),
        [*params, min(limit, PAGE_MAX), offset],
    )
    return [_template(row) for row in rows], total


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def update(conn: psycopg.Connection, template_id: int, rules: RuleValues, *, by: str) -> Template | None:
    """Replace one template's values and flags. A field that now has a value loses the source note it had (the note
    explained an empty cell). Returns None when there is no such template; a clash with another template's key raises
    `psycopg.errors.UniqueViolation`."""
    current = get(conn, template_id)
    if current is None:
        return None
    notes = {k: v for k, v in current.rules.source_notes.items() if rules.values.get(k) is None}
    assignments = sql.SQL(", ").join(
        sql.SQL("{c} = %s").format(c=sql.Identifier(name)) for name in (*FIELD_NAMES, *_EXTRA)
    )
    kept = RuleValues(dict(rules.values), list(rules.flagged_fields), list(rules.flag_reasons), notes)
    conn.execute(
        sql.SQL("UPDATE rule_templates SET {a}, updated_at = now(), updated_by = %s WHERE id = %s").format(
            a=assignments
        ),
        [*_params(kept), by, template_id],
    )
    return get(conn, template_id)


def key_text(rules: RuleValues) -> str:
    return " · ".join(str(rules.values[name]) for name in KEY_FIELDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load the prop-firm rule templates from the research CSV (as the schema owner). A dry run unless --apply."
    )
    parser.add_argument("command", choices=["import"])
    parser.add_argument("--csv", required=True, type=Path, help="the rules CSV (48 columns, one row per rule set)")
    parser.add_argument("--apply", action="store_true", help="write the changes (without it, nothing is kept)")
    args = parser.parse_args(argv)
    url = os.environ.get("TJ_OWNER_URL")
    if not url:
        print("TJ_OWNER_URL is not set", file=sys.stderr)
        return 2
    try:
        text = args.csv.read_text(encoding="utf-8")
    except OSError as problem:
        print(f"Cannot read {args.csv}: {problem.strerror or problem}", file=sys.stderr)
        return 2
    with psycopg.connect(url, row_factory=dict_row) as conn:
        counts, problems = import_csv(conn, text, by="cli:owner")
        if args.apply and counts.total:
            conn.commit()
        else:
            conn.rollback()
    for problem in problems:
        print(problem, file=sys.stderr)
    verb = "Imported" if args.apply else "Dry run (nothing kept): would import"
    print(
        f"{verb} {counts.total} rule sets: {counts.inserted} new, {counts.updated} changed, {counts.unchanged} unchanged."
    )
    if not args.apply:
        print("Run again with --apply to keep them.")
    return 1 if problems and not counts.total else 0


if __name__ == "__main__":
    sys.exit(main())
