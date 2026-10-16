"""The application factory.

    uvicorn tradejournal.api.app:create_app --factory --host 0.0.0.0 --port 8000

Configuration comes from the environment (see `ApiConfig`). Routers join here as milestones land: sessions and
settings first, then the tenant API (doc 07 section 5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..auth.flow import AuthFlow
from ..auth.idp import IdentityProvider
from ..auth.kinde import KindeProvider
from ..billing.factory import provider_for
from ..billing.provider import BillingProvider
from ..bucketstore import BucketFileStore
from ..filestore import FileStore, LocalFileStore
from ..pg.readiness import DatabaseReadiness
from ..pg.session import Database
from ..pg.system import SystemDatabase
from .admin_service import PgAdminService
from .audit import PgAuditLog
from .billing_service import PgBillingService
from .config import ApiConfig
from .deps import utc_now
from .dev import dev_services
from .errors import install_error_handlers
from .limits import BodyLimitMiddleware
from .logs import configure_logging
from .mailer import LogMailer, Mailer, ResendMailer
from .pg_beta import PgBetaService
from .pg_services import PgAccountService
from .ports import ReadinessCheck, Services
from .ratelimit import RateLimiter
from .requestlog import RequestLogMiddleware
from .routers import (
    account,
    accounts,
    activity,
    admin_codes,
    admin_rules,
    admin_users,
    auth,
    beta,
    billing,
    day_notes,
    export,
    grants,
    imports,
    journal,
    pay,
    rules,
    screenshots,
    session,
    sessions,
    stats,
    tags,
    trades,
    webhooks,
)
from .security import SecurityHeadersMiddleware
from .sessions import PgSessionStore
from .slots import ImportSlots
from .spa import mount_spa

API_VERSION = "1"  # the contract's version (`/api/v1`), not the build: that is `ApiConfig.app_version`


def _mailer_for(config: ApiConfig) -> Mailer | None:
    """Email goes through the provider when it is configured. In development without one the message is logged (so the code
    can be read from the console); in production without one there is no mailer and the form answers 503."""
    if config.email_api_key and config.email_from:
        return ResendMailer(api_key=config.email_api_key, sender=config.email_from, base_url=config.email_api_url)
    return None if config.production else LogMailer()


def _store_for(config: ApiConfig) -> FileStore | None:
    """Where the files are kept: the bucket if one is configured, else the folder, else nowhere (the routes then answer 503)."""
    if config.bucket is not None:
        return BucketFileStore(config.bucket)
    return LocalFileStore(config.files_dir) if config.files_dir else None


def create_app(
    config: ApiConfig | None = None,
    services: Services | None = None,
    readiness: ReadinessCheck | None = None,
    *,
    database: Database | None = None,
    clock: Callable[[], datetime] = utc_now,
    idp: IdentityProvider | None = None,
    limiter: RateLimiter | None = None,
    files: FileStore | None = None,
    mailer: Mailer | None = None,
    billing_provider: BillingProvider | None = None,
) -> FastAPI:
    """`services` is where sessions and accounts come from. Without it the routes that need them answer 503, which is
    what a deployment looks like until sign-in is built (M5), except in development with `TJ_DEV_USER_ID` set, where
    every request is that user (`api/dev.py`).

    `database` is the request role's pool, used by the tenant routes. Left out, it is built from
    `config.database_url` (opened at startup, closed at shutdown); with no URL either, those routes answer 503. A
    `database` passed in is the caller's to open and close (tests do).

    `readiness` answers `/readyz`. Left out, it is built from the database when there is one; with neither, a
    development process is ready and a production one is not.

    `clock` says what instant it is now. It exists so tests can prove what "today" means for a date preset.

    `idp` is the identity provider for sign-in (Kinde, built from the configuration when left out; tests pass a fake). `limiter`
    keeps the rate limits' counts (one per process; tests pass one with their own clock). `files` is where screenshot
    files are kept; left out, it is the bucket in `config.bucket`, or a folder at `config.files_dir`, or nothing (the screenshot
    routes then answer 503).

    `billing_provider` is the payment provider (tests pass a `FakeBilling` whose clock they move). Left out, it is the one
    `TJ_BILLING_PROVIDER` names, if any; with none, the billing routes answer 404 whatever the switch says."""
    config = config or ApiConfig.from_env()
    owned = database is None and config.database_url is not None
    if owned:
        database = Database(config.database_url)  # type: ignore[arg-type]  # not None: `owned` checked it
    if readiness is None and database is not None:
        readiness = DatabaseReadiness(database)
    system: SystemDatabase | None = None
    auth_flow: AuthFlow | None = None
    if services is None and config.dev_user_id is not None and database is not None and config.login_database_url:
        system = SystemDatabase({"login": config.login_database_url})
        services = dev_services(config.dev_user_id, database, system)
    elif services is None and database is not None and config.login_database_url:
        # Real sessions, and, when Kinde is configured, real sign-in. Without Kinde nobody can sign in yet.
        urls = {"login": config.login_database_url}
        if config.admin_database_url:  # the operator's tools exist only where their database role does
            urls["admin"] = config.admin_database_url
        if config.webhook_database_url:  # the billing webhook writes as its own role
            urls["webhook"] = config.webhook_database_url
        system = SystemDatabase(urls)
        store = PgSessionStore(system)
        provider = billing_provider if billing_provider is not None else provider_for(config, clock=clock)
        billing_service = PgBillingService(system, provider, clock=clock) if provider is not None else None
        services = Services(
            sessions=store,
            accounts=PgAccountService(database, system, clock=clock, billing_enabled=config.billing_enabled),
            audit=PgAuditLog(system),
            admin=PgAdminService(system, store, clock=clock) if config.admin_database_url else None,
            beta=PgBetaService(system),
            billing=billing_service,
        )
        if idp is None and config.kinde_domain and config.kinde_client_id and config.kinde_client_secret:
            idp = KindeProvider(
                domain=config.kinde_domain, client_id=config.kinde_client_id, client_secret=config.kinde_client_secret
            )
        if idp is not None:
            auth_flow = AuthFlow(
                idp=idp,
                system=system,
                database=database,
                sessions=store,
                secret=config.session_secret,
                app_origin=config.app_origin,
                invite_only=config.signup_mode == "invite",
                trial=config.billing_enabled,
                billing=billing_service,
            )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if config.production:
            configure_logging()  # JSON lines on standard output, scrubbed (api/logs.py); development keeps its own output
        if owned and database is not None:
            database.open(wait=False)  # a database that is briefly away is reported by /readyz, not a failure to boot
        if system is not None:
            system.open()  # development only: it must be reachable, there is no sign-in without it
        try:
            yield
        finally:
            if system is not None:
                system.close()
            if owned and database is not None:
                database.close()

    # No interactive docs and no served schema: the SPA does not need them, and `python -m tradejournal.api.openapi`
    # writes the schema to a file for code generation.
    app = FastAPI(
        title="Trading Journal API",
        version=API_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        # Operation ids are the handler names (`get_session`), not FastAPI's `get_session_api_v1_session_get`, so a
        # path change does not rename every generated TypeScript operation.
        generate_unique_id_function=lambda route: route.name,
    )
    app.state.config = config
    app.state.services = services
    app.state.database = database
    app.state.auth = auth_flow
    app.state.clock = clock
    app.state.limiter = limiter or RateLimiter()
    app.state.import_slots = ImportSlots(config.max_concurrent_imports)
    app.state.files = files if files is not None else _store_for(config)
    app.state.mailer = mailer if mailer is not None else _mailer_for(config)
    # The last one added is the outermost: the security headers must wrap everything, including a 413 from the limit.
    app.add_middleware(BodyLimitMiddleware, json_limit=config.max_json_bytes, upload_limit=config.max_upload_bytes)
    app.add_middleware(SecurityHeadersMiddleware, config=config)
    app.add_middleware(RequestLogMiddleware)  # outermost: one line for every request, whoever refused it
    install_error_handlers(app, config)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}  # liveness only: the process is up. Whether it can serve is `/readyz`

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        """Can this process serve requests? 200 when every check passes, 503 otherwise, so the platform holds traffic
        back during a deploy and a pinger can alert. Public, so it answers with states, never with error text."""
        if readiness is None:
            ready, checks = not config.production, {"database": "not configured"}
        else:
            try:
                ready, checks = readiness()
            except Exception:  # noqa: BLE001  (a failing check is "not ready"; its message stays out of a public answer)
                ready, checks = False, {"readiness": "error"}
        body = {"status": "ready" if ready else "not_ready", "checks": checks}
        return JSONResponse(body, status_code=200 if ready else 503, headers={"Cache-Control": "no-store"})

    app.include_router(session.router)
    app.include_router(sessions.router)
    app.include_router(account.router)
    app.include_router(admin_users.router)
    app.include_router(admin_codes.router)
    app.include_router(admin_rules.router)
    app.include_router(activity.router)
    app.include_router(grants.router)
    app.include_router(billing.router)
    app.include_router(webhooks.router)
    app.include_router(pay.router)
    app.include_router(export.router)
    app.include_router(rules.router)  # before accounts: /accounts/overview is not an account id
    app.include_router(accounts.router)
    app.include_router(stats.router)
    app.include_router(trades.router)
    app.include_router(tags.router)
    app.include_router(journal.router)
    app.include_router(day_notes.router)
    app.include_router(screenshots.router)
    app.include_router(imports.router)
    app.include_router(auth.public)
    app.include_router(auth.router)
    app.include_router(beta.public)
    mount_spa(app, config.web_dist)  # last, so it never shadows a route above
    return app
