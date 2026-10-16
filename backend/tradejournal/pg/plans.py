"""Plans on Postgres: a person's effective plan, grant codes, and the owner's CLI to make them (doc 03 section 5, ADR-012).

A code is stored hashed. Anyone redeeming one gets the same answer for every kind of failure (unknown, revoked, expired,
used up, already used by this person), so a guess teaches nothing. Creating, listing and revoking codes needs the schema
owner (the CLI below) or the operator's tools; reading a person's plan and redeeming happen as the `login` role.

Two shapes of code exist. A random one (GR-XXXX-XXXX) is shown once and only its hash is kept. A promo code is typed by the
operator (FREE30: 3 to 20 capital letters and digits) and is also kept readable, because it is meant to be shared (migration
0020). Both are looked up by hash, so redeeming works the same way for each.

    TJ_OWNER_URL=postgresql://tj_owner:...@host/db python -m tradejournal.pg.plans create-grant --label "Beta testers" --days 30
    TJ_OWNER_URL=postgresql://tj_owner:...@host/db python -m tradejournal.pg.plans create-grant --code FREE30 --days 30

`start-trials` is for the day billing is switched on (`docs/runbooks/billing.md`): it gives the 7-day trial to every account
that has never had free days, no override and no subscription, which billing on would otherwise make read-only at once.
Without `--apply` it only counts.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

from ..entitlements import (
    Access,
    EffectivePlan,
    EndedGrant,
    Grant,
    Override,
    SubscriptionState,
    access_of,
    effective_plan,
    subscription_lapsed_at,
)
from .invites import ALPHABET, hash_code, normalize

_SHAPE = re.compile(r"^[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+$")  # a random code: GR-XXXX-XXXX
PROMO_SHAPE = re.compile(r"^[A-Z0-9]{3,20}$")  # a typed code: FREE30 (migration 0020 holds the same rule)
LOCK_CLASS = 7_283_003  # advisory lock: one redemption of one code at a time (invites use 7_283_002)
MAX_GRANT_DAYS = 30  # a code that gives full access gives at most this long (migration 0014 holds the same line)


def generate_code() -> str:
    """GR-XXXX-XXXX: its own prefix, so a plan code is not mistaken for an invite code (TJ-...)."""
    return "GR-" + "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(2))


def typed_code(text: str) -> str | None:
    """The promo code an operator typed, as it is stored ("free30" is "FREE30"), or None if it is not 3 to 20 letters and
    digits. Spaces and dashes are refused here rather than removed: what is stored is exactly what people will be told."""
    code = text.strip().upper()
    return code if PROMO_SHAPE.fullmatch(code) else None


def lookup_forms(code: str | None) -> list[str]:
    """The forms of what a person typed to look up, in order. "gr 7kq2-m9xa" is the random code GR-7KQ2-M9XA; "free30",
    " Free30 " and "free 30" are all the promo code FREE30 (a typed code never has a dash, so the dashes that `normalize`
    puts for spaces can be taken out again). Empty when it cannot be any code, so nothing is looked up."""
    if not code:
        return []
    normalized = normalize(code)
    if len(normalized) > 40:
        return []
    forms = []
    if _SHAPE.fullmatch(normalized) or PROMO_SHAPE.fullmatch(normalized):
        forms.append(normalized)
    compact = normalized.replace("-", "")
    if compact != normalized and PROMO_SHAPE.fullmatch(compact):
        forms.append(compact)
    return forms


def find_code(conn: psycopg.Connection, code: str | None) -> int | None:
    """The id of the code a person typed, in either shape, or None."""
    for form in lookup_forms(code):
        row = conn.execute("SELECT id FROM grant_codes WHERE code_hash = %s", (hash_code(form),)).fetchone()
        if row is not None:
            return row["id"]
    return None


# ---- a person's plan -----------------------------------------------------------------------------------------------


def subscription_state(conn: psycopg.Connection, user_id: UUID) -> SubscriptionState | None:
    """The person's subscription as the plan rule reads it (migration 0021), or None."""
    row = conn.execute(
        """SELECT plan, status, current_period_end, cancel_at_period_end, past_due_since, ended_at
           FROM subscriptions WHERE user_id = %s""",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return SubscriptionState(
        row["plan"],
        row["status"],
        row["current_period_end"],
        cancel_at_period_end=row["cancel_at_period_end"],
        past_due_since=row["past_due_since"],
        ended_at=row["ended_at"],
    )


def effective(conn: psycopg.Connection, user_id: UUID, now: datetime | None = None) -> EffectivePlan:
    """The plan this person is on now: the active override, else the higher of the active grant and a live subscription,
    else free."""
    moment = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT plan, expires_at, revoked_at FROM plan_overrides WHERE user_id = %s AND revoked_at IS NULL", (user_id,)
    ).fetchone()
    override = Override(row["plan"], row["expires_at"], revoked=row["revoked_at"] is not None) if row else None
    grants = [
        Grant(r["plan"], r["starts_at"], r["ends_at"])
        for r in conn.execute(
            "SELECT plan, starts_at, ends_at FROM grant_redemptions"
            " WHERE user_id = %s AND ends_at > %s AND revoked_at IS NULL",
            (user_id, moment),
        ).fetchall()
    ]
    return effective_plan(moment, override=override, grants=grants, subscription=subscription_state(conn, user_id))


