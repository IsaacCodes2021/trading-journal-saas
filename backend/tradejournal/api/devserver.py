"""Run the API on your own machine, signed in as one user of the local development database. Never for production.

    cd backend
    .venv\\Scripts\\python.exe -m tradejournal.api.devserver

It reads the development database URLs from the gitignored `backend/.env.test` (written by
`python -m tradejournal.pg.provision`), so no password is typed or printed, and it listens on 127.0.0.1 only. With no
`--user-id` it signs in as the one user in the database (an error if there are none or several). Pair it with
`TJ_DEV_API=real pnpm dev` in `frontend/` (docs/runbooks/run-locally.md).

`--real-sign-in` runs the real sign-in flow instead: people sign in through Kinde (the settings come from the gitignored
`backend/.env.kinde`), get real sessions, and a new person needs an invite (`--make-invite` prints one). Pair it with
`TJ_DEV_API=signin pnpm dev` (docs/runbooks/try-real-sign-in.md). Still development only: it listens on 127.0.0.1 and
uses the local database.

`--billing` (with `--real-sign-in`) turns billing on with the Paddle sandbox, to take a test payment on this machine: the
six `TJ_PADDLE_*` settings come from the gitignored `backend/.env.paddle`, and only a sandbox is accepted. Paddle's webhooks
reach this server through a tunnel (docs/runbooks/billing.md, "Paddle sandbox").

`--billing --stripe` uses Stripe's test mode instead, beside Paddle (docs/design/stripe-plan.md): the `TJ_STRIPE_*`
settings come from the gitignored `backend/.env.stripe`, only test keys are accepted, and Stripe's webhooks come through
the same kind of tunnel. Plain `--billing` stays the Paddle sandbox.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path
from typing import TextIO
from uuid import UUID

import psycopg

from ..billing.paddle import PaddleSettings
from ..billing.stripe import StripeSettings
from ..pg.invites import create_invite
from ..pg.migrate import latest_version, migrate
from ..pg.provision import read_env_file
from .app import create_app
from .config import ApiConfig, ConfigError
from .logs import scrub

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.test"
KINDE_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.kinde"
KINDE_KEYS = ("KINDE_DOMAIN", "KINDE_CLIENT_ID", "KINDE_CLIENT_SECRET")
PADDLE_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.paddle"
PADDLE_KEYS = (
    "TJ_PADDLE_ENV", "TJ_PADDLE_API_KEY", "TJ_PADDLE_CLIENT_TOKEN", "TJ_PADDLE_WEBHOOK_SECRET",
    "TJ_PADDLE_PRODUCT_ID", "TJ_PADDLE_PRICE_ID",
)  # fmt: skip
STRIPE_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.stripe"
STRIPE_KEYS = (
    "TJ_STRIPE_ENV", "TJ_STRIPE_API_KEY", "TJ_STRIPE_WEBHOOK_SECRET", "TJ_STRIPE_PRODUCT_ID", "TJ_STRIPE_PRICE_ID",
)  # fmt: skip


class DevServerError(Exception):
    """Something the person starting the server can fix; the message says what."""


class _ScrubbedLine(logging.Formatter):
    def formatMessage(self, record: logging.LogRecord) -> str:
        line = super().formatMessage(record)
        fields = getattr(record, "fields", None)  # what production writes as JSON (a request: method, path, status)
        if isinstance(fields, dict):
            line += "".join(f" {key}={value}" for key, value in fields.items())
        return line

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


def show_log_lines(stream: TextIO | None = None) -> logging.Handler:
    """Print the app's own records (`tj.*`, from INFO) beside the server's, as plain lines scrubbed as in production. In
    production they go to `configure_logging`'s JSON stream; without either, they go nowhere (api/logs.py)."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(_ScrubbedLine("%(levelname)s:     %(name)s: %(message)s"))
    ours = logging.getLogger("tj")
    ours.addHandler(handler)
    ours.setLevel(logging.INFO)
    return handler


