"""Joining the beta: a person gives an address, a first name and a favourite prop firm and is made a personal code.

The rules live in the database (migration 0014: `beta_join`), because the request that takes the form has no session and the
sign-in role may not make invites on its own. This module is the thin, typed way to call them. The code is made here, shown to
nobody, and goes to the person by email: it is returned only so the caller can send it, and only its hash is stored.

    code = invites.generate_code()
    signup = beta.join(conn, email=..., first_name=..., prop_firm=..., code=code)   # an id, or None: send nothing
    if signup is not None:
        ...send the email with `code`...
        beta.email_result(conn, signup, ok=True)
"""

from __future__ import annotations

import psycopg

from .invites import hash_code


def join(conn: psycopg.Connection, *, email: str, first_name: str, prop_firm: str, code: str) -> int | None:
    """Record the sign-up and make the personal code `code` for it. Returns the sign-up's id when a code was made (the
    caller sends it), or None when nothing was made: a malformed address, an address that already has an account or
    already used its code, a code sent less than an hour ago, or five made already. None says nothing about which."""
    row = conn.execute(
        "SELECT beta_join(%s, %s, %s, %s) AS signup", (email, first_name, prop_firm, hash_code(code))
    ).fetchone()
    return row["signup"]


def email_result(conn: psycopg.Connection, signup_id: int, *, ok: bool) -> None:
    """Whether the email carrying the latest code left. A failure lets the person ask again at once."""
    conn.execute("SELECT beta_email_result(%s, %s)", (signup_id, ok))
