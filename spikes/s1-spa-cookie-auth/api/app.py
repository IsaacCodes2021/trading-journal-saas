"""S1 spike: a FastAPI app that serves a built SPA and a cookie-authenticated JSON API on one origin (ADR-010, 011).

Throwaway code. It exists to answer the S1 questions in docs/design/05-features-and-roadmap.md, with in-memory
data standing in for Postgres.
"""

from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fake_idp import FakeIdP, pkce_challenge
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from money import Money
from pydantic import BaseModel, ConfigDict
from security import (
    OIDC_COOKIE,
    SECURITY_HEADERS,
    SESSION_COOKIE,
    Session,
    SessionStore,
    check_csrf,
    next_path,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

APP_VERSION = "s1-spike-1"
MAX_UPLOAD = 2 * 1024 * 1024
RESERVED_PREFIXES = {"api", "auth", "files", "webhooks", "fake-idp", "healthz"}


@dataclass
class Settings:
    web_dist: Path
    app_origin: str = "http://localhost:8000"
    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))


@dataclass
class User:
    id: int
    sub: str
    email: str
    name: str
    timezone: str


USERS = [
    User(1, "idp|alice", "alice@example.test", "Alice", "America/Los_Angeles"),
    User(2, "idp|bob", "bob@example.test", "Bob", "Australia/Sydney"),
    User(3, "idp|carol", "carol@example.test", "Carol", "Europe/London"),
]


# ---- schemas -----------------------------------------------------------------------------------------------------


class ApiModel(BaseModel):
    # Responses always contain defaulted fields, so the *response* schema marks them required while the request
    # schema keeps them optional. Pairs with openapi-typescript's --default-non-nullable false.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class UserOut(ApiModel):
    id: int
    email: str
    display_name: str


class SettingsOut(ApiModel):
    timezone: str
    theme: Literal["dark", "light"]


class FlagsOut(ApiModel):
    is_admin: bool
    mfa_fresh: bool


class SessionOut(ApiModel):
    user: UserOut
    settings: SettingsOut
    flags: FlagsOut
    csrf_token: str
    app_version: str


class TradeOut(ApiModel):
    id: int
    symbol: str
    direction: Literal["long", "short"]
    trade_date: date
    net_pnl: Money
    fees: Money
    notes: str


class TradesOut(ApiModel):
    items: list[TradeOut]
    total: int


class StatsSummary(ApiModel):
    preset: str
    trades: int
    net_pnl: Money
    fees: Money
    win_rate: float


class DailyPoint(ApiModel):
    trade_date: date
    net_pnl: Money


class NotesIn(ApiModel):
    notes: str


class LedgerIn(ApiModel):
    amount: Money
    kind: Literal["cost", "payout"]
    note: str = ""


class LedgerOut(ApiModel):
    id: int
    amount: Money
    kind: Literal["cost", "payout"]
    note: str


class UploadOut(ApiModel):
    filename: str
    size: int
    content_type: str | None


# ---- helpers -----------------------------------------------------------------------------------------------------


def resolve_preset(preset: str, today: date) -> tuple[date | None, date | None]:
    """Date presets are resolved on the server, in the user's timezone (the client never does date math)."""
    if preset in ("7d", "30d"):
        return today - timedelta(days=int(preset[:-1]) - 1), today
    if preset == "mtd":
        return today.replace(day=1), today
    return None, None


def user_today(user: User, now: datetime) -> date:
    return now.astimezone(ZoneInfo(user.timezone)).date()


def seed_trades(now: datetime) -> list[dict]:
    """A few trades per user, dated relative to *that user's* today."""
    out: list[dict] = []
    offsets = [0, 1, 2, 5, 8, 20, 40]
    pnl = ["125.50", "-80.00", "310.25", "-45.75", "200.00", "-120.00", "95.00"]
    for user in USERS:
        today = user_today(user, now)
        for i, (off, amount) in enumerate(zip(offsets, pnl, strict=True)):
            out.append(
                {
                    "id": len(out) + 1,
                    "user_id": user.id,
                    "symbol": ["NQ", "MNQ", "ES"][i % 3],
                    "direction": "long" if i % 2 == 0 else "short",
                    "trade_date": today - timedelta(days=off),
                    "net_pnl": Decimal(amount),
                    "fees": Decimal("4.12"),
                    "notes": "",
                }
            )
    return out


