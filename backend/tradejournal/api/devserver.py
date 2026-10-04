"""Run the API on your own machine, signed in as one user of the local development database. Never for production.

    cd backend
    .venv\\Scripts\\python.exe -m tradejournal.api.devserver

It reads the development database URLs from the gitignored `backend/.env.test` (written by
`python -m tradejournal.pg.provision`), so no password is typed or printed, and it listens on 127.0.0.1 only. With no
`--user-id` it signs in as the one user in the database (an error if there are none or several). Pair it with
`TJ_DEV_API=real pnpm dev` in `frontend/` (docs/runbooks/run-locally.md).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

import psycopg

from ..pg.migrate import latest_version, migrate
from ..pg.provision import read_env_file
from .app import create_app
from .config import ApiConfig, ConfigError

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.test"


class DevServerError(Exception):
    """Something the person starting the server can fix; the message says what."""


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


def build_config(env_file: Path, *, origin: str, user_id: str | None, migrate_now: bool = False) -> ApiConfig:
    env = read_env_file(env_file)
    app_url, login_url = env.get("TJ_DEV_APP_URL"), env.get("TJ_DEV_LOGIN_URL")
    if not app_url or not login_url:
        raise DevServerError(f"{env_file.name} has no TJ_DEV_APP_URL and TJ_DEV_LOGIN_URL: run the provisioning script")
    if applied := check_schema(env, migrate_now=migrate_now):
        print(applied)
    try:
        return ApiConfig(
            app_origin=origin,
            database_url=app_url,
            login_database_url=login_url,
            dev_user_id=pick_user(login_url, user_id),
        )
    except ConfigError as problem:
        raise DevServerError(str(problem)) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    parser.add_argument("--user-id", help="the user to sign in as (default: the only user in the database)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--migrate", action="store_true", help="apply pending migrations to the development database")
    parser.add_argument(
        "--origin",
        default="http://localhost:5173",
        help="where the browser reaches the app; the front end's dev server by default (writes are checked against it)",
    )
    args = parser.parse_args(argv)
    try:
        config = build_config(args.env_file, origin=args.origin, user_id=args.user_id, migrate_now=args.migrate)
    except (DevServerError, psycopg.Error, OSError) as problem:
        print(f"cannot start: {problem}", file=sys.stderr)
        return 2
    import uvicorn  # imported here: the checks above need none of it

    print(f"Development sign-in: every request is user {str(config.dev_user_id)[:8]}... on {config.app_origin}")
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
