"""The real account service, on Postgres. Sessions are not here: they arrive with sign-in (M5)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import psycopg

from ..entitlements import limits_for
from ..pg import audit as pg_audit
from ..pg import deletion as pg_deletion
from ..pg import discounts as pg_discounts
from ..pg import plans as pg_plans
from ..pg import settings as pg_settings
from ..pg import store as pg_store
from ..pg.session import HEAVY_STATEMENT_TIMEOUT_MS, Database
from ..pg.system import SystemDatabase
from .ports import Account, GrantResult
from .schemas import AccessOut, DiscountOut, PlanOut, SettingsOut, SettingsPatch, UserOut

_DAY_SETTINGS = ("trading_day_timezone", "trading_day_start")


def discount_out(terms: pg_discounts.Terms) -> DiscountOut:
    return DiscountOut(
        percent_off=terms.percent_off,
        duration=terms.duration,
        duration_months=terms.duration_months,
        words=terms.words,
    )


class PgAccountService:
    """`load` and `update_settings` for the signed-in user (`ports.AccountService`).

    Who the user is comes through the `login` system role (the request role cannot read the users table); their
    settings come through a tenant session like everything else of theirs."""

    def __init__(
        self,
        database: Database,
        system: SystemDatabase,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        billing_enabled: bool = False,
        billing_provider: str | None = None,
    ) -> None:
        self._database = database
        self._system = system
        self._clock = clock
        self._billing = billing_enabled  # on: nothing live means read-only (entitlements.access_of)
        # The provider whose discount codes work here ("fake" or "paddle"); None while billing is off: no discount works.
        self._provider = billing_provider if billing_enabled else None

    def load(self, user_id: UUID) -> Account | None:
        with self._system.system_session("login") as conn:
            row = conn.execute("SELECT id, email, status, is_admin FROM users WHERE id = %s", (user_id,)).fetchone()
            now = self._clock()
            plan = pg_plans.effective(conn, user_id, now) if row is not None else None
            access = pg_plans.access(conn, user_id, now, plan=plan, billing=self._billing) if plan is not None else None
        if row is None or plan is None or access is None or row["status"] != "active":
            return None  # gone, suspended or being deleted: not signed in
        with self._database.tenant_session(user_id) as conn:
            settings = _settings(conn)
        return Account(
            user=UserOut(id=row["id"], email=row["email"]),
            settings=settings,
            plan=PlanOut(effective=plan.plan, source=plan.source, expires_at=plan.expires_at),
            entitlements=limits_for(plan.plan),
            is_admin=row["is_admin"],
            access=AccessOut(state=access.state, ends_at=access.ends_at, reason=access.reason),
        )

    def access(self, user_id: UUID) -> AccessOut:
        with self._system.system_session("login") as conn:
            state = pg_plans.access(conn, user_id, self._clock(), billing=self._billing)
        return AccessOut(state=state.state, ends_at=state.ends_at, reason=state.reason)

    def redeem_grant(self, user_id: UUID, code: str, *, ip_hash: str | None = None) -> GrantResult:
        with self._system.system_session("login") as conn:
            now = self._clock()
            outcome = pg_plans.redeem(conn, user_id, code, now=now)
            if isinstance(outcome, pg_plans.Redeemed):
                pg_audit.record(
                    conn,
                    "grant_redeemed",
                    user_id=user_id,
                    ip_hash=ip_hash,
                    meta={"plan": outcome.plan, "days": outcome.days, "via": "settings"},
                )
                plan = pg_plans.effective(conn, user_id, now)
                return GrantResult(
                    "ok", plan=PlanOut(effective=plan.plan, source=plan.source, expires_at=plan.expires_at)
                )
            if isinstance(outcome, pg_plans.Refused):
                # Not a free-days code that works: perhaps a discount code, saved for the next checkout.
                terms = pg_discounts.remember(conn, user_id, code, provider=self._provider, now=now)
                if terms is not None:
                    pg_audit.record(
                        conn,
                        "discount_saved",
                        user_id=user_id,
                        ip_hash=ip_hash,
                        meta={"via": "settings"},
                    )
                    plan = pg_plans.effective(conn, user_id, now)
                    return GrantResult(
                        "discount",
                        plan=PlanOut(effective=plan.plan, source=plan.source, expires_at=plan.expires_at),
                        discount=discount_out(terms),
                    )
            # A refusal is worth a row (many in a row is a guesser), but never the code.
            reason = "active" if isinstance(outcome, pg_plans.AlreadyHave) else "refused"
            pg_audit.record(conn, "grant_refused", user_id=user_id, ip_hash=ip_hash, meta={"reason": reason})
            return GrantResult("active", ends_at=outcome.ends_at) if reason == "active" else GrantResult("refused")

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
        with self._database.tenant_session(user_id, statement_timeout_ms=HEAVY_STATEMENT_TIMEOUT_MS) as conn:
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