def problem(status: int, detail: str | None = None) -> JSONResponse:
    return JSONResponse(
        {"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status, "detail": detail},
        status_code=status,
        media_type="application/problem+json",
    )


class ImmutableStatic(StaticFiles):
    """Hashed build assets never change, so they are cached for a year."""

    def file_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# ---- application ------------------------------------------------------------------------------------------------


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="S1 spike API", version=APP_VERSION, docs_url=None, redoc_url=None)
    store = SessionStore(settings.secret)
    idp = FakeIdP(redirect_uri=f"{settings.app_origin}/auth/callback")
    oidc_signer = URLSafeTimedSerializer(settings.secret, salt="oidc-state")
    now = datetime.now(UTC)
    trades = seed_trades(now)
    ledger: list[dict] = []
    csp_reports: list[dict] = []
    app.state.settings, app.state.store, app.state.csp_reports = settings, store, csp_reports
    app.include_router(idp.router())

    # -- cross-cutting -------------------------------------------------------------------------------------------

    @app.middleware("http")
    async def headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        response.headers["X-App-Version"] = APP_VERSION
        if request.url.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(exc.status_code, str(exc.detail) if exc.detail else None)

    # -- dependencies --------------------------------------------------------------------------------------------

    def current_session(request: Request) -> tuple[str, Session, User]:
        found = store.lookup(request.cookies.get(SESSION_COOKIE))
        if found is None:
            raise HTTPException(401, "not signed in")
        key, session = found
        return key, session, next(u for u in USERS if u.id == session.user_id)

    def require_csrf(request: Request, auth: tuple[str, Session, User] = Depends(current_session)):
        check_csrf(request, store.csrf_token(auth[0]), settings.app_origin)
        return auth

    # -- auth ----------------------------------------------------------------------------------------------------

    @app.get("/auth/login", include_in_schema=False)
    def login(next: str | None = None) -> RedirectResponse:
        state, nonce, verifier = secrets.token_urlsafe(16), secrets.token_urlsafe(16), secrets.token_urlsafe(32)
        params = {
            "redirect_uri": idp.redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        response = RedirectResponse(f"/fake-idp/authorize?{urlencode(params)}", 302)
        response.set_cookie(
            OIDC_COOKIE,
            oidc_signer.dumps({"state": state, "nonce": nonce, "verifier": verifier, "next": next_path(next)}),
            max_age=600, secure=True, httponly=True, samesite="lax", path="/",
        )  # fmt: skip
        return response

    @app.get("/auth/callback", include_in_schema=False)
    def callback(request: Request, code: str, state: str) -> RedirectResponse:
        try:
            saved = oidc_signer.loads(request.cookies.get(OIDC_COOKIE, ""), max_age=600)
        except BadSignature:
            raise HTTPException(400, "login session expired") from None
        if not hmac.compare_digest(saved["state"], state):
            raise HTTPException(400, "state mismatch")
        claims = idp.exchange(code, saved["verifier"])
        if claims is None or claims["nonce"] != saved["nonce"]:
            raise HTTPException(400, "token exchange failed")
        if not claims["email_verified"]:
            raise HTTPException(403, "email not verified")
        user = next(u for u in USERS if u.sub == claims["sub"])
        token = store.create(user.id, mfa="mfa" in claims["amr"])  # a fresh token on every login (rotation)
        response = RedirectResponse(saved["next"], 302)
        response.set_cookie(
            SESSION_COOKIE, token, max_age=90 * 24 * 3600, secure=True, httponly=True, samesite="lax", path="/"
        )
        response.delete_cookie(OIDC_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @app.post("/auth/logout", status_code=204, include_in_schema=False)
    def logout(request: Request, _: tuple = Depends(require_csrf)) -> Response:
        store.revoke(request.cookies.get(SESSION_COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    # -- API -----------------------------------------------------------------------------------------------------

    @app.get("/api/v1/session", response_model=SessionOut)
    def session(auth: tuple[str, Session, User] = Depends(current_session)) -> SessionOut:
        key, sess, user = auth
        return SessionOut(
            user=UserOut(id=user.id, email=user.email, display_name=user.name),
            settings=SettingsOut(timezone=user.timezone, theme="dark"),
            flags=FlagsOut(is_admin=False, mfa_fresh=sess.mfa_at is not None),
            csrf_token=store.csrf_token(key),
            app_version=APP_VERSION,
        )

    def visible(user: User, preset: str) -> list[dict]:
        start, end = resolve_preset(preset, user_today(user, now))
        rows = [t for t in trades if t["user_id"] == user.id]
        return [t for t in rows if (start is None or t["trade_date"] >= start) and (end is None or t["trade_date"] <= end)]

    @app.get("/api/v1/trades", response_model=TradesOut)
    def list_trades(
        preset: str = "all",
        direction: Literal["all", "long", "short"] = "all",
        auth: tuple[str, Session, User] = Depends(current_session),
    ) -> TradesOut:
        rows = [t for t in visible(auth[2], preset) if direction in ("all", t["direction"])]
        rows.sort(key=lambda t: t["trade_date"], reverse=True)
        return TradesOut(items=[TradeOut(**t) for t in rows], total=len(rows))

    def own_trade(trade_id: int, user: User) -> dict:
        trade = next((t for t in trades if t["id"] == trade_id and t["user_id"] == user.id), None)
        if trade is None:
            raise HTTPException(404, "not found")  # someone else's id looks exactly like a missing one
        return trade

    @app.get("/api/v1/trades/{trade_id}", response_model=TradeOut)
    def get_trade(trade_id: int, auth: tuple[str, Session, User] = Depends(current_session)) -> TradeOut:
        return TradeOut(**own_trade(trade_id, auth[2]))

    @app.put("/api/v1/trades/{trade_id}/notes", response_model=TradeOut)
    def put_notes(trade_id: int, body: NotesIn, auth: tuple[str, Session, User] = Depends(require_csrf)) -> TradeOut:
        trade = own_trade(trade_id, auth[2])
        trade["notes"] = body.notes
        return TradeOut(**trade)

    @app.post("/api/v1/trades/{trade_id}/screenshots", response_model=UploadOut)
    async def upload(
        trade_id: int, file: UploadFile = File(...), auth: tuple[str, Session, User] = Depends(require_csrf)
    ) -> UploadOut:
        own_trade(trade_id, auth[2])
        size = 0
        while chunk := await file.read(64 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise HTTPException(413, "file too large")  # aborts the stream instead of buffering it all
        return UploadOut(filename=file.filename or "", size=size, content_type=file.content_type)

    @app.get("/api/v1/stats/summary", response_model=StatsSummary)
    def summary(preset: str = "all", auth: tuple[str, Session, User] = Depends(current_session)) -> StatsSummary:
        rows = visible(auth[2], preset)
        wins = sum(1 for t in rows if t["net_pnl"] > 0)
        return StatsSummary(
            preset=preset,
            trades=len(rows),
            net_pnl=sum((t["net_pnl"] for t in rows), Decimal(0)),
            fees=sum((t["fees"] for t in rows), Decimal(0)),
            win_rate=(wins / len(rows)) if rows else 0.0,
        )

    @app.get("/api/v1/stats/daily", response_model=list[DailyPoint])
    def daily(preset: str = "all", auth: tuple[str, Session, User] = Depends(current_session)) -> list[DailyPoint]:
        by_day: dict[date, Decimal] = {}
        for t in visible(auth[2], preset):
            by_day[t["trade_date"]] = by_day.get(t["trade_date"], Decimal(0)) + t["net_pnl"]
        return [DailyPoint(trade_date=d, net_pnl=v) for d, v in sorted(by_day.items())]

    @app.post("/api/v1/ledger", response_model=LedgerOut, status_code=201)
    def add_ledger(body: LedgerIn, auth: tuple[str, Session, User] = Depends(require_csrf)) -> LedgerOut:
        entry = {"id": len(ledger) + 1, "user_id": auth[2].id, **body.model_dump()}
        ledger.append(entry)
        return LedgerOut(**entry)

    @app.post("/api/v1/csp-report", status_code=204, include_in_schema=False)
    async def csp_report(request: Request) -> Response:
        try:
            csp_reports.append(json.loads(await request.body()))
        except ValueError:
            csp_reports.append({"unparseable": True})
        return Response(status_code=204)

    @app.get("/api/v1/_debug/csp-reports", include_in_schema=False)
    def debug_reports() -> list[dict]:  # spike only: lets the test harness read what a real browser reported
        return csp_reports

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # -- the SPA, last, so it never shadows an API route ----------------------------------------------------------

    dist = settings.web_dist
    if (dist / "assets").is_dir():
        app.mount("/assets", ImmutableStatic(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request) -> Response:
        if full_path.split("/")[0] in RESERVED_PREFIXES:
            raise HTTPException(404, "not found")
        index = dist / "index.html"
        if "text/html" in request.headers.get("accept", "") and index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})  # history fallback
        raise HTTPException(404, "not found")

    return app