def pick_user(login_url: str, wanted: str | None) -> UUID:
    """The user to be: the one asked for, else the only user in the database."""
    with psycopg.connect(login_url) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM users ORDER BY created_at")]
    if wanted:
        try:
            user_id = UUID(wanted)
        except ValueError:
            raise DevServerError("--user-id must be a user id (a UUID)") from None
        if user_id not in ids:
            raise DevServerError("there is no such user in the development database")
        return user_id
    if len(ids) != 1:
        raise DevServerError(f"the development database has {len(ids)} users; choose one with --user-id")
    return ids[0]


def check_schema(env: dict[str, str], *, migrate_now: bool) -> str | None:
    """Is the development database as new as this code expects? Migrations are a deliberate step, so by default a
    database that is behind stops the launch with the command to run; `--migrate` applies them as the schema owner
    (the same runner as a release, `pg.migrate`). Returns a line to print when something was applied."""
    with psycopg.connect(env["TJ_DEV_APP_URL"]) as conn:
        current = conn.execute("SELECT app_schema_version()").fetchone()[0]
    wanted = latest_version()
    if current == wanted:
        return None
    if not migrate_now:
        raise DevServerError(
            f"the development database is at {current or 'no migrations'} but this code needs {wanted}: "
            "start again with --migrate to apply them"
        )
    if not env.get("TJ_DEV_OWNER_URL"):
        raise DevServerError("--migrate needs TJ_DEV_OWNER_URL in the env file: run the provisioning script")
    return "applied migrations: " + ", ".join(migrate(env["TJ_DEV_OWNER_URL"]))


def kinde_settings(kinde_env_file: Path) -> dict[str, str]:
    """The three Kinde settings, from the gitignored file. Only the names of what is missing are ever said."""
    if not kinde_env_file.exists():
        raise DevServerError(f"{kinde_env_file.name} does not exist: it holds {', '.join(KINDE_KEYS)}")
    env = read_env_file(kinde_env_file)
    missing = [key for key in KINDE_KEYS if not env.get(key)]
    if missing:
        raise DevServerError(f"{kinde_env_file.name} has no value for {', '.join(missing)}")
    return {key: env[key].strip() for key in KINDE_KEYS}


def billing_settings(env: dict[str, str], env_file: Path, paddle_env_file: Path) -> dict[str, object]:
    """Billing on with the Paddle sandbox, and the webhook's and the operator's database roles. Only the names of what is
    missing are ever said; a live key or `TJ_PADDLE_ENV=production` is refused (a test payment here takes no money)."""
    if not paddle_env_file.exists():
        raise DevServerError(f"{paddle_env_file.name} does not exist: it holds {', '.join(PADDLE_KEYS)}")
    paddle_env = read_env_file(paddle_env_file)
    missing = [key for key in PADDLE_KEYS if not paddle_env.get(key, "").strip()]
    if missing:
        raise DevServerError(f"{paddle_env_file.name} has no value for {', '.join(missing)}")
    if paddle_env["TJ_PADDLE_ENV"].strip().lower() != "sandbox":
        raise DevServerError("--billing is for the Paddle sandbox only: TJ_PADDLE_ENV must be sandbox")
    if not env.get("TJ_DEV_WEBHOOK_URL"):
        raise DevServerError(f"{env_file.name} has no TJ_DEV_WEBHOOK_URL: run the provisioning script")
    try:
        paddle = PaddleSettings.from_env(paddle_env)
    except ValueError as problem:  # names the setting, never its value
        raise DevServerError(str(problem)) from None
    return {
        "billing_enabled": True,
        "billing_provider": "paddle",
        "paddle": paddle,
        "webhook_database_url": env["TJ_DEV_WEBHOOK_URL"],
        "admin_database_url": env.get("TJ_DEV_ADMIN_URL") or None,  # discount codes are made in the operator's tools
    }


