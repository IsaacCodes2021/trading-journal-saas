"""App-wide settings stored in the database, with defaults."""

from __future__ import annotations

import sqlite3
from datetime import time

from .timeutil import TradingSession, zone

DEFAULTS = {
    # Timezone the CSV timestamps are written in, and the one the app displays.
    "timezone": "America/Los_Angeles",
    # A trading day starts at this wall time in this timezone (CME Globex: 18:00 New York).
    "trading_day_timezone": "America/New_York",
    "trading_day_start": "18:00",
    # Interface color scheme: "dark" or "light".
    "theme": "dark",
}


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting: {key}")
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS[key]


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Validate and store a setting. Changing the trading day settings does not
    touch stored trades; call store.recalculate_trades afterwards."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting: {key}")
    if key in ("timezone", "trading_day_timezone"):
        zone(value)
    elif key == "trading_day_start":
        _parse_hhmm(value)
    elif key == "theme" and value not in ("dark", "light"):
        raise ValueError("theme must be 'dark' or 'light'")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def trading_session(conn: sqlite3.Connection) -> TradingSession:
    return TradingSession(
        timezone=get_setting(conn, "trading_day_timezone"),
        start=_parse_hhmm(get_setting(conn, "trading_day_start")),
    )


def _parse_hhmm(text: str) -> time:
    try:
        hours, minutes = (int(part) for part in text.split(":"))
        return time(hours, minutes)
    except ValueError:
        raise ValueError(f"expected a time like 18:00, got {text!r}") from None
