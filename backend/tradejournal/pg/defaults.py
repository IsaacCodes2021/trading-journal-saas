"""What a new user starts with. Runs inside their tenant session, right after the account row exists (doc 03 section 8)."""

from __future__ import annotations

import psycopg

# Shown as "categories" in the app. Today they are seeded by SQLite migration v1 for the single user.
DEFAULT_TAG_GROUPS = ("Setup", "Mistake", "Emotion")


def provision_defaults(conn: psycopg.Connection) -> None:
    """Idempotent: calling it twice leaves one settings row, one usage row and one set of default categories."""
    conn.execute("INSERT INTO user_settings DEFAULT VALUES ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO user_usage DEFAULT VALUES ON CONFLICT DO NOTHING")
    with conn.cursor() as cursor:  # a psycopg connection has no executemany; the cursor does
        cursor.executemany(
            "INSERT INTO tag_groups (name, sort_order) VALUES (%s, %s) ON CONFLICT (user_id, lower(name)) DO NOTHING",
            [(name, order) for order, name in enumerate(DEFAULT_TAG_GROUPS, start=1)],
        )
