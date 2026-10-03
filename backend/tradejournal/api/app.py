"""The application factory.

    uvicorn tradejournal.api.app:create_app --factory --host 0.0.0.0 --port 8000

Configuration comes from the environment (see `ApiConfig`). Routers join here as milestones land: sessions and
settings first, then the tenant API (doc 07 section 5).
"""

from __future__ import annotations

from fastapi import FastAPI

from .config import ApiConfig
from .errors import install_error_handlers
from .security import SecurityHeadersMiddleware
from .spa import mount_spa

API_VERSION = "1"  # the contract's version (`/api/v1`), not the build: that is `ApiConfig.app_version`


def create_app(config: ApiConfig | None = None) -> FastAPI:
    config = config or ApiConfig.from_env()
    # No interactive docs and no served schema: the SPA does not need them, and `python -m tradejournal.api.openapi`
    # writes the schema to a file for code generation.
    app = FastAPI(title="Trading Journal API", version=API_VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.add_middleware(SecurityHeadersMiddleware, config=config)
    install_error_handlers(app, config)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}  # liveness only; `/readyz` (database reachable) arrives with the database in M2

    mount_spa(app, config.web_dist)  # last, so it never shadows a route above
    return app
