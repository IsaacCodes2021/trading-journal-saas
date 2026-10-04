"""Settings for the hosted API, read from environment variables (the container is configured, never edited)."""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from .ratelimit import RateLimits

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
    # The `login` system role's URL (app_login): finds a user by id or identity at sign-in. Held apart from the request
    # role because it can read the users table, which the request role cannot (doc 03 section 1).
    login_database_url: str | None = field(default=None, repr=False)
    admin_database_url: str | None = field(
        default=None, repr=False
    )  # the operator's role (app_admin); none: no operator tools
    # Development sign-in: every request is this user. It exists so the app can be run against a local database before
    # real sign-in is built (M5); the configuration refuses it in production.
    dev_user_id: UUID | None = None
    # Sign-in through Kinde (ADR-003). All three or none: with none, /auth/login answers 503 and nobody can sign in.
    kinde_domain: str | None = None  # https://<tenant>.kinde.com, or the custom domain
    kinde_client_id: str | None = None
    kinde_client_secret: str | None = field(default=None, repr=False)
    # "invite": a new person needs a valid invitation code (the private beta). "open": anyone may sign up.
    signup_mode: str = "invite"
    # Rate limits (api/ratelimit.py). `client_ip_header` names the header that carries the real client address when every
    # request comes through a proxy that sets it (CF-Connecting-IP behind Cloudflare); left out, the socket's address is used.
    rate_limits: RateLimits = field(default_factory=RateLimits)
    client_ip_header: str | None = None

    def __post_init__(self) -> None:
        if self.max_json_bytes < 1 or self.max_upload_bytes < 1:
            raise ConfigError("body size limits must be positive")
        for name, url in (
            ("TJ_DATABASE_URL", self.database_url),
            ("TJ_LOGIN_DATABASE_URL", self.login_database_url),
            ("TJ_ADMIN_DATABASE_URL", self.admin_database_url),
        ):
            if url is not None and urlsplit(url).scheme not in ("postgresql", "postgres"):
                raise ConfigError(f"{name} must be a postgresql:// URL")
        kinde = (self.kinde_domain, self.kinde_client_id, self.kinde_client_secret)
        if any(kinde) and not all(kinde):
            raise ConfigError("TJ_KINDE_DOMAIN, TJ_KINDE_CLIENT_ID and TJ_KINDE_CLIENT_SECRET go together")
        if self.kinde_domain is not None:
            domain = urlsplit(self.kinde_domain)
            if (
                domain.scheme != "https"
                or not domain.netloc
                or domain.path.strip("/")
                or domain.query
                or domain.fragment
            ):
                raise ConfigError("TJ_KINDE_DOMAIN must look like https://your-tenant.kinde.com (no path)")
        if self.client_ip_header is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.client_ip_header):
            raise ConfigError("TJ_CLIENT_IP_HEADER must be a header name such as CF-Connecting-IP")
        if self.signup_mode not in ("invite", "open"):
            raise ConfigError("TJ_SIGNUP_MODE must be 'invite' or 'open'")
        if self.dev_user_id is not None:
            if self.production:
                raise ConfigError("TJ_DEV_USER_ID is for development only: it signs every request in as that user")
            if self.database_url is None or self.login_database_url is None:
                raise ConfigError("TJ_DEV_USER_ID needs TJ_DATABASE_URL and TJ_LOGIN_DATABASE_URL")
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
        if env.get("TJ_LOGIN_DATABASE_URL"):
            values["login_database_url"] = env["TJ_LOGIN_DATABASE_URL"]
        if env.get("TJ_ADMIN_DATABASE_URL"):
            values["admin_database_url"] = env["TJ_ADMIN_DATABASE_URL"]
        for key, name in (
            ("TJ_KINDE_DOMAIN", "kinde_domain"),
            ("TJ_KINDE_CLIENT_ID", "kinde_client_id"),
            ("TJ_KINDE_CLIENT_SECRET", "kinde_client_secret"),
            ("TJ_SIGNUP_MODE", "signup_mode"),
            ("TJ_CLIENT_IP_HEADER", "client_ip_header"),
        ):
            if env.get(key):
                values[name] = env[key].strip()
        if env.get("TJ_RATE_LIMITS", "").strip().lower() in ("off", "0", "false", "no"):
            values["rate_limits"] = RateLimits(
                enabled=False
            )  # an emergency or development switch, not a setting to run on
        if env.get("TJ_DEV_USER_ID"):
            try:
                values["dev_user_id"] = UUID(env["TJ_DEV_USER_ID"])
            except ValueError:
                raise ConfigError("TJ_DEV_USER_ID must be a user id (a UUID)") from None
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
