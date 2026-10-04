"""The real account service, on Postgres. Sessions are not here: they arrive with sign-in (M5)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import psycopg

from ..pg import audit as pg_audit
from ..pg import deletion as pg_deletion
from ..pg import settings as pg_settings
from ..pg import store as pg_store
from ..pg.session import Database
from ..pg.system import SystemDatabase
from .ports import Account
from .schemas import PlanOut, SettingsOut, SettingsPatch, UserOut

_DAY_SETTINGS = ("trading_day_timezone", "trading_day_start")


class PgAccountService:
    """`load` and `update_settings` for the signed-in user (`ports.AccountService`).

    Who the user is comes through the `login` system role (the request role cannot read the users table); their
    settings come through a tenant session like everything else of theirs."""

    def __init__(
        self, database: Database, system: SystemDatabase, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._database = database
        self._system = system
        self._clock = clock

    def load(self, user_id: UUID) -> Account | None:
        with self._system.system_session("login") as conn:
            row = conn.execute("SELECT id, email, status, is_admin FROM users WHERE id = %s", (user_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None  # gone, suspended or being deleted: not signed in
        with self._database.tenant_session(user_id) as conn:
            settings = _settings(conn)
        return Account(
            user=UserOut(id=row["id"], email=row["email"]),
            settings=settings,
            # Plans, grants and overrides are computed from billing tables that arrive with M9 and M10; until then
            # everyone is on the free plan with no limits listed.
            plan=PlanOut(effective="free", source="free", expires_at=None),
            entitlements={},
            is_admin=row["is_admin"],
        )

    def schedule_deletion(self, user_id: UUID, *, ip_hash: str | None = None) -> datetime | None:
        with self._system.system_session("login") as conn:
            when = pg_deletion.request(conn, user_id, now=self._clock())
            if when is not None:  # the change and its record are one transaction
                pg_audit.record(
                    conn,
                    "deletion_requested",
                    user_id=user_id,
                    ip_hash=ip_hash,
                    meta={"scheduled_for": when.isoformat()},
                )
        return when

    def update_settings(self, user_id: UUID, changes: SettingsPatch) -> SettingsOut:
        """Apply the fields that are set and return all settings, in one transaction. Moving the trading day moves
        which day each stored trade belongs to, so the trades are recalculated in the same transaction."""
        with self._database.tenant_session(user_id) as conn:
            before = _settings(conn)
            for key in ("timezone", "trading_day_timezone", "trading_day_start", "theme"):
                value = getattr(changes, key)
                if value is not None:
                    pg_settings.set_setting(conn, key, value)
            if changes.csv_timezone is not None:
                pg_settings.set_csv_timezone(conn, changes.csv_timezone or None)  # "" follows `timezone` again
            after = _settings(conn)
            if any(getattr(before, key) != getattr(after, key) for key in _DAY_SETTINGS):
                pg_store.recalculate_trades(conn)
            return after


def _settings(conn: psycopg.Connection) -> SettingsOut:
    return SettingsOut(
        timezone=pg_settings.get_setting(conn, "timezone"),
        csv_timezone=pg_settings.get_csv_timezone(conn),
        trading_day_timezone=pg_settings.get_setting(conn, "trading_day_timezone"),
        trading_day_start=pg_settings.get_setting(conn, "trading_day_start"),
        theme=pg_settings.get_setting(conn, "theme"),  # type: ignore[arg-type]  # a CHECK constraint holds it to the two themes
    )