def access(
    conn: psycopg.Connection,
    user_id: UUID,
    now: datetime | None = None,
    *,
    plan: EffectivePlan | None = None,
    billing: bool = False,
) -> Access:
    """May this person change things now? With billing off: read-only only for someone whose time-limited access ran out or
    was ended (see `entitlements.access_of`); nobody else is ever affected. With `billing` on, anyone with nothing live is
    read-only. Pass `plan` when it has just been worked out."""
    moment = now or datetime.now(UTC)
    plan = plan or effective(conn, user_id, moment)
    last = None
    lapsed = None
    if plan.source == "free":  # only worth a second query when it could matter
        row = conn.execute(
            """SELECT least(ends_at, coalesce(revoked_at, ends_at)) AS ended_at, revoked_at IS NOT NULL AS revoked
               FROM grant_redemptions WHERE user_id = %s AND (ends_at <= %s OR revoked_at IS NOT NULL)
               ORDER BY least(ends_at, coalesce(revoked_at, ends_at)) DESC LIMIT 1""",
            (user_id, moment),
        ).fetchone()
        if row is not None:
            last = EndedGrant(row["ended_at"], bool(row["revoked"]))
        if billing:
            lapsed = subscription_lapsed_at(subscription_state(conn, user_id), moment)
    return access_of(plan, last, billing=billing, lapsed_at=lapsed)


# ---- redeeming ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Redeemed:
    plan: str
    days: int
    ends_at: datetime


@dataclass(frozen=True)
class Refused:
    """Unknown, revoked, expired, used up, or already used by this person: one answer for all of them."""


@dataclass(frozen=True)
class AlreadyHave:
    """A valid code, but this person already has a plan from a code that has not ended: one at a time."""

    ends_at: datetime


def _usable(conn: psycopg.Connection, grant_id: int, user_id: UUID, moment: datetime) -> dict | None:
    """The code's row if it can be redeemed by this person now (call it with the code's lock held)."""
    row = conn.execute(
        """SELECT g.id, g.kind, g.plan, g.days, g.max_redemptions, g.expires_at, g.revoked_at,
                  (SELECT count(*) FROM grant_redemptions r WHERE r.grant_code_id = g.id) AS used,
                  EXISTS (SELECT 1 FROM grant_redemptions r WHERE r.grant_code_id = g.id AND r.user_id = %s) AS mine
           FROM grant_codes g WHERE g.id = %s""",
        (user_id, grant_id),
    ).fetchone()
    if (
        row is None
        or row["revoked_at"] is not None
        or (row["expires_at"] is not None and row["expires_at"] <= moment)
        or (row["max_redemptions"] is not None and row["used"] >= row["max_redemptions"])
        or row["mine"]
    ):
        return None
    return row


def _give(conn: psycopg.Connection, row: dict, user_id: UUID, moment: datetime) -> Redeemed:
    ends = moment + timedelta(days=row["days"])
    conn.execute(
        "INSERT INTO grant_redemptions (grant_code_id, user_id, plan, starts_at, ends_at) VALUES (%s, %s, %s, %s, %s)",
        (row["id"], user_id, row["plan"], moment, ends),
    )
    return Redeemed(row["plan"], row["days"], ends)


