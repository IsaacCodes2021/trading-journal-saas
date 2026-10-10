"""Joining the beta on Postgres: the sign-in role calls the database's rules (migration 0014) and nothing else."""

from __future__ import annotations

from ..pg import beta as pg_beta
from ..pg import invites as pg_invites
from ..pg.system import SystemDatabase
from .ports import BetaCode


class PgBetaService:
    """`ports.BetaService`. The code is made here, handed back once for the email, and only its hash is stored."""

    def __init__(self, system: SystemDatabase) -> None:
        self._system = system

    def join(self, *, first_name: str, email: str, prop_firm: str) -> BetaCode | None:
        code = pg_invites.generate_code()
        with self._system.system_session("login") as conn:
            signup = pg_beta.join(conn, email=email, first_name=first_name, prop_firm=prop_firm, code=code)
        return None if signup is None else BetaCode(signup, code)

    def email_result(self, signup_id: int, *, ok: bool) -> None:
        with self._system.system_session("login") as conn:
            pg_beta.email_result(conn, signup_id, ok=ok)
