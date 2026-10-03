"""Apply the SQL files in migrations/ in order, once each, as the schema owner.

    TJ_OWNER_URL=postgresql://tj_owner:...@host/db python -m tradejournal.pg.migrate

This runs as a release step (doc 02 section 7), never when the app starts. A Postgres advisory lock makes two
simultaneous runs take turns instead of colliding. A migration that has shipped must not change: its checksum is
stored, and a file that no longer matches stops the run (add a new migration instead).
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOCK_KEY = 7_283_001  # arbitrary but fixed: every runner must use the same number


class MigrationError(RuntimeError):
    pass


def latest_version(directory: Path = MIGRATIONS_DIR) -> str:
    """The newest migration this code ships with: what the database must have applied before this image takes traffic."""
    versions = sorted(path.stem for path in directory.glob("*.sql"))
    if not versions:
        raise MigrationError(f"no migrations found in {directory}")
    return versions[-1]


def migrate(owner_url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply what is pending. Returns the versions applied by this call."""
    applied_now: list[str] = []
    with psycopg.connect(owner_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())
            for path in sorted(directory.glob("*.sql")):
                text = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(text.encode()).hexdigest()
                if path.stem in done:
                    if done[path.stem] != checksum:
                        raise MigrationError(f"{path.name} changed after it was applied; add a new migration instead")
                    continue
                with conn.transaction():
                    conn.execute(text)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", (path.stem, checksum)
                    )
                applied_now.append(path.stem)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return applied_now


def main() -> int:
    url = os.environ.get("TJ_OWNER_URL")
    if not url:
        print("TJ_OWNER_URL is not set", file=sys.stderr)
        return 2
    applied = migrate(url)
    print("applied: " + ", ".join(applied) if applied else "nothing to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
