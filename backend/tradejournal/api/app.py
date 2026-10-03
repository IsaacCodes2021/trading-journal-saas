"""The application factory.

    uvicorn tradejournal.api.app:create_app --factory --host 0.0.0.0 --port 8000

Configuration comes from the environment (see `ApiConfig`). Routers join here as milestones land: sessions and
settings first, then the tenant API (doc 07 section 5).
"""

from __future__ import annotations

from fastapi import FastAPI

from .config import ApiConfig
from .errors import install_error_handlers
from .limits import BodyLimitMiddleware
from .ports import Services
from .routers import session
from .security import SecurityHeadersMiddleware
from .spa import mount_spa

API_VERSION = "1"  # the contract's version (`/api/v1`), not the build: that is `ApiConfig.app_version`


def create_app(config: ApiConfig | None = None, services: Services | None = None) -> FastAPI:
    """`services` is where sessions and accounts come from. Without it the routes that need them answer 503, which is
    what a deployment looks like until sign-in is built (M5)."""
    config = config or ApiConfig.from_env()
    # No interactive docs and no served schema: the SPA does not need them, and `python -m tradejournal.api.openapi`
    # writes the schema to a file for code generation.
    app = FastAPI(
        title="Trading Journal API",
        version=API_VERSION,
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
        return {"status": "ok"}  # liveness only; `/readyz` (database reachable) arrives with the database in M2

    app.include_router(session.router)
    mount_spa(app, config.web_dist)  # last, so it never shadows a route above
    return app
