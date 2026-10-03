"""Create the database roles and databases the Postgres code expects. For development and CI only.

    # PGPASSWORD holds the superuser's password; nothing is printed.
    python -m tradejournal.pg.provision --superuser-url postgresql://postgres@127.0.0.1:54318/postgres --env-file .env.test

Production creates the same roles once, by hand, on the managed database (doc 03 section 1). Every one is a plain login
role: not a superuser, no BYPASSRLS, no CREATEDB or CREATEROLE.

- ``tj_owner`` owns the schema and runs migrations.
- ``app_rw`` serves requests as a tenant: row-level security always applies to it.
- ``app_login``, ``app_webhook``, ``app_sweep`` and ``app_admin`` are the four ``system_session`` profiles. Each has its
  own narrow grant list (migration 0004), and none has any grant on a tenant table except ``app_admin``'s read of the
  usage counters.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

OWNER_ROLE = "tj_owner"
APP_ROLE = "app_rw"
# system_session profile -> the database role it connects as
SYSTEM_ROLES = {"login": "app_login", "webhook": "app_webhook", "sweep": "app_sweep", "admin": "app_admin"}
DATABASES = {"test": "tj_test", "dev": "tj_dev"}
_PASSWORD_KEYS = {
    OWNER_ROLE: "TJ_OWNER_PASSWORD",
    APP_ROLE: "TJ_APP_PASSWORD",
    **{role: f"TJ_{profile.upper()}_PASSWORD" for profile, role in SYSTEM_ROLES.items()},
}
_ALPHABET = string.ascii_letters + string.digits  # URL-safe, so a password can sit in a connection string as is


def random_password(length: int = 32) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def provision(superuser_url: str, *, passwords: Mapping[str, str]) -> None:
    """Idempotent: running it again re-applies the passwords and leaves existing roles and databases in place.

    ``passwords`` maps each role name (``tj_owner``, ``app_rw``, and the system roles) to its password.
    """
    with psycopg.connect(superuser_url, autocommit=True) as conn:
        for role, password in passwords.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            # CREATE ROLE is a utility statement: it takes no bound parameters, so the password is quoted client side.
            conn.execute(
                sql.SQL(f"{verb} ROLE {{}} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {{}}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
        for database in DATABASES.values():
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone():
                conn.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(OWNER_ROLE))
                )


def url_for(superuser_url: str, role: str, password: str, database: str) -> str:
    parts = urlsplit(superuser_url)
    return f"{parts.scheme}://{role}:{password}@{parts.hostname}:{parts.port}/{database}"


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def password_key(role: str) -> str:
    """The env file key holding a role's password: tj_owner -> TJ_OWNER_PASSWORD, app_login -> TJ_LOGIN_PASSWORD."""
    return _PASSWORD_KEYS[role]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--superuser-url", required=True, help="a superuser connection URL without the password")
    parser.add_argument("--env-file", type=Path, help="write (or reuse) role passwords and connection URLs here")
    args = parser.parse_args(argv)

    existing = read_env_file(args.env_file) if args.env_file else {}
    roles = [OWNER_ROLE, APP_ROLE, *SYSTEM_ROLES.values()]
    passwords = {role: existing.get(password_key(role)) or random_password() for role in roles}
    provision(args.superuser_url, passwords=passwords)

    if args.env_file:
        lines = ["# Local development credentials for synthetic data. Generated; never commit.", ""]
        lines += [f"{password_key(role)}={passwords[role]}" for role in roles]
        for label, database in DATABASES.items():
            lines.append(
                f"TJ_{label.upper()}_OWNER_URL={url_for(args.superuser_url, OWNER_ROLE, passwords[OWNER_ROLE], database)}"
            )
            lines.append(
                f"TJ_{label.upper()}_APP_URL={url_for(args.superuser_url, APP_ROLE, passwords[APP_ROLE], database)}"
            )
            for profile, role in SYSTEM_ROLES.items():
                lines.append(
                    f"TJ_{label.upper()}_{profile.upper()}_URL={url_for(args.superuser_url, role, passwords[role], database)}"
                )
        args.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {args.env_file} (contains passwords; it is gitignored)")
    print(f"roles {', '.join(roles)}; databases {', '.join(DATABASES.values())}: ready")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
