"""Discount codes on Postgres (migration 0022; docs/design/promo-codes.md section 6, the Paddle guide section 5.8).

A discount code ("LAUNCH20": 20% off the first 3 months) is a `grant_codes` row of kind `discount`. It gives no days of Pro,
so nobody "redeems" it into `grant_redemptions`: a person who types one has it **remembered** (`pending_promos`) and the
next checkout they open carries it to the payment provider, which applies it. It works only while its provider copy is in
place (`sync_status` synced, for the provider this server uses), so a code is never shown as saved and then charged in full.

Who calls what, as which role:

    find_live, remember, pending        a person's code, from Settings, sign-up and the checkout     (login)
    forget_used                         once a subscription comes of the checkout that carried it      (webhook)
    used_sql                            how many people subscribed with each code, for the operator    (admin)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import psycopg

from .plans import find_code

Duration = Literal["once", "repeating", "forever"]

# People who subscribed with a code: its completed checkouts, one per person (a person can open more than one).
USED = """(SELECT count(DISTINCT c.user_id) FROM billing_checkouts c
           WHERE c.grant_code_id = g.id AND c.completed_at IS NOT NULL)"""


@dataclass(frozen=True)
class Terms:
    """What a discount code gives, in a person's words: "20% off your first 3 months". Not the code itself: the sign-in
    role cannot read codes (it finds them by hash), and the person knows what they typed."""

    percent_off: int
    duration: Duration
    duration_months: int | None

    @property
    def words(self) -> str:
        return describe(self.percent_off, self.duration, self.duration_months)


def describe(percent_off: int, duration: str, duration_months: int | None) -> str:
    if duration == "once":
        return f"{percent_off}% off your first payment"
    if duration == "forever":
        return f"{percent_off}% off every payment"
    months = duration_months or 1
    return f"{percent_off}% off your first month" if months == 1 else f"{percent_off}% off your first {months} months"


@dataclass(frozen=True)
class Live:
    """A discount code that works now with this server's provider."""

    grant_code_id: int
    provider_discount_id: str
    terms: Terms


def _live(conn: psycopg.Connection, grant_code_id: int, *, provider: str, now: datetime) -> Live | None:
    row = conn.execute(
        f"""SELECT g.id, g.percent_off, g.duration, g.duration_months, g.provider, g.provider_discount_id,
                   g.sync_status, g.max_redemptions, g.expires_at, g.revoked_at, {USED} AS used
            FROM grant_codes g WHERE g.id = %s AND g.kind = 'discount'""",  # noqa: S608 (a fixed fragment)
        (grant_code_id,),
    ).fetchone()
    if (
        row is None
        or row["revoked_at"] is not None
        or (row["expires_at"] is not None and row["expires_at"] <= now)
        or (row["max_redemptions"] is not None and row["used"] >= row["max_redemptions"])
        or row["sync_status"] != "synced"
        or row["provider"] != provider
        or not row["provider_discount_id"]
    ):
        return None
    terms = Terms(row["percent_off"], row["duration"], row["duration_months"])
    return Live(row["id"], row["provider_discount_id"], terms)


def find_live(
    conn: psycopg.Connection, code: str | None, *, provider: str | None, now: datetime | None = None
) -> Live | None:
    """The discount code a person typed, if it works now with this server's provider. None for every way it does not, and
    always None without a provider (billing off): a discount needs a checkout to apply to."""
    if provider is None:
        return None
    found = find_code(conn, code)
    return None if found is None else _live(conn, found, provider=provider, now=now or datetime.now(UTC))


def remember(
    conn: psycopg.Connection, user_id: UUID, code: str | None, *, provider: str | None, now: datetime | None = None
) -> Terms | None:
    """Keep a working discount code for this person's next checkout; a newer one replaces an older one. None when the code
    does not work (the caller gives the one refusal sentence)."""
    moment = now or datetime.now(UTC)
    live = find_live(conn, code, provider=provider, now=moment)
    if live is None:
        return None
    conn.execute(
        """INSERT INTO pending_promos (user_id, grant_code_id, created_at) VALUES (%s, %s, %s)
           ON CONFLICT (user_id) DO UPDATE SET grant_code_id = EXCLUDED.grant_code_id, created_at = EXCLUDED.created_at""",
        (user_id, live.grant_code_id, moment),
    )
    return live.terms


def pending(
    conn: psycopg.Connection, user_id: UUID, *, provider: str | None, now: datetime | None = None
) -> Live | None:
    """The discount this person's next checkout carries, if they have one that still works. One that stopped working (it
    was revoked, ran out or expired meanwhile) is forgotten here, so it is never shown again."""
    row = conn.execute("SELECT grant_code_id FROM pending_promos WHERE user_id = %s", (user_id,)).fetchone()
    if row is None or provider is None:
        return None
    live = _live(conn, row["grant_code_id"], provider=provider, now=now or datetime.now(UTC))
    if live is None:
        conn.execute("DELETE FROM pending_promos WHERE user_id = %s", (user_id,))
    return live


def forget_used(conn: psycopg.Connection, user_id: UUID) -> None:
    """A subscription came of a checkout: the discount it carried has done its work (as the webhook role)."""
    conn.execute(
        """DELETE FROM pending_promos p WHERE p.user_id = %s AND EXISTS (
               SELECT 1 FROM billing_checkouts c
               WHERE c.user_id = p.user_id AND c.grant_code_id = p.grant_code_id AND c.completed_at IS NOT NULL)""",
        (user_id,),
    )
