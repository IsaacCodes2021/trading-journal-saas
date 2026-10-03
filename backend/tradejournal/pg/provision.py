"""Create the database roles and databases the Postgres code expects. For development and CI only.

    # PGPASSWORD holds the superuser's password; nothing is printed.
    python -m tradejournal.pg.provision --superuser-url postgresql://postgres@127.0.0.1:54318/postgres --env-file .env.test

Production creates the same two roles once, by hand, on the managed database (doc 03 section 1):

- ``tj_owner`` owns the schema and runs migrations. It is not a superuser.
- ``app_rw`` serves requests. It is not a superuser and lacks BYPASSRLS, so row-level security always applies to it.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

OWNER_ROLE = "tj_owner"
APP_ROLE = "app_rw"
DATABASES = {"test": "tj_test", "dev": "tj_dev"}
_ALPHABET = string.ascii_letters + string.digits  # URL-safe, so a password can sit in a connection string as is


def random_password(length: int = 32) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def provision(superuser_url: str, *, owner_password: str, app_password: str) -> None:
    """Idempotent: running it again re-applies the passwords and leaves existing roles and databases in place."""
    with psycopg.connect(superuser_url, autocommit=True) as conn:
        for role, password in ((OWNER_ROLE, owner_password), (APP_ROLE, app_password)):
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--superuser-url", required=True, help="a superuser connection URL without the password")
    parser.add_argument("--env-file", type=Path, help="write (or reuse) role passwords and connection URLs here")
    args = parser.parse_args(argv)

    existing = read_env_file(args.env_file) if args.env_file else {}
    owner_password = existing.get("TJ_OWNER_PASSWORD") or random_password()
    app_password = existing.get("TJ_APP_PASSWORD") or random_password()
    provision(args.superuser_url, owner_password=owner_password, app_password=app_password)

    if args.env_file:
        lines = ["# Local development credentials for synthetic data. Generated; never commit.", ""]
        lines += [f"TJ_OWNER_PASSWORD={owner_password}", f"TJ_APP_PASSWORD={app_password}"]
        for label, database in DATABASES.items():
            lines += [
                f"TJ_{label.upper()}_OWNER_URL={url_for(args.superuser_url, OWNER_ROLE, owner_password, database)}",
                f"TJ_{label.upper()}_APP_URL={url_for(args.superuser_url, APP_ROLE, app_password, database)}",
            ]
        args.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {args.env_file} (contains passwords; it is gitignored)")
    print(f"roles {OWNER_ROLE} and {APP_ROLE}, databases {', '.join(DATABASES.values())}: ready")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
