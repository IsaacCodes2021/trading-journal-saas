"""The application factory.

    uvicorn tradejournal.api.app:create_app --factory --host 0.0.0.0 --port 8000

Configuration comes from the environment (see `ApiConfig`). Routers join here as milestones land: sessions and
settings first, then the tenant API (doc 07 section 5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..pg.readiness import DatabaseReadiness
from ..pg.session import Database
from .config import ApiConfig
from .errors import install_error_handlers
from .limits import BodyLimitMiddleware
from .ports import ReadinessCheck, Services
from .routers import session
from .security import SecurityHeadersMiddleware
from .spa import mount_spa

API_VERSION = "1"  # the contract's version (`/api/v1`), not the build: that is `ApiConfig.app_version`


def create_app(
    config: ApiConfig | None = None, services: Services | None = None, readiness: ReadinessCheck | None = None
) -> FastAPI:
    """`services` is where sessions and accounts come from. Without it the routes that need them answer 503, which is
    what a deployment looks like until sign-in is built (M5).

    `readiness` answers `/readyz`. Left out, it is built from `config.database_url` (the pool is opened at startup and
    closed at shutdown); with no URL either, a development process is ready and a production one is not."""
    config = config or ApiConfig.from_env()
    database = Database(config.database_url) if readiness is None and config.database_url else None
    if database is not None:
        readiness = DatabaseReadiness(database)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if database is not None:
            database.open(wait=False)  # a database that is briefly away is reported by /readyz, not a failure to boot
        try:
            yield
        finally:
            if database is not None:
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
    # The last one added is the outermost: the security headers must wrap everything, including a 413 from the limit.
    app.add_middleware(BodyLimitMiddleware, json_limit=config.max_json_bytes, upload_limit=config.max_upload_bytes)
    app.add_middleware(SecurityHeadersMiddleware, config=config)
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
    mount_spa(app, config.web_dist)  # last, so it never shadows a route above
    return app
