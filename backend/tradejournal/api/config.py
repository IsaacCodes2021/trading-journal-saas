"""Settings for the hosted API, read from environment variables (the container is configured, never edited)."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[3]
MIN_SECRET_BYTES = 32


class ConfigError(ValueError):
    """The environment is not a configuration the API can run with."""


@dataclass(frozen=True)
class ApiConfig:
    # Where the browser reaches the app, as scheme://host[:port] with no path. It is compared byte for byte with
    # the Origin header of unsafe requests (CSRF layer 2, ADR-011), so a trailing slash would break every write.
    app_origin: str = "http://localhost:8000"
    web_dist: Path = REPO_ROOT / "frontend" / "dist"
    # Signs per-session CSRF tokens. A fresh value per process is fine in development; in production it must be
    # fixed, or every deploy would invalidate every open tab's token.
    session_secret: bytes = field(default_factory=lambda: secrets.token_bytes(MIN_SECRET_BYTES), repr=False)
    app_version: str = "dev"  # the build id (git sha); sent as X-App-Version so a stale tab can tell it is stale
    production: bool = False
    # Ceilings on a request body (api/limits.py). Routes enforce their own exact limits underneath these.
    max_json_bytes: int = 1024 * 1024
    max_upload_bytes: int = 12 * 1024 * 1024  # one 10 MB screenshot plus multipart overhead
    # The request role's connection URL (app_rw). It holds a password, so it never appears in repr or logs. Not
    # required yet: until the routes use the database (M3), a process without it starts, and /readyz says so.
    database_url: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.max_json_bytes < 1 or self.max_upload_bytes < 1:
            raise ConfigError("body size limits must be positive")
        if self.database_url is not None and urlsplit(self.database_url).scheme not in ("postgresql", "postgres"):
            raise ConfigError("TJ_DATABASE_URL must be a postgresql:// URL")
        parts = urlsplit(self.app_origin)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query or parts.fragment:
            raise ConfigError(
                f"TJ_APP_ORIGIN must look like https://app.example.com (no path), got {self.app_origin!r}"
            )
        if self.production:
            if parts.scheme != "https":
                raise ConfigError("TJ_APP_ORIGIN must be https in production")
            if len(self.session_secret) < MIN_SECRET_BYTES:
                raise ConfigError(f"TJ_SESSION_SECRET must be at least {MIN_SECRET_BYTES} bytes in production")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ApiConfig:
        env = os.environ if env is None else env
        mode = env.get("TJ_ENV", "development")
        if mode not in ("development", "production"):
            raise ConfigError(f"TJ_ENV must be 'development' or 'production', got {mode!r}")
        values: dict[str, object] = {"production": mode == "production"}
        if "TJ_APP_ORIGIN" in env:
            values["app_origin"] = env["TJ_APP_ORIGIN"]
        if "TJ_WEB_DIST" in env:
            values["web_dist"] = Path(env["TJ_WEB_DIST"])
        if "TJ_APP_VERSION" in env:
            values["app_version"] = env["TJ_APP_VERSION"]
        if env.get("TJ_DATABASE_URL"):
            values["database_url"] = env["TJ_DATABASE_URL"]
        if "TJ_SESSION_SECRET" in env:
            try:
                values["session_secret"] = bytes.fromhex(env["TJ_SESSION_SECRET"])
            except ValueError:
                raise ConfigError(
                    "TJ_SESSION_SECRET must be hexadecimal (generate: python -c 'import secrets; print(secrets.token_hex(32))')"
                ) from None
        elif mode == "production":
            raise ConfigError("TJ_SESSION_SECRET is required in production")
        return cls(**values)