def stripe_billing_settings(env: dict[str, str], env_file: Path, stripe_env_file: Path) -> dict[str, object]:
    """Billing on with Stripe's test mode, as `billing_settings` does for the Paddle sandbox. A live key or
    `TJ_STRIPE_ENV=live` is refused: a test payment here takes no money."""
    if not stripe_env_file.exists():
        raise DevServerError(f"{stripe_env_file.name} does not exist: it holds {', '.join(STRIPE_KEYS)}")
    stripe_env = read_env_file(stripe_env_file)
    missing = [key for key in STRIPE_KEYS if not stripe_env.get(key, "").strip()]
    if missing:
        raise DevServerError(f"{stripe_env_file.name} has no value for {', '.join(missing)}")
    if stripe_env["TJ_STRIPE_ENV"].strip().lower() != "test":
        raise DevServerError("--stripe is for Stripe's test mode only: TJ_STRIPE_ENV must be test")
    if not env.get("TJ_DEV_WEBHOOK_URL"):
        raise DevServerError(f"{env_file.name} has no TJ_DEV_WEBHOOK_URL: run the provisioning script")
    try:
        stripe = StripeSettings.from_env(stripe_env)
    except ValueError as problem:  # names the setting, never its value
        raise DevServerError(str(problem)) from None
    return {
        "billing_enabled": True,
        "billing_provider": "stripe",
        "stripe": stripe,
        "webhook_database_url": env["TJ_DEV_WEBHOOK_URL"],
        "admin_database_url": env.get("TJ_DEV_ADMIN_URL") or None,
    }


def build_config(
    env_file: Path,
    *,
    origin: str,
    user_id: str | None,
    migrate_now: bool = False,
    real_sign_in: bool = False,
    kinde_env_file: Path = KINDE_ENV_FILE,
    billing: bool = False,
    paddle_env_file: Path = PADDLE_ENV_FILE,
    stripe: bool = False,
    stripe_env_file: Path = STRIPE_ENV_FILE,
) -> ApiConfig:
    env = read_env_file(env_file)
    app_url, login_url = env.get("TJ_DEV_APP_URL"), env.get("TJ_DEV_LOGIN_URL")
    if not app_url or not login_url:
        raise DevServerError(f"{env_file.name} has no TJ_DEV_APP_URL and TJ_DEV_LOGIN_URL: run the provisioning script")
    if real_sign_in and user_id:
        raise DevServerError("--user-id is for the development sign-in; with --real-sign-in people sign in themselves")
    if billing and not real_sign_in:
        raise DevServerError(
            "--billing needs --real-sign-in: billing runs on real sessions, which the development sign-in has not"
        )
    if stripe and not billing:
        raise DevServerError("--stripe goes with --billing: it picks Stripe's test mode instead of the Paddle sandbox")
    kinde = kinde_settings(kinde_env_file) if real_sign_in else None
    if billing and stripe:
        paying = stripe_billing_settings(env, env_file, stripe_env_file)
    else:
        paying = billing_settings(env, env_file, paddle_env_file) if billing else {}
    if applied := check_schema(env, migrate_now=migrate_now):
        print(applied)
    try:
        if kinde:
            return ApiConfig(
                app_origin=origin,
                database_url=app_url,
                login_database_url=login_url,
                kinde_domain=kinde["KINDE_DOMAIN"],
                kinde_client_id=kinde["KINDE_CLIENT_ID"],
                kinde_client_secret=kinde["KINDE_CLIENT_SECRET"],
                signup_mode="invite",
                **paying,  # type: ignore[arg-type]  # the billing fields, with their types (billing_settings)
            )
        return ApiConfig(
            app_origin=origin,
            database_url=app_url,
            login_database_url=login_url,
            dev_user_id=pick_user(login_url, user_id),
        )
    except ConfigError as problem:
        raise DevServerError(str(problem)) from None


