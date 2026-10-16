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
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

import psycopg

from ..billing.provider import BillingProvider
from ..billing.types import BillingError, DiscountSpec
from ..pg import admin as pg_admin
from ..pg import admin_beta as pg_beta
from ..pg import admin_codes as pg_codes
from ..pg import admin_log as pg_log
from ..pg import audit as pg_audit
from ..pg import deletion as pg_deletion
from ..pg import discounts as pg_discounts
from ..pg import invites as pg_invites
from ..pg import plans as pg_plans
from ..pg import rule_templates as pg_templates
from ..pg.system import SystemDatabase
from ..prop_rules import RuleValues
from .ports import AdminRefusal, BetaRevoked, SessionStore
from .rules_view import admin_template_out, template_key
from .schemas import PlanOut
from .schemas.admin import (
    AdminBillingEventOut,
    AdminSubscriptionOut,
    AdminUserDetailOut,
    AdminUserListOut,
    AdminUserOut,
    AuditEventOut,
    AuditPageOut,
    BetaListOut,
    BetaPersonOut,
    BetaRemovedOut,
    CreatedGrantCodeOut,
    CreatedInviteOut,
    EventOut,
    GrantCodeListOut,
    GrantCodeOut,
    InviteListOut,
    InviteOut,
    OverrideOut,
    OverviewOut,
    RedemptionOut,
    UsageOut,
)
from .schemas.rules import AdminRuleTemplateListOut, AdminRuleTemplateOut, RuleTemplateImportOut

ADMIN = "admin"
SELF_REFUSED = "You cannot do that to your own account."
OPERATOR_REFUSED = "That account is an operator. Operators are changed with the command line, not here."
NOT_FOUND = "There is no such account."
NO_INVITE = "There is no such invite."
NO_GRANT = "There is no such plan code."
CODE_TAKEN = "That code is already in use. Codes are never reused, even revoked ones: choose another."
BAD_CODE = "A promo code is 3 to 20 letters and digits, with no spaces or dashes, such as FREE30."
NO_PERSON = "There is no such person in the beta."
HAS_ACCOUNT = "They have an account. Delete the account instead: that removes the sign-up and its codes with it."
NO_TEMPLATE = "There is no such rule template."
TEMPLATE_CLASH = "Another rule template already has that firm, program, size, phase and version."
NOTHING_TO_REVOKE = (
    "There is nothing to revoke: their access has already ended, or their code is used up, expired or withdrawn."
)


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


def invite_out(row: pg_codes.InviteRow, now: datetime) -> InviteOut:
    return InviteOut(
        id=row.id,
        label=row.label,
        email=row.email,
        max_uses=row.max_uses,
        used=row.used,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        grant_code_id=row.grant_code_id,
        grant_label=row.grant_label,
        status=row.status(now),  # type: ignore[arg-type]
    )


def beta_person_out(row: pg_beta.BetaRow) -> BetaPersonOut:
    return BetaPersonOut(
        id=row.id,
        first_name=row.first_name,
        email=row.email,
        prop_firm=row.prop_firm,
        joined_at=row.joined_at,
        state=row.state,
        code=row.code,  # type: ignore[arg-type]  # one of the four the query produces
        access_ends_at=row.access_ends_at,
        revoked_at=row.access_revoked_at,
        notice_sent_at=row.ended_notice_sent_at,
        code_email_sent_at=row.email_sent_at,
        code_email_failures=row.email_failures,
    )