def redeem(
    conn: psycopg.Connection, user_id: UUID, code: str | None, *, now: datetime | None = None
) -> Redeemed | Refused | AlreadyHave:
    """Use a code for this person. Call it inside a transaction: two people redeeming the last use at once cannot both get
    it, and a person redeeming the same code in two tabs gets it once."""
    moment = now or datetime.now(UTC)
    found = find_code(conn, code)
    if found is None:
        return Refused()
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_CLASS, found))
    row = _usable(conn, found, user_id, moment)
    if row is None or row["kind"] != "free_days":
        return Refused()
    # One code at a time, but a trial never blocks one (the billing design, question 5): a promo that runs longer replaces it.
    active = conn.execute(
        """SELECT max(r.ends_at) AS ends_at FROM grant_redemptions r JOIN grant_codes g ON g.id = r.grant_code_id
           WHERE r.user_id = %s AND r.starts_at <= %s AND r.ends_at > %s AND r.revoked_at IS NULL AND g.kind <> 'trial'""",
        (user_id, moment, moment),
    ).fetchone()
    if active["ends_at"] is not None:
        return AlreadyHave(active["ends_at"])
    return _give(conn, row, user_id, moment)


def usable_by_anyone(conn: psycopg.Connection, code: str | None, *, now: datetime | None = None) -> bool:
    """Whether this code could still be redeemed by *somebody* now: it exists, is not revoked or expired, and has a use left.
    The check made at sign-up before the browser leaves for the identity provider; `redeem` still decides, inside the
    sign-up transaction. One answer (False) for every way it cannot."""
    found = find_code(conn, code)
    if found is None:
        return False
    row = conn.execute(
        """SELECT g.max_redemptions, g.expires_at, g.revoked_at,
                  (SELECT count(*) FROM grant_redemptions r WHERE r.grant_code_id = g.id) AS used
           FROM grant_codes g WHERE g.id = %s""",
        (found,),
    ).fetchone()
    moment = now or datetime.now(UTC)
    return not (
        row is None
        or row["revoked_at"] is not None
        or (row["expires_at"] is not None and row["expires_at"] <= moment)
        or (row["max_redemptions"] is not None and row["used"] >= row["max_redemptions"])
    )


def give_bundled(
    conn: psycopg.Connection, grant_code_id: int, user_id: UUID, *, now: datetime | None = None
) -> Redeemed | None:
    """The grant an invite carries, given to the person who signed up with it, inside the sign-up transaction. A code that
    has run out, been revoked or expired in the meantime gives nothing, and sign-up goes ahead without it."""
    moment = now or datetime.now(UTC)
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_CLASS, grant_code_id))
    row = _usable(conn, grant_code_id, user_id, moment)
    return None if row is None else _give(conn, row, user_id, moment)


# ---- the owner's side ----------------------------------------------------------------------------------------------


