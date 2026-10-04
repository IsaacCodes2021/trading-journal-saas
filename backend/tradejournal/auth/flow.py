"""The sign-in flow: OIDC authorization code with PKCE, then our own session (doc 03 section 4, doc 04 gates).

    begin()     the browser asked to sign in: make `state`, `nonce` and a PKCE verifier, keep them in a short-lived signed
                cookie, and say where to send the browser (the identity provider).
    complete()  the browser came back with a code: check the cookie and `state`, have the provider validate the code and
                the ID token, require a verified email, find or create the person (a new person needs an invite while the
                beta is gated), start a session, and say where to send the browser next.
    logout()    end our session (the provider's own session is ended by sending the browser to its logout page).

Nothing here touches HTTP: the routes pass values in and set cookies from what comes back. A failure never says why to
the browser (it gets a short code on the way to the sign-in page); the reason goes to the audit log, which never holds
a token, a code, a secret or anything a person wrote.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import psycopg

from ..api.sessions import PgSessionStore
from ..pg import audit, deletion, invites, users
from ..pg import sessions as pg_sessions
from ..pg.defaults import provision_defaults
from ..pg.session import Database
from ..pg.system import SystemDatabase
from .idp import IdentityError, IdentityProvider
from .iphash import hash_ip
from .signed import sign, unsign

LOGIN_PURPOSE = "login-state"
CALLBACK_PATH = "/auth/callback"
SIGNED_OUT_PATH = "/invite"  # where a person lands after signing out: the page that needs no session

# The short codes a failed sign-in carries to the page (`/invite?error=...`). None of them says more than it must.
ERR_EXPIRED = "expired"  # the attempt timed out or its cookie was missing: just try again
ERR_FAILED = "signin_failed"
ERR_UNVERIFIED = "email_unverified"
ERR_INVITE = "invite_invalid"  # one code for "no invite", "wrong invite", "used up", "meant for someone else"
ERR_UNAVAILABLE = "account_unavailable"  # suspended or being deleted
ERR_EMAIL_TAKEN = "email_taken"
ERR_REAUTH = "reauth_failed"  # asked to prove who they are again, and did not (or someone else did)

REAUTH_WINDOW = timedelta(minutes=2)  # how fresh the provider's `auth_time` must be for a re-authentication to count
RESTORED_PATH = "/settings/privacy?restored=1"  # where signing in lands when it cancelled a scheduled deletion


def safe_next(value: str | None) -> str:
    """Where to go after signing in: a path on this site, or "/". Never another site, never the sign-in routes."""
    if not value or len(value) > 500 or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.path.startswith("/auth/"):
        return "/"
    return value


@dataclass(frozen=True)
class LoginStart:
    redirect_url: str  # the identity provider's page
    state_cookie: str  # the signed value to set as the login-state cookie


@dataclass(frozen=True)
class LoginResult:
    redirect_to: str  # a path on this site
    session_token: str | None  # set as the session cookie when signed in
    error: str | None  # a short code when not
    restored: bool = False  # signing in cancelled a scheduled account deletion


class AuthFlow:
    def __init__(
        self,
        *,
        idp: IdentityProvider,
        system: SystemDatabase,
        database: Database,
        sessions: PgSessionStore,
        secret: bytes,
        app_origin: str,
        invite_only: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._idp = idp
        self._system = system
        self._database = database
        self._sessions = sessions
        self._secret = secret
        self._origin = app_origin.rstrip("/")
        self._invite_only = invite_only
        self._clock = clock
        self._wall = wall

    @property
    def redirect_uri(self) -> str:
        return f"{self._origin}{CALLBACK_PATH}"

    def hash_ip(self, ip: str | None) -> str | None:
        return hash_ip(self._secret, ip)

    # ---- step 1 ---------------------------------------------------------------------------------------------------

    def begin(
        self, *, next_url: str | None, invite: str | None, step_up: bool = False, reauth: bool = False
    ) -> LoginStart:
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        code = invites.normalize(invite) if invite and len(invite) <= 60 else None
        cookie = sign(
            self._secret,
            LOGIN_PURPOSE,
            {
                "s": state,
                "n": nonce,
                "v": verifier,
                "next": safe_next(next_url),
                "inv": code,
                "up": step_up,
                "re": reauth,
            },
            now=self._wall,
        )
        url = self._idp.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=challenge,
            redirect_uri=self.redirect_uri,
            step_up=step_up,
            reauth=reauth,
        )
        return LoginStart(url, cookie)

    # ---- step 2 ---------------------------------------------------------------------------------------------------

    def complete(
        self,
        *,
        state_cookie: str | None,
        code: str | None,
        state: str | None,
        provider_error: str | None = None,
        previous_session: str | None = None,
        user_agent: str | None = None,
        client_ip: str | None = None,
    ) -> LoginResult:
        ip_hash = self.hash_ip(client_ip)
        saved = unsign(self._secret, LOGIN_PURPOSE, state_cookie, now=self._wall)
        if saved is None:
            return self._fail(ERR_EXPIRED, "no_state", ip_hash)
        next_url = safe_next(saved.get("next"))
        if (
            provider_error
            or not code
            or not state
            or not hmac.compare_digest(str(saved.get("s", "")).encode(), state.encode())
        ):
            reason = "provider_error" if provider_error else "state"
            return self._fail(ERR_FAILED, reason, ip_hash)
        try:
            identity = self._idp.complete(
                code=code, code_verifier=str(saved["v"]), redirect_uri=self.redirect_uri, nonce=str(saved["n"])
            )
        except IdentityError as problem:
            return self._fail(ERR_FAILED, problem.reason, ip_hash)
        if not identity.email_verified:
            return self._fail(ERR_UNVERIFIED, "email_unverified", ip_hash)
        mfa_at = identity.auth_time if identity.acr == "mfa" and identity.auth_time else None

        reauth_for: UUID | None = None
        if saved.get("re"):
            # The person was asked to prove who they are again (before deleting an account). Three things must hold, and the
            # provider's willingness to honour `max_age=0` is not one we take on trust: the answer must be about the person who
            # asked, and must say they authenticated just now.
            asking = self._sessions.lookup(previous_session)
            if asking is None:
                return self._fail(ERR_REAUTH, "reauth_no_session", ip_hash)
            problem = self._reauth_problem(identity)
            if problem:
                return self._fail(ERR_REAUTH, problem, ip_hash)
            reauth_for = asking.user_id

        outcome = self._record_sign_in(
            identity, saved.get("inv"), mfa_at, previous_session, user_agent, ip_hash, reauth_for=reauth_for
        )
        if isinstance(outcome, str):
            return LoginResult("/", None, outcome)
        user_id, token, restored = outcome
        # Their own tables start with the defaults (settings, usage row, the three default categories). Idempotent, so
        # every sign-in repairs a first sign-in that was cut short.
        with self._database.tenant_session(user_id) as conn:
            provision_defaults(conn)
        return LoginResult(RESTORED_PATH if restored else next_url, token, None, restored=restored)

    def _reauth_problem(self, identity) -> str | None:
        """Why this answer does not prove a fresh authentication, or None if it does."""
        if identity.auth_time is None:
            return "reauth_no_auth_time"
        age = self._clock() - identity.auth_time
        if age > REAUTH_WINDOW:
            return "reauth_stale"
        if age < -timedelta(minutes=1):  # a clock a minute ahead of ours is tolerated; more is not believed
            return "reauth_future"
        return None

    def _record_sign_in(
        self, identity, invite_code, mfa_at, previous_session, user_agent, ip_hash, *, reauth_for: UUID | None = None
    ) -> tuple[UUID, str, bool] | str:
        """Everything the sign-in changes, in one transaction. Returns (user id, session token, whether it cancelled a
        scheduled deletion), or an error code (the audit row for the failure is committed; nothing else is)."""
        now = self._clock()
        with self._system.system_session("login") as conn:
            user = users.find_by_subject(conn, identity.subject)
            restored = False
            if reauth_for is not None and (user is None or user.id != reauth_for):
                # Someone else completed the prompt, or the identity is not the one that asked: nothing changes.
                audit.record(conn, "login_failed", ip_hash=ip_hash, meta={"reason": "reauth_other_person"})
                return ERR_REAUTH
            if user is None:
                created = self._sign_up(conn, identity, invite_code, now, ip_hash)
                if isinstance(created, str):
                    return created
                user, signup = created, True
            else:
                signup = False
                if user.status == "pending_deletion":
                    # Coming back before the date is a way to change your mind. Only that status is restored; a
                    # suspended account is refused below.
                    restored = deletion.cancel(conn, user.id)
                    if restored:
                        audit.record(conn, "deletion_cancelled", user_id=user.id, ip_hash=ip_hash)
                if user.status not in ("active", "pending_deletion") or (
                    user.status == "pending_deletion" and not restored
                ):
                    audit.record(conn, "login_failed", user_id=user.id, ip_hash=ip_hash, meta={"reason": "status"})
                    return ERR_UNAVAILABLE
                try:
                    users.record_login(conn, user.id, identity.email, now=now)
                except users.EmailTaken:
                    audit.record(conn, "login_failed", user_id=user.id, ip_hash=ip_hash, meta={"reason": "email_taken"})
                    return ERR_EMAIL_TAKEN
            if previous_session:
                pg_sessions.revoke(conn, previous_session, now=now)  # a new sign-in always means a new token
            token = pg_sessions.create(
                conn,
                user.id,
                mfa_at=mfa_at,
                authenticated_at=identity.auth_time,  # the provider's own word for when; defaults to now
                user_agent=user_agent,
                ip_hash=ip_hash,
                now=now,
            )
            audit.record(
                conn,
                "signup" if signup else "login",
                user_id=user.id,
                ip_hash=ip_hash,
                meta={"mfa": mfa_at is not None, "reauth": reauth_for is not None},
            )
            return user.id, token, restored

    def _sign_up(self, conn: psycopg.Connection, identity, invite_code, now, ip_hash) -> users.User | str:
        invite = None
        if self._invite_only:
            invite = invites.check(conn, invite_code, identity.email, now=now)
            if invite is None:
                audit.record(conn, "login_failed", ip_hash=ip_hash, meta={"reason": "invite"})
                return ERR_INVITE
        try:
            with conn.transaction():  # undone together if the invite turns out to be used up in the meantime
                user = users.create(conn, identity.subject, identity.email, now=now)
                if invite is not None and not invites.redeem(conn, invite, user.id, now=now):
                    raise psycopg.Rollback
        except users.EmailTaken:
            audit.record(conn, "login_failed", ip_hash=ip_hash, meta={"reason": "email_taken"})
            return ERR_EMAIL_TAKEN
        except psycopg.errors.UniqueViolation:  # the same person signing up in two tabs at once
            audit.record(conn, "login_failed", ip_hash=ip_hash, meta={"reason": "race"})
            return ERR_FAILED
        if invite is not None and users.find_by_subject(conn, identity.subject) is None:
            audit.record(conn, "login_failed", ip_hash=ip_hash, meta={"reason": "invite"})
            return ERR_INVITE
        return user

    def _fail(self, error: str, reason: str, ip_hash: str | None) -> LoginResult:
        """A failure that happened before anyone was identified: the reason goes to the log, a short code to the page."""
        with self._system.system_session("login") as conn:
            audit.record(conn, "login_failed", actor=audit.ACTOR_SYSTEM, ip_hash=ip_hash, meta={"reason": reason})
        return LoginResult("/", None, error)

    # ---- sign out -------------------------------------------------------------------------------------------------

    def logout(self, *, token: str, user_id: UUID, ip_hash: str | None = None) -> str:
        """End the session; returns where to send the browser (the provider's logout page, else the home page)."""
        self._sessions.revoke(token)
        with self._system.system_session("login") as conn:
            audit.record(conn, "logout", user_id=user_id, ip_hash=ip_hash)
        # Back to the public page: "/" needs a session, so it would send the person straight to the provider's sign-in.
        return self._idp.logout_url(return_to=f"{self._origin}{SIGNED_OUT_PATH}") or SIGNED_OUT_PATH