def grant_out(row: pg_codes.GrantRow, now: datetime) -> GrantCodeOut:
    return GrantCodeOut(
        id=row.id,
        code=row.code,
        kind=row.kind,  # type: ignore[arg-type]  # a CHECK constraint holds it to the kinds the schema names
        label=row.label,
        plan=row.plan,  # type: ignore[arg-type]  # a CHECK constraint holds it to 'pro'
        days=row.days,
        max_redemptions=row.max_redemptions,
        used=row.used,
        percent_off=row.percent_off,
        duration=row.duration,  # type: ignore[arg-type]  # a CHECK constraint holds it
        duration_months=row.duration_months,
        words=pg_discounts.describe(row.percent_off, row.duration, row.duration_months)
        if row.percent_off is not None and row.duration is not None
        else None,
        sync_status=row.sync_status,  # type: ignore[arg-type]
        sync_error=row.sync_error,
        expires_at=row.expires_at,
        created_by=row.created_by,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        status=row.status(now),  # type: ignore[arg-type]
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
        billing: BillingProvider | None = None,
        billing_enabled: bool = False,
    ) -> None:
        self._system = system
        self._sessions = sessions
        self._clock = clock
        self._billing = billing  # the payment provider discount codes are pushed to; None: they wait as "pending"
        self._billing_enabled = billing_enabled

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
            subscription=AdminSubscriptionOut(**detail.subscription) if detail.subscription else None,
            billing_events=[AdminBillingEventOut(**b) for b in detail.billing_events],
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

    # ---- invites and plan codes --------------------------------------------------------------------------------------

    def list_invites(self, *, limit: int, offset: int) -> InviteListOut:
        now = self._clock()
        with self._system.system_session(ADMIN) as conn:
            rows, total = pg_codes.list_invites(conn, limit=limit, offset=offset)
        return InviteListOut(invites=[invite_out(r, now) for r in rows], total=total, limit=limit, offset=offset)

    def create_invite(
        self,
        actor: UUID,
        *,
        label: str,
        email: str | None,
        max_uses: int,
        days: int | None,
        grant_code_id: int | None,
        ip_hash: str | None = None,
    ) -> CreatedInviteOut:
        now = self._clock()
        code = pg_invites.generate_code()
        with self._system.system_session(ADMIN) as conn:
            if grant_code_id is not None and not pg_codes.grant_exists(conn, grant_code_id, now=now):
                raise AdminRefusal(422, "That plan code does not exist, has expired or has been revoked.")
            invite_id = pg_codes.create_invite(
                conn,
                code_hash=pg_invites.hash_code(code),
                label=label,
                email=email.lower() if email else None,
                max_uses=max_uses,
                expires_at=now + timedelta(days=days) if days else None,
                grant_code_id=grant_code_id,
            )
            # The record says what was made and never the code, nor whom it was for.
            self._record(
                conn,
                "admin_invite_created",
                actor,
                None,
                ip_hash,
                invite=invite_id,
                label=label,
                max_uses=max_uses,
                days=days,
                grant=grant_code_id,
                for_one_address=email is not None,
            )
            row = pg_codes.get_invite(conn, invite_id)
        if row is None:
            raise AdminRefusal(404, NO_INVITE)
        return CreatedInviteOut(invite=invite_out(row, now), code=code)

    def revoke_invite(self, actor: UUID, invite_id: int, reason: str, *, ip_hash: str | None = None) -> InviteOut:
        now = self._clock()
        with self._system.system_session(ADMIN) as conn:
            if pg_codes.get_invite(conn, invite_id) is None:
                raise AdminRefusal(404, NO_INVITE)
            if not pg_codes.revoke_invite(conn, invite_id, now=now):
                raise AdminRefusal(409, "That invite is already revoked.")
            self._record(conn, "admin_invite_revoked", actor, None, ip_hash, invite=invite_id, reason=reason)
            row = pg_codes.get_invite(conn, invite_id)
        if row is None:
            raise AdminRefusal(404, NO_INVITE)
        return invite_out(row, now)

    def list_grant_codes(self, *, limit: int, offset: int, show: str | None = None) -> GrantCodeListOut:
        now = self._clock()
        with self._system.system_session(ADMIN) as conn:
            rows, total = pg_codes.list_grant_codes(conn, limit=limit, offset=offset, show=show, now=now)
        return GrantCodeListOut(
            grant_codes=[grant_out(r, now) for r in rows],
            discounts_live=self._billing is not None and self._billing_enabled,
            total=total,
            limit=limit,
            offset=offset,
        )

    def create_grant_code(
        self,
        actor: UUID,
        *,
        label: str,
        plan: str,
        days: int | None,
        max_redemptions: int | None,
        expires_in_days: int | None,
        code: str | None = None,
        expires_on: date | None = None,
        ip_hash: str | None = None,
        kind: str = "free_days",
        percent_off: int | None = None,
        duration: str | None = None,
        duration_months: int | None = None,
    ) -> CreatedGrantCodeOut:
        """Make a plan code. A typed `code` (FREE30) is kept readable and may appear in the audit log; a random one is shown
        in this answer only. A typed code already in use, in any case and even revoked, is refused: codes are never reused."""
        now = self._clock()
        typed = pg_plans.typed_code(code) if code is not None else None
        if code is not None and typed is None:
            raise AdminRefusal(422, BAD_CODE)
        discount = kind == "discount"
        if discount and (typed is None or percent_off is None or duration is None):
            raise AdminRefusal(422, "A discount code is typed (such as LAUNCH20) and has a percentage and a duration.")
        made = typed or pg_plans.generate_code()
        if expires_on is not None:
            expires_at: datetime | None = datetime.combine(expires_on + timedelta(days=1), time(), tzinfo=UTC)
            if expires_at <= now:
                raise AdminRefusal(422, "That date has already passed. Choose a day from today on.")
        else:
            expires_at = now + timedelta(days=expires_in_days) if expires_in_days else None
        with self._system.system_session(ADMIN) as conn:
            if typed is not None and pg_codes.code_in_use(conn, typed):
                raise AdminRefusal(422, CODE_TAKEN)
            try:
                with (
                    conn.transaction()
                ):  # a savepoint: a duplicate made a moment ago by someone else is refused cleanly
                    if discount:
                        grant_id = pg_codes.create_discount_code(
                            conn,
                            code_hash=pg_invites.hash_code(made),
                            code=made,
                            label=label,
                            percent_off=percent_off,  # type: ignore[arg-type]  # checked above
                            duration=duration,  # type: ignore[arg-type]
                            duration_months=duration_months,
                            max_redemptions=max_redemptions,
                            expires_at=expires_at,
                            created_by=_by(actor),
                        )
                    else:
                        grant_id = pg_codes.create_grant_code(
                            conn,
                            code_hash=pg_invites.hash_code(made),
                            code=typed,
                            label=label,
                            plan=plan,
                            days=days,  # type: ignore[arg-type]  # the schema requires it for free days
                            max_redemptions=max_redemptions,
                            expires_at=expires_at,
                            created_by=_by(actor),
                        )
            except psycopg.errors.UniqueViolation:
                raise AdminRefusal(422, CODE_TAKEN) from None
            # A typed code is meant to be shared, so the log may name it; a random one never appears in the log.
            named = {"code": typed} if typed is not None else {}
            self._record(
                conn,
                "admin_grant_code_created",
                actor,
                None,
                ip_hash,
                grant=grant_id,
                **named,
                label=label,
                plan=plan,
                **(
                    {"kind": kind, "percent_off": percent_off, "duration": duration, "duration_months": duration_months}
                    if discount
                    else {"days": days}
                ),
                max_redemptions=max_redemptions,
            )
        if discount:  # saved first; the provider's copy follows, outside the transaction (it can be slow, or fail)
            self._push(grant_id, actor, ip_hash)
        with self._system.system_session(ADMIN) as conn:
            row = pg_codes.get_grant_code(conn, grant_id)
        if row is None:
            raise AdminRefusal(404, NO_GRANT)
        return CreatedGrantCodeOut(grant_code=grant_out(row, now), code=made)

    def sync_grant_code(self, actor: UUID, grant_id: int, *, ip_hash: str | None = None) -> GrantCodeOut:
        """Try again to give the provider its copy of a discount code (or to archive it, once revoked). Safe to repeat: a
        copy the provider already holds is found and adopted, never made twice."""
        with self._system.system_session(ADMIN) as conn:
            row = pg_codes.get_grant_code(conn, grant_id)
        if row is None:
            raise AdminRefusal(404, NO_GRANT)
        if row.kind != "discount":
            raise AdminRefusal(409, "Only a discount code has a copy at the payment provider.")
        if self._billing is None:
            raise AdminRefusal(409, "No payment provider is set up on this server, so there is nothing to sync with.")
        self._push(grant_id, actor, ip_hash)
        with self._system.system_session(ADMIN) as conn:
            row = pg_codes.get_grant_code(conn, grant_id)
        return grant_out(row, self._clock())  # type: ignore[arg-type]  # it existed a moment ago; codes are never deleted

    def _push(self, grant_id: int, actor: UUID, ip_hash: str | None) -> None:
        """Make the provider's copy of a discount code match the row: create it (adopting one the provider already holds
        with this code), or archive it once the code is revoked. Never raises: a provider that fails leaves the row
        `failed` with a short reason (never its message), which the operator sees and can retry."""
        provider = self._billing
        if provider is None:
            return  # no provider on this server: the code waits as "pending"
        with self._system.system_session(ADMIN) as conn:
            row = pg_codes.get_grant_code(conn, grant_id)
        if row is None or row.kind != "discount" or row.code is None:
            return
        discount_id = row.provider_discount_id
        try:
            if row.revoked_at is not None:
                if discount_id is not None:
                    provider.archive_discount(discount_id)
            elif discount_id is None:
                discount_id = provider.find_discount(row.code) or provider.create_discount(
                    DiscountSpec(
                        code=row.code,
                        percent_off=row.percent_off,  # type: ignore[arg-type]  # a CHECK holds a discount's terms
                        duration=row.duration,  # type: ignore[arg-type]
                        months=row.duration_months,
                        max_uses=row.max_redemptions,
                        expires_at=row.expires_at,
                        note=row.label,
                    )
                )
        except BillingError as problem:
            status, error = "failed", problem.reason
        else:
            status, error = "synced", None
        with self._system.system_session(ADMIN) as conn:
            pg_codes.set_sync(
                conn,
                grant_id,
                status=status,
                provider=provider.name,
                provider_discount_id=discount_id if status == "synced" else None,
                error=error,
                now=self._clock(),
            )
            outcome = {"reason": error} if error else {}
            self._record(
                conn,
                "admin_discount_synced",
                actor,
                None,
                ip_hash,
                grant=grant_id,
                code=row.code,
                status=status,
                **outcome,
            )

    def revoke_grant_code(self, actor: UUID, grant_id: int, reason: str, *, ip_hash: str | None = None) -> GrantCodeOut:
        now = self._clock()
        with self._system.system_session(ADMIN) as conn:
            if pg_codes.get_grant_code(conn, grant_id) is None:
                raise AdminRefusal(404, NO_GRANT)
            if not pg_codes.revoke_grant_code(conn, grant_id, now=now):
                raise AdminRefusal(409, "That plan code is already revoked.")
            typed = pg_codes.get_grant_code(conn, grant_id)
            named = {"code": typed.code} if typed is not None and typed.code is not None else {}
            self._record(conn, "admin_grant_code_revoked", actor, None, ip_hash, grant=grant_id, **named, reason=reason)
        if typed is not None and typed.kind == "discount":
            self._push(grant_id, actor, ip_hash)  # revoked here first; then archived at the provider
        with self._system.system_session(ADMIN) as conn:
            row = pg_codes.get_grant_code(conn, grant_id)
        if row is None:
            raise AdminRefusal(404, NO_GRANT)
        return grant_out(row, now)

    # ---- the beta ----------------------------------------------------------------------------------------------------

    def list_beta(self, *, state: str | None, q: str | None, limit: int, offset: int) -> BetaListOut:
        now = self._clock()
        with self._system.system_session(ADMIN) as conn:
            rows, total = pg_beta.list_people(
                conn,
                now=now,
                state=state if state in pg_beta.STATES else None,
                q=q,
                limit=limit,
                offset=offset,  # type: ignore[arg-type]
            )
            counted = pg_beta.counts(conn, now=now)
        return BetaListOut(
            people=[beta_person_out(r) for r in rows],
            total=total,
            limit=max(1, min(limit, pg_beta.PAGE_MAX)),
            offset=max(0, offset),
            counts=counted,
        )

    def revoke_beta(self, actor: UUID, signup_id: int, reason: str, *, ip_hash: str | None = None) -> BetaRevoked:
        """End a person's beta access (or withdraw a code nobody used). The email that tells them is the caller's to send,
        so it is returned here and recorded as sent by `beta_notice_sent` once it has left; it never appears in an answer."""
        now = self._clock()
        with self._system.system_session(ADMIN) as conn:
            person = pg_beta.get_person(conn, signup_id, now=now)
            if person is None:
                raise AdminRefusal(404, NO_PERSON)
            done = pg_beta.revoke(conn, person, now=now)
            if done is None:
                raise AdminRefusal(409, NOTHING_TO_REVOKE)
            self._record(
                conn, "admin_beta_revoked", actor, None, ip_hash, signup=signup_id, outcome=done.outcome, reason=reason
            )
            after = pg_beta.get_person(conn, signup_id, now=now)
        if after is None:
            raise AdminRefusal(404, NO_PERSON)
        out = beta_person_out(after)
        if done.outcome == "access_ended":
            return BetaRevoked(
                out, notice_to=person.email, notice_name=person.first_name, redemption_id=done.redemption_id
            )
        return BetaRevoked(out)

    def remove_beta(self, actor: UUID, signup_id: int, reason: str, *, ip_hash: str | None = None) -> BetaRemovedOut:
        """Remove a sign-up that has no account, and every code tied to its address (a removal request by email)."""
        with self._system.system_session(ADMIN) as conn:
            outcome = pg_beta.remove(conn, signup_id)
            if outcome == "not_found":
                raise AdminRefusal(404, NO_PERSON)
            if outcome == "has_account":
                raise AdminRefusal(409, HAS_ACCOUNT)
            self._record(conn, "admin_beta_removed", actor, None, ip_hash, signup=signup_id, reason=reason)
        return BetaRemovedOut(removed=signup_id)

    def beta_notice_sent(self, redemption_id: int) -> None:
        with self._system.system_session(ADMIN) as conn:
            pg_beta.mark_notice_sent(conn, redemption_id, now=self._clock())

    # ---- the log and the overview ------------------------------------------------------------------------------------

    def audit(
        self,
        *,
        action: str | None,
        q: str | None,
        actor: str | None,
        days: int | None,
        limit: int,
        before: int | None,
    ) -> AuditPageOut:
        limit = max(1, min(limit, pg_log.PAGE_MAX))
        since = self._clock() - timedelta(days=days) if days else None
        with self._system.system_session(ADMIN) as conn:
            rows = pg_log.audit_events(
                conn, action=action, q=q, actor=actor, since=since, limit=limit + 1, before=before
            )
        page = rows[:limit]
        return AuditPageOut(
            events=[
                AuditEventOut(
                    id=r.id,
                    at=r.at,
                    actor=r.actor,
                    action=r.action,
                    actor_email=r.actor_email,
                    user_id=r.user_id,
                    user_email=r.user_email,
                    meta=r.meta,
                )
                for r in page
            ],
            next_before=page[-1].id if len(rows) > limit and page else None,
        )

    def overview(self) -> OverviewOut:
        with self._system.system_session(ADMIN) as conn:
            o = pg_log.overview(conn, now=self._clock())
        return OverviewOut(**vars(o))

    # ---- the firms' rule templates --------------------------------------------------------------------------------------
    # Everyone reads them to pick an account's rules; they hold no one's journal. An edit never reaches a person's verified
    # rule set: that is a copy (`pg/rules.py`).

    def list_rule_templates(self, *, q: str | None, limit: int, offset: int) -> AdminRuleTemplateListOut:
        with self._system.system_session(ADMIN) as conn:
            rows, total = pg_templates.search(conn, q, limit=limit, offset=offset)
        return AdminRuleTemplateListOut(
            templates=[template_key(t) for t in rows], total=total, limit=limit, offset=offset
        )

    def rule_template(self, template_id: int) -> AdminRuleTemplateOut:
        with self._system.system_session(ADMIN) as conn:
            template = pg_templates.get(conn, template_id)
        if template is None:
            raise AdminRefusal(404, NO_TEMPLATE)
        return admin_template_out(template)

    def edit_rule_template(
        self, actor: UUID, template_id: int, rules: RuleValues, *, ip_hash: str | None = None
    ) -> AdminRuleTemplateOut:
        try:
            with self._system.system_session(ADMIN) as conn:
                template = pg_templates.update(conn, template_id, rules, by=_by(actor))
                if template is None:
                    raise AdminRefusal(404, NO_TEMPLATE)
                self._record(conn, "admin_rule_template_edited", actor, None, ip_hash, template=template_id)
        except psycopg.errors.UniqueViolation:
            raise AdminRefusal(409, TEMPLATE_CLASH) from None
        return admin_template_out(template)

    def import_rule_templates(
        self, actor: UUID, text: str, *, dry_run: bool = False, ip_hash: str | None = None
    ) -> RuleTemplateImportOut:
        """Upsert every row the CSV holds that can be read; the rows that cannot are listed and nothing else is lost. A
        dry run does the same work and rolls it back, so its counts are exactly what the import would do."""
        with self._system.system_session(ADMIN) as conn:
            counts, problems = pg_templates.import_csv(conn, text, by=_by(actor))
            if dry_run:
                conn.rollback()
            elif counts.inserted or counts.updated:
                self._record(
                    conn,
                    "admin_rule_templates_imported",
                    actor,
                    None,
                    ip_hash,
                    inserted=counts.inserted,
                    updated=counts.updated,
                    unchanged=counts.unchanged,
                    refused=len(problems),
                )
        return RuleTemplateImportOut(
            applied=not dry_run,
            inserted=counts.inserted,
            updated=counts.updated,
            unchanged=counts.unchanged,
            changed=list(counts.changed),
            problems=problems,
        )

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
        self, conn: psycopg.Connection, action: str, actor: UUID, target: UUID | None, ip_hash: str | None, **meta
    ) -> None:
        pg_audit.record(
            conn, action, user_id=target, actor=pg_audit.ACTOR_ADMIN, by=actor, ip_hash=ip_hash, meta=meta or None
        )
