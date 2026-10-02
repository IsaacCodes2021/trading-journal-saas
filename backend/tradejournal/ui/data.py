"""Database access for the interface: one short-lived connection per operation.

Short-lived connections keep things simple when work moves to a background
thread (imports run off the UI thread so the page stays responsive).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator
from zoneinfo import ZoneInfo

from .. import config, db, settings
from ..timeutil import zone


@contextmanager
def open_db() -> Iterator[sqlite3.Connection]:
    conn = db.connect(config.db_path())
    try:
        yield conn
    finally:
        conn.close()


def display_zone() -> ZoneInfo:
    with open_db() as conn:
        return zone(settings.get_setting(conn, "timezone"))


def is_dark() -> bool:
    with open_db() as conn:
        return settings.get_setting(conn, "theme") == "dark"


def set_dark(dark: bool) -> None:
    with open_db() as conn, db.transaction(conn):
        settings.set_setting(conn, "theme", "dark" if dark else "light")