def make_invite(env_file: Path, *, email: str | None) -> str:
    """One single-use invite code for the local development database (made as its schema owner)."""
    owner_url = read_env_file(env_file).get("TJ_DEV_OWNER_URL")
    if not owner_url:
        raise DevServerError(f"{env_file.name} has no TJ_DEV_OWNER_URL: run the provisioning script")
    with psycopg.connect(owner_url, autocommit=True) as conn:
        return create_invite(conn, label="local trial", email=email, max_uses=1, expires_in=timedelta(days=7))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument("--kinde-env-file", type=Path, default=KINDE_ENV_FILE, help="with --real-sign-in")
    parser.add_argument("--user-id", help="the user to sign in as (default: the only user in the database)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--migrate", action="store_true", help="apply pending migrations to the development database")
    parser.add_argument(
        "--real-sign-in",
        action="store_true",
        help="sign in through Kinde (settings from backend/.env.kinde) instead of the one-user development sign-in",
    )
    parser.add_argument(
        "--billing",
        action="store_true",
        help="with --real-sign-in: billing on with the Paddle sandbox (settings from backend/.env.paddle)",
    )
    parser.add_argument("--paddle-env-file", type=Path, default=PADDLE_ENV_FILE, help="with --billing")
    parser.add_argument(
        "--stripe",
        action="store_true",
        help="with --billing: Stripe's test mode instead of the Paddle sandbox (settings from backend/.env.stripe)",
    )
    parser.add_argument("--stripe-env-file", type=Path, default=STRIPE_ENV_FILE, help="with --billing --stripe")
    parser.add_argument(
        "--make-invite",
        action="store_true",
        help="print a single-use invite code (valid 7 days) for the local database, and exit",
    )
    parser.add_argument("--invite-email", help="with --make-invite: only this email address may use the code")
    parser.add_argument(
        "--origin",
        default="http://localhost:5173",
        help="where the browser reaches the app; the front end's dev server by default (writes are checked against it)",
    )
    args = parser.parse_args(argv)
    if args.make_invite:
        try:
            print(make_invite(args.env_file, email=args.invite_email))
        except (DevServerError, psycopg.Error, OSError) as problem:
            print(f"cannot make an invite: {problem}", file=sys.stderr)
            return 2
        return 0
    try:
        config = build_config(
            args.env_file,
            origin=args.origin,
            user_id=args.user_id,
            migrate_now=args.migrate,
            real_sign_in=args.real_sign_in,
            kinde_env_file=args.kinde_env_file,
            billing=args.billing,
            paddle_env_file=args.paddle_env_file,
            stripe=args.stripe,
            stripe_env_file=args.stripe_env_file,
        )
    except (DevServerError, psycopg.Error, OSError) as problem:
        print(f"cannot start: {problem}", file=sys.stderr)
        return 2
    import uvicorn  # imported here: the checks above need none of it

    show_log_lines()
    if config.dev_user_id is None:
        print(
            f"Real sign-in through Kinde on {config.app_origin} (new people need an invite: --make-invite).\n"
            f"  Allowed callback URL in Kinde:  {config.app_origin}/auth/callback\n"
            f"  Allowed logout redirect URL:    {config.app_origin}/invite"
        )
        if config.billing_enabled and config.billing_provider == "stripe":
            print(
                "Billing on with Stripe's TEST MODE (test cards only, no money moves).\n"
                f"  Payment link:   {config.app_origin}/billing/stripe/pay (sends the browser to Stripe's checkout)\n"
                f"  Webhooks:       a tunnel to http://localhost:{args.port}, and in Stripe a webhook endpoint at\n"
                "                  https://<the tunnel's address>/webhooks/billing (API version "
                "2026-08-26.dahlia)"
            )
        elif config.billing_enabled:
            print(
                "Billing on with the Paddle SANDBOX (test cards only, no money moves).\n"
                f"  Payment page:   {config.app_origin}/billing/pay (Paddle's checkout opens there)\n"
                f"  Webhooks:       a tunnel to http://localhost:{args.port}, and in the sandbox a notification\n"
                "                  destination at https://<the tunnel's address>/webhooks/billing"
            )
    else:
        print(f"Development sign-in: every request is user {str(config.dev_user_id)[:8]}... on {config.app_origin}")
    # No access log, as in production (Dockerfile): the app's own request lines say the same, without query strings.
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
