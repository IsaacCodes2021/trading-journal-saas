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

from ..bucketstore import BucketSettings
from .ratelimit import RateLimits

REPO_ROOT = Path(__file__).resolve().parents[3]
MIN_SECRET_BYTES = 32
MAX_CONCURRENT_IMPORTS = (
    16  # a ceiling on the setting: more than the worker threads can spare is a mistake, not a choice
)


_SENDER = re.compile(r"(?:[^<>\r\n@]{1,80} <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>|[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+)")


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
    # How many imports may run at once in this process (api/slots.py); the rest are told to try again in a few seconds.
    max_concurrent_imports: int = 2
    # Where screenshot files are kept (filestore.LocalFileStore). Left out, the screenshot routes answer 503: a host with
    # no persistent disk must use the object store instead (doc 03 section 6), which is chosen in api/app.py.
    files_dir: Path | None = None
    # Or a private bucket (Cloudflare R2, or anything that speaks the S3 API), for a host with no persistent disk and for the
    # sweep job, which runs in another process: the four TJ_S3_* settings together (bucketstore.py). Not both this and a folder.
    bucket: BucketSettings | None = field(default=None, repr=False)
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
    # Email (api/mailer.py): the provider's key and the address mail comes from, together or not at all. With neither, the form
    # that joins the beta answers 503 (nobody could receive the code). `email_api_url` is the provider's address, for tests.
    email_api_key: str | None = field(default=None, repr=False)
    email_from: str | None = None
    email_api_url: str = "https://api.resend.com"
    # Rate limits (api/ratelimit.py). `client_ip_header` names the header that carries the real client address when every
    # request comes through a proxy that sets it (CF-Connecting-IP behind Cloudflare); left out, the socket's address is used.
    rate_limits: RateLimits = field(default_factory=RateLimits)
    client_ip_header: str | None = None
    # Billing (the billing design; docs/design/paddle-integration.md). Off: sign-up, access and every page are exactly as they
    # were, and the billing routes, the webhook and the payment page answer 404. On: a 7-day trial at sign-up, nothing live
    # means read-only, and the provider below is used. `billing_provider` is "fake" (tests and local clicking-through; refused
    # in production) or "paddle". The webhook writes as its own database role (`TJ_WEBHOOK_DATABASE_URL`).
    billing_enabled: bool = False
    billing_provider: str | None = None
    webhook_database_url: str | None = field(default=None, repr=False)
    # The fake provider's webhook secret: a fresh one per process unless a test gives one.
    fake_billing_secret: str = field(default_factory=lambda: secrets.token_hex(16), repr=False)

    def __post_init__(self) -> None:
        if self.max_json_bytes < 1 or self.max_upload_bytes < 1:
            raise ConfigError("body size limits must be positive")
        if not 1 <= self.max_concurrent_imports <= MAX_CONCURRENT_IMPORTS:
            raise ConfigError(f"TJ_MAX_CONCURRENT_IMPORTS must be from 1 to {MAX_CONCURRENT_IMPORTS}")
        for name, url in (
            ("TJ_DATABASE_URL", self.database_url),
            ("TJ_LOGIN_DATABASE_URL", self.login_database_url),
            ("TJ_ADMIN_DATABASE_URL", self.admin_database_url),
            ("TJ_WEBHOOK_DATABASE_URL", self.webhook_database_url),
        ):
            if url is not None and urlsplit(url).scheme not in ("postgresql", "postgres"):
                raise ConfigError(f"{name} must be a postgresql:// URL")
        if self.bucket is not None and self.files_dir is not None:
            raise ConfigError("TJ_S3_* and TJ_FILES_DIR are two places to keep files: choose one")
        if self.bucket is not None and self.production and not self.bucket.https:
            raise ConfigError("TJ_S3_ENDPOINT must be https in production")
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
        if bool(self.email_api_key) != bool(self.email_from):
            raise ConfigError("TJ_EMAIL_API_KEY and TJ_EMAIL_FROM go together")
        if self.email_from is not None and not _SENDER.fullmatch(self.email_from):
            raise ConfigError("TJ_EMAIL_FROM must look like hello@example.com or Name <hello@example.com>")
        if self.production and self.email_api_key and not self.email_api_url.startswith("https://"):
            raise ConfigError("TJ_EMAIL_API_URL must be https in production")
        if self.client_ip_header is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.client_ip_header):
            raise ConfigError("TJ_CLIENT_IP_HEADER must be a header name such as CF-Connecting-IP")
        if self.signup_mode not in ("invite", "open"):
            raise ConfigError("TJ_SIGNUP_MODE must be 'invite' or 'open'")
        if self.billing_provider is not None and self.billing_provider not in ("fake", "paddle"):
            raise ConfigError("TJ_BILLING_PROVIDER must be 'fake' or 'paddle'")
        if self.billing_enabled and self.billing_provider is None:
            raise ConfigError("TJ_BILLING_ENABLED needs TJ_BILLING_PROVIDER")
        if self.production and self.billing_provider == "fake":
            raise ConfigError("TJ_BILLING_PROVIDER=fake is for tests and development: it takes no payment")
        if self.production and self.billing_enabled and self.webhook_database_url is None:
            raise ConfigError("TJ_BILLING_ENABLED needs TJ_WEBHOOK_DATABASE_URL: the webhook writes as its own role")
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
        if env.get("TJ_WEBHOOK_DATABASE_URL"):
            values["webhook_database_url"] = env["TJ_WEBHOOK_DATABASE_URL"]
        switch = env.get("TJ_BILLING_ENABLED", "").strip().lower()
        if switch not in ("", "0", "false", "no", "off", "1", "true", "yes", "on"):
            raise ConfigError("TJ_BILLING_ENABLED must be true or false")
        values["billing_enabled"] = switch in ("1", "true", "yes", "on")
        if env.get("TJ_BILLING_PROVIDER", "").strip():
            values["billing_provider"] = env["TJ_BILLING_PROVIDER"].strip().lower()
        for key, name in (
            ("TJ_KINDE_DOMAIN", "kinde_domain"),
            ("TJ_KINDE_CLIENT_ID", "kinde_client_id"),
            ("TJ_KINDE_CLIENT_SECRET", "kinde_client_secret"),
            ("TJ_SIGNUP_MODE", "signup_mode"),
            ("TJ_CLIENT_IP_HEADER", "client_ip_header"),
            ("TJ_EMAIL_API_KEY", "email_api_key"),
            ("TJ_EMAIL_FROM", "email_from"),
            ("TJ_EMAIL_API_URL", "email_api_url"),
        ):
            if env.get(key):
                values[name] = env[key].strip()
        if env.get("TJ_FILES_DIR", "").strip():
            values["files_dir"] = Path(env["TJ_FILES_DIR"].strip())
        try:
            bucket = BucketSettings.from_env(env)
        except ValueError as problem:
            raise ConfigError(str(problem)) from None
        if bucket is not None:
            values["bucket"] = bucket
        if env.get("TJ_MAX_CONCURRENT_IMPORTS"):
            try:
                values["max_concurrent_imports"] = int(env["TJ_MAX_CONCURRENT_IMPORTS"].strip())
            except ValueError:
                raise ConfigError("TJ_MAX_CONCURRENT_IMPORTS must be a whole number") from None
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
