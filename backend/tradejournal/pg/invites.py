"""Invitation codes for the private beta (doc 03 section 2, doc 04 T18).

A code is stored hashed. Whoever checks one gets the same answer for every kind of failure (unknown, revoked, expired,
used up, or meant for another email), so a guess teaches nothing. Creating a code needs the schema owner (the CLI
below); checking and redeeming happen as the `login` role during sign-in.

    TJ_OWNER_URL=postgresql://tj_owner:...@host/db python -m tradejournal.pg.invites create --label "Sam, futures group"
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg

# No 0, O, 1, I or L: a code is read aloud and typed from an email.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_SHAPE = re.compile(r"^[A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+$")


def normalize(code: str) -> str:
    """ "tj 7kq2-m9xa" is "TJ-7KQ2-M9XA": trimmed, capitals, spaces and underscores as dashes (the page does the same)."""
    return re.sub(r"[\s_]+", "-", code.strip().upper())


def hash_code(code: str) -> bytes:
    return hashlib.sha256(normalize(code).encode()).digest()


def generate_code() -> str:
    """TJ-XXXX-XXXX from 31 letters and digits: about 40 bits, which is fine for a code that is checked against a
    hash, rate limited, and worth nothing without a sign-in behind it."""
    return "TJ-" + "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(2))


@dataclass(frozen=True)
class Invite:
    id: int
    grant_code_id: int | None = None  # a plan grant that comes with this invite (see `pg.plans`)


def check(conn: psycopg.Connection, code: str | None, email: str, *, now: datetime | None = None) -> Invite | None:
    """The invite this code is, if it can be used now by this email. One answer (None) for every way it cannot."""
    if not code:
        return None
    normalized = normalize(code)
    if len(normalized) > 40 or not _SHAPE.fullmatch(normalized):
        return None
    moment = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT id, email, max_uses, expires_at, revoked_at, grant_code_id FROM invites WHERE code_hash = %s",
        (hash_code(normalized),),
    ).fetchone()
    if row is None or row["revoked_at"] is not None or (row["expires_at"] and row["expires_at"] <= moment):
        return None
    if row["email"] is not None and row["email"].casefold() != email.casefold():
        return None
    used = conn.execute("SELECT count(*) AS n FROM invite_redemptions WHERE invite_id = %s", (row["id"],)).fetchone()[
        "n"
    ]
    return Invite(row["id"], row["grant_code_id"]) if used < row["max_uses"] else None


@dataclass(frozen=True)
class UsableInvite:
    """What can be said about a code before anyone has signed in: that it works, and which address it is for."""

    id: int
    email: str | None  # the address it is tied to, if any (used to pre-fill the provider's page)


def lookup(conn: psycopg.Connection, code: str | None, *, now: datetime | None = None) -> UsableInvite | None:
    """The invite this code is, if it could still be used by *somebody*: it exists, is not revoked or expired, and has a use
    left. Nothing is said about whom it is for beyond the stored address. One answer (None) for every way it cannot. This is
    the check made before the browser is sent to the identity provider; `check` still runs after, against the verified
    address, and decides."""
    if not code:
        return None
    normalized = normalize(code)
    if len(normalized) > 40 or not _SHAPE.fullmatch(normalized):
        return None
    moment = now or datetime.now(UTC)
    row = conn.execute(
        """SELECT i.id, i.email, i.max_uses, i.expires_at, i.revoked_at,
                  (SELECT count(*) FROM invite_redemptions r WHERE r.invite_id = i.id) AS used
           FROM invites i WHERE i.code_hash = %s""",
        (hash_code(normalized),),
    ).fetchone()
    if row is None or row["revoked_at"] is not None or (row["expires_at"] and row["expires_at"] <= moment):
        return None
    return UsableInvite(row["id"], row["email"]) if row["used"] < row["max_uses"] else None


def redeem(conn: psycopg.Connection, invite: Invite, user_id, *, now: datetime | None = None) -> bool:
    """Use the invite for this user, inside the sign-up transaction. False (and nothing recorded) if it was used up in
    the meantime: two people redeeming the last use at once cannot both get it."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(%s, %s)", (7_283_002, invite.id)
    )  # one redemption of this invite at a time
    row = conn.execute(
        """SELECT i.max_uses, i.revoked_at, i.expires_at,
                  (SELECT count(*) FROM invite_redemptions r WHERE r.invite_id = i.id) AS used
           FROM invites i WHERE i.id = %s""",
        (invite.id,),
    ).fetchone()
    moment = now or datetime.now(UTC)
    if (
        row is None
        or row["revoked_at"] is not None
        or (row["expires_at"] and row["expires_at"] <= moment)
        or row["used"] >= row["max_uses"]
    ):
        return False
    conn.execute(
        "INSERT INTO invite_redemptions (invite_id, user_id, redeemed_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (invite.id, user_id, moment),
    )
    return True


def create_invite(
    owner_conn: psycopg.Connection,
    *,
    label: str = "",
    email: str | None = None,
    max_uses: int = 1,
    expires_in: timedelta | None = None,
    grant_code_id: int | None = None,
) -> str:
    """Make a code (as the schema owner) and return it. It is shown once: only its hash is kept. `grant_code_id` is a
    plan code (`pg.plans`) whose plan the person gets when they sign up with this invite."""
    if max_uses < 1:
        raise ValueError("an invite must allow at least one use")
    code = generate_code()
    owner_conn.execute(
        "INSERT INTO invites (code_hash, label, email, max_uses, expires_at, grant_code_id) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            hash_code(code),
            label.strip(),
            email.strip() if email else None,
            max_uses,
            datetime.now(UTC) + expires_in if expires_in else None,
            grant_code_id,
        ),
    )
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an invitation code (as the schema owner).")
    parser.add_argument("command", choices=["create"])
    parser.add_argument("--label", default="", help="who it is for, in your words (shown to you, never to them)")
    parser.add_argument("--email", help="only this address may use it")
    parser.add_argument("--max-uses", type=int, default=1)
    parser.add_argument("--days", type=int, help="expires after this many days")
    parser.add_argument("--grant-id", type=int, help="a plan code (see tradejournal.pg.plans) whose plan they get")
    args = parser.parse_args(argv)
    url = os.environ.get("TJ_OWNER_URL")
    if not url:
        print("TJ_OWNER_URL is not set", file=sys.stderr)
        return 2
    with psycopg.connect(url, autocommit=True) as conn:
        code = create_invite(
            conn,
            label=args.label,
            email=args.email,
            max_uses=args.max_uses,
            expires_in=timedelta(days=args.days) if args.days else None,
            grant_code_id=args.grant_id,
        )
    print(code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