def create_grant(
    owner_conn: psycopg.Connection,
    *,
    label: str = "",
    plan: str = "pro",
    days: int,
    max_redemptions: int | None = None,
    expires_in: timedelta | None = None,
    code: str | None = None,
    now: datetime | None = None,
) -> tuple[int, str]:
    """Make a code (as the schema owner). Returns its id and the code. A typed `code` (FREE30) is kept readable; without one
    a random code is made, which is shown once: only its hash is kept. A typed code already in use, in any case and even if
    it was revoked, is refused by the database (`psycopg.errors.UniqueViolation`)."""
    if not 1 <= days <= MAX_GRANT_DAYS:
        raise ValueError(f"a grant lasts from 1 to {MAX_GRANT_DAYS} days")
    if max_redemptions is not None and max_redemptions < 1:
        raise ValueError("a code must allow at least one use")
    typed = None
    if code is not None:
        typed = typed_code(code)
        if typed is None:
            raise ValueError("a promo code is 3 to 20 letters and digits, such as FREE30")
    made = typed or generate_code()
    row = owner_conn.execute(
        """INSERT INTO grant_codes (code_hash, code, label, plan, days, max_redemptions, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (
            hash_code(made),
            typed,
            label.strip(),
            plan,
            days,
            max_redemptions,
            (now or datetime.now(UTC)) + expires_in if expires_in else None,
        ),
    ).fetchone()
    return row["id"], made


def list_grants(owner_conn: psycopg.Connection) -> list[dict]:
    return owner_conn.execute(
        """SELECT g.id, g.code, g.label, g.plan, g.days, g.max_redemptions, g.expires_at, g.revoked_at,
                  (SELECT count(*) FROM grant_redemptions r WHERE r.grant_code_id = g.id) AS used
           FROM grant_codes g ORDER BY g.id"""
    ).fetchall()


def revoke_grant(owner_conn: psycopg.Connection, grant_id: int, *, now: datetime | None = None) -> bool:
    """Stop a code being used. People who already redeemed it keep what they were given."""
    row = owner_conn.execute(
        "UPDATE grant_codes SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL RETURNING id",
        (now or datetime.now(UTC), grant_id),
    ).fetchone()
    return row is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make, list and revoke plan codes (as the schema owner).")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-grant", help="make a code that gives a plan for some days")
    create.add_argument(
        "--label", default="", help="who or what it is for, in your words (shown to you, never to them)"
    )
    create.add_argument(
        "--code", help="a promo code to type, such as FREE30 (kept readable); leave out for a random one"
    )
    create.add_argument("--plan", default="pro", choices=["pro"])
    create.add_argument("--days", type=int, required=True, help="how long the plan lasts once redeemed")
    create.add_argument("--max-redemptions", type=int, help="how many people may use it (default: any number)")
    create.add_argument("--expires-days", type=int, help="the code stops working after this many days")
    sub.add_parser("list-grants", help="every code, how many people used it, and whether it still works")
    revoke = sub.add_parser("revoke-grant", help="stop a code being used (people who used it keep their plan)")
    revoke.add_argument("--id", type=int, required=True)
    trials = sub.add_parser(
        "start-trials", help="switch-on day: a 7-day trial for every account with no code, override or subscription"
    )
    trials.add_argument("--apply", action="store_true", help="give them (the default only counts)")
    args = parser.parse_args(argv)
    url = os.environ.get("TJ_OWNER_URL")
    if not url:
        print("TJ_OWNER_URL is not set", file=sys.stderr)
        return 2
    with psycopg.connect(url, autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
        if args.command == "create-grant":
            try:
                grant_id, code = create_grant(
                    conn,
                    label=args.label,
                    plan=args.plan,
                    days=args.days,
                    max_redemptions=args.max_redemptions,
                    expires_in=timedelta(days=args.expires_days) if args.expires_days else None,
                    code=args.code,
                )
            except psycopg.errors.UniqueViolation:
                print("That code is already in use (codes are never reused, even revoked ones).", file=sys.stderr)
                return 1
            print(f"Plan code #{grant_id} ({args.days} days of {args.plan}): {code}")
            if args.code is None:
                print("It is shown once. Only its hash is kept.")
        elif args.command == "list-grants":
            for g in list_grants(conn):
                limit = "any number" if g["max_redemptions"] is None else f"up to {g['max_redemptions']}"
                state = "revoked" if g["revoked_at"] else "works"
                name = g["code"] or g["label"] or "(no label)"
                print(f"#{g['id']} {name}: {g['days']} days of {g['plan']}, used {g['used']} ({limit}), {state}")
        elif args.command == "start-trials":
            from .billing import start_trials  # billing builds on this module

            done = start_trials(conn, apply=args.apply)
            if done.no_trial_code:
                print(
                    "The trial's code is missing or revoked (migration 0021 makes it): no trial can be given.",
                    file=sys.stderr,
                )
                return 1
            mode = "applied" if args.apply else "dry run: nothing changed"
            print(f"{mode}; accounts with nothing that gives Pro: {done.due}, trials started: {done.started}")
            if done.operators:
                print(
                    f"{done.operators} operator account(s) also have nothing: give each an override (Pro, no end) instead."
                )
        else:
            print("revoked" if revoke_grant(conn, args.id) else "no such code, or it was already revoked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
