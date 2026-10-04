"""The operator's tools on Postgres (`ports.AdminService`; doc 07 section 6, ADR-012).

Connects only as the `admin` role: no grant on any tenant table, so it can read counters and account facts and never a
trade, a note or a screenshot. It writes one audit row for every change and for every look at one person's detail, in the
same transaction as the change, so there is no change without its record.

Who may act on whom is decided here, once, for every action: an operator cannot suspend, delete, take sessions from or
change the plan of their own account (they would lock themselves out or give themselves things, and a second operator
should be the one to do it), and cannot do any of those to another operator (operators are made and removed with the
command line, not by each other). Looking is allowed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import psycopg

from ..pg import admin as pg_admin
from ..pg import audit as pg_audit
from ..pg import deletion as pg_deletion
from ..pg.system import SystemDatabase
from .ports import AdminRefusal, SessionStore
from .schemas import PlanOut
from .schemas.admin import (
    AdminUserDetailOut,
    AdminUserListOut,
    AdminUserOut,
    EventOut,
    OverrideOut,
    RedemptionOut,
    UsageOut,
)

ADMIN = "admin"
SELF_REFUSED = "You cannot do that to your own account."
OPERATOR_REFUSED = "That account is an operator. Operators are changed with the command line, not here."
NOT_FOUND = "There is no such account."


def user_out(row: pg_admin.UserRow) -> AdminUserOut:
    return AdminUserOut(
        id=row.id,
        email=row.email,
        status=row.status,  # type: ignore[arg-type]  # a CHECK constraint holds it to the three statuses
        is_admin=row.is_admin,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
        deletion_scheduled_at=row.deletion_scheduled_at,
        plan=PlanOut(effective=row.plan.plan, source=row.plan.source, expires_at=row.plan.expires_at),
        usage=UsageOut(
            accounts=row.usage.accounts,
            trades=row.usage.trades,
            screenshots=row.usage.screenshots,
            storage_bytes=row.usage.storage_bytes,
            last_import_at=row.usage.last_import_at,
        ),
    )


def _by(actor: UUID) -> str:
    return f"admin:{actor}"


class PgAdminService:
    def __init__(
        self,
        system: SystemDatabase,
        sessions: SessionStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._system = system
        self._sessions = sessions
        self._clock = clock

    # ---- looking -----------------------------------------------------------------------------------------------------

    def list_users(self, *, q: str | None, status: str | None, limit: int, offset: int) -> AdminUserListOut:
        with self._system.system_session(ADMIN) as conn:
            rows, total = pg_admin.list_users(conn, q=q, status=status, limit=limit, offset=offset, now=self._clock())
        return AdminUserListOut(users=[user_out(r) for r in rows], total=total, limit=limit, offset=offset)

    def user_detail(self, actor: UUID, target: UUID, *, ip_hash: str | None = None) -> AdminUserDetailOut:
        with self._system.system_session(ADMIN) as conn:
            detail = pg_admin.get_user(conn, target, now=self._clock())
            if detail is None:
                raise AdminRefusal(404, NOT_FOUND)
            # A look at one person's account is itself an event: the log says who looked, and at whom.
            self._record(conn, "admin_viewed_user", actor, target, ip_hash)
        return AdminUserDetailOut(
            user=user_out(detail.user),
            active_sessions=detail.active_sessions,
            overrides=[OverrideOut(**vars(o)) for o in detail.overrides],  # type: ignore[arg-type]
            redemptions=[
                RedemptionOut(code_label=r.label, plan=r.plan, starts_at=r.starts_at, ends_at=r.ends_at)
                for r in detail.redemptions
            ],
            events=[
                EventOut(id=e.id, at=e.at, actor=e.actor, action=e.action, by=e.by, meta=e.meta) for e in detail.events
            ],
        )

    # ---- changing ----------------------------------------------------------------------------------------------------

    def suspend(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> AdminUserOut:
        with self._system.system_session(ADMIN) as conn:
            self._may_change(conn, actor, target)
            if not pg_admin.set_status(conn, target, to="suspended"):
                raise AdminRefusal(409, "That account is not active, so it cannot be suspended.")
            self._record(conn, "admin_suspended", actor, target, ip_hash, reason=reason)
            out = self._reload(conn, target)
        self._sessions.revoke_all(target)  # a suspended person is signed out at once on this instance
        return out

    def unsuspend(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> AdminUserOut:
        with self._system.system_session(ADMIN) as conn:
            self._may_change(conn, actor, target)
            if not pg_admin.set_status(conn, target, to="active"):
                raise AdminRefusal(409, "That account is not suspended.")
            self._record(conn, "admin_unsuspended", actor, target, ip_hash, reason=reason)
            return self._reload(conn, target)

    def revoke_sessions(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> int:
        with self._system.system_session(ADMIN) as conn:
            self._may_change(conn, actor, target)
        ended = self._sessions.revoke_all(target)
        with self._system.system_session(ADMIN) as conn:
            self._record(conn, "admin_sessions_revoked", actor, target, ip_hash, reason=reason, ended=ended)
        return ended

    def schedule_deletion(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> AdminUserOut:
        with self._system.system_session(ADMIN) as conn:
            self._may_change(conn, actor, target)
            when = pg_deletion.request(conn, target, now=self._clock())
            if when is None:
                raise AdminRefusal(409, "That account is not active, so deletion cannot be scheduled.")
            self._record(conn, "admin_deletion_scheduled", actor, target, ip_hash, reason=reason)
            out = self._reload(conn, target)
        self._sessions.revoke_all(target)
        return out

    def cancel_deletion(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> AdminUserOut:
        with self._system.system_session(ADMIN) as conn:
            self._may_change(conn, actor, target)
            if not pg_deletion.cancel(conn, target):
                raise AdminRefusal(409, "That account is not waiting to be deleted.")
            self._record(conn, "admin_deletion_cancelled", actor, target, ip_hash, reason=reason)
            return self._reload(conn, target)

    def set_override(
        self,
        actor: UUID,
        target: UUID,
        plan: str,
        expires_at: datetime | None,
        reason: str,
        *,
        ip_hash: str | None = None,
    ) -> AdminUserOut:
        now = self._clock()
        if expires_at is not None and expires_at <= now:
            raise AdminRefusal(422, "An override has to end in the future (or not end at all).")
        with self._system.system_session(ADMIN) as conn:
            self._may_change(conn, actor, target)
            replaced = pg_admin.set_override(
                conn, target, plan=plan, expires_at=expires_at, reason=reason, by=_by(actor), now=now
            )
            self._record(
                conn,
                "admin_plan_override_set",
                actor,
                target,
                ip_hash,
                reason=reason,
                plan=plan,
                ends=expires_at.isoformat() if expires_at else None,
                replaced=replaced,
            )
            return self._reload(conn, target)

    def revoke_override(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> AdminUserOut:
        with self._system.system_session(ADMIN) as conn:
            self._may_change(conn, actor, target)
            if not pg_admin.revoke_override(conn, target, reason=reason, by=_by(actor), now=self._clock()):
                raise AdminRefusal(409, "That account has no plan override to revoke.")
            self._record(conn, "admin_plan_override_revoked", actor, target, ip_hash, reason=reason)
            return self._reload(conn, target)

    # ---- the rules and the record ------------------------------------------------------------------------------------

    def _may_change(self, conn: psycopg.Connection, actor: UUID, target: UUID) -> None:
        """Refuse before anything changes: no such account, one's own account, or another operator's."""
        row = conn.execute("SELECT is_admin FROM users WHERE id = %s", (target,)).fetchone()
        if row is None:
            raise AdminRefusal(404, NOT_FOUND)
        if actor == target:
            raise AdminRefusal(422, SELF_REFUSED)
        if row["is_admin"]:
            raise AdminRefusal(422, OPERATOR_REFUSED)

    def _reload(self, conn: psycopg.Connection, target: UUID) -> AdminUserOut:
        detail = pg_admin.get_user(conn, target, now=self._clock(), events=1)
        if detail is None:  # it was there a moment ago, in this transaction; a delete in between is the only way
            raise AdminRefusal(404, NOT_FOUND)
        return user_out(detail.user)

    def _record(
        self, conn: psycopg.Connection, action: str, actor: UUID, target: UUID, ip_hash: str | None, **meta
    ) -> None:
        pg_audit.record(
            conn, action, user_id=target, actor=pg_audit.ACTOR_ADMIN, by=actor, ip_hash=ip_hash, meta=meta or None
        )
