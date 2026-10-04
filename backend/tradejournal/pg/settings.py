"""One user's settings, on Postgres.

The port of ``tradejournal/settings.py`` (milestone M2). SQLite kept settings as key/value rows for the single user;
here they are the columns of the user's ``user_settings`` row, which ``provision_defaults`` creates at sign-up.
The defaults, the validation and ``_parse_hhmm`` are shared with the SQLite module.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

from ..settings import DEFAULTS, _parse_hhmm
from ..timeutil import TradingSession, zone


def get_setting(conn: psycopg.Connection, key: str) -> str:
    _check_key(key)
    row = conn.execute(sql.SQL("SELECT {} AS value FROM user_settings").format(sql.Identifier(key))).fetchone()
    return row["value"] if row else DEFAULTS[key]


def get_csv_timezone(conn: psycopg.Connection) -> str | None:
    """The zone the person's CSV files are written in, or None: "the same as my timezone" (the default)."""
    row = conn.execute("SELECT csv_timezone FROM user_settings").fetchone()
    return row["csv_timezone"] if row else None


def set_csv_timezone(conn: psycopg.Connection, value: str | None) -> None:
    """Choose the zone CSV files are read in. None or an empty string go back to following `timezone`."""
    if value:
        zone(value)  # raises ValueError("unknown timezone ...") for a name that is not one
    conn.execute(
        "INSERT INTO user_settings (csv_timezone) VALUES (%s) ON CONFLICT (user_id) DO UPDATE SET csv_timezone = EXCLUDED.csv_timezone",
        (value or None,),
    )


def effective_csv_timezone(conn: psycopg.Connection) -> str:
    """The zone an import reads timestamps in: the chosen CSV zone, else the person's own timezone."""
    return get_csv_timezone(conn) or get_setting(conn, "timezone")


def set_setting(conn: psycopg.Connection, key: str, value: str) -> None:
    """Validate and store a setting. Changing the trading day settings does not
    touch stored trades; call store.recalculate_trades afterwards."""
    _check_key(key)
    if key in ("timezone", "trading_day_timezone"):
        zone(value)
    elif key == "trading_day_start":
        # The database stores zero-padded HH:MM (a CHECK enforces it); "9:30" is accepted and stored as "09:30".
        value = _parse_hhmm(value).strftime("%H:%M")
    elif key == "theme" and value not in ("dark", "light"):
        raise ValueError("theme must be 'dark' or 'light'")
    column = sql.Identifier(key)
    # user_id is filled from the session by its column default, so a missing row is created rather than skipped.
    conn.execute(
        sql.SQL(
            "INSERT INTO user_settings ({column}) VALUES (%s) ON CONFLICT (user_id) DO UPDATE SET {column} = EXCLUDED.{column}"
        ).format(column=column),
        (value,),
    )


def trading_session(conn: psycopg.Connection) -> TradingSession:
    return TradingSession(
        timezone=get_setting(conn, "trading_day_timezone"),
        start=_parse_hhmm(get_setting(conn, "trading_day_start")),
    )


def _check_key(key: str) -> None:
    """Column names come from this allow-list only, never from the caller's text."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting: {key}")
