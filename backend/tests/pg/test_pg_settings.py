"""Settings on Postgres. The SQLite module had no tests of its own (the importer tests touched it); these are new."""

from datetime import time

import psycopg
import pytest

from tradejournal.pg import settings
from tradejournal.settings import DEFAULTS


def test_a_new_user_starts_with_the_defaults(conn):
    assert {key: settings.get_setting(conn, key) for key in DEFAULTS} == DEFAULTS


def test_a_missing_row_reads_as_the_defaults_and_is_created_on_first_write(owner, db):
    row = owner.execute("INSERT INTO users (idp_subject, email) VALUES ('idp|bare', 'bare@example.test') RETURNING id")
    with db.tenant_session(row.fetchone()["id"]) as conn:  # no provision_defaults: no settings row yet
        assert settings.get_setting(conn, "theme") == "dark"
        settings.set_setting(conn, "theme", "light")
        assert settings.get_setting(conn, "theme") == "light"
        assert settings.get_setting(conn, "timezone") == DEFAULTS["timezone"]  # the other columns took their defaults


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("timezone", "Europe/London"),
        ("trading_day_timezone", "America/Chicago"),
        ("trading_day_start", "17:30"),
        ("theme", "light"),
    ],
)
def test_set_then_get(conn, key, value):
    settings.set_setting(conn, key, value)
    assert settings.get_setting(conn, key) == value
    settings.set_setting(conn, key, value)  # the second write updates the same row
    assert conn.execute("SELECT count(*) AS n FROM user_settings").fetchone()["n"] == 1


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("timezone", "Mars/Olympus", "unknown timezone"),
        ("trading_day_timezone", "", "unknown timezone"),
        ("trading_day_start", "25:00", "expected a time"),
        ("trading_day_start", "18:00:30", "expected a time"),
        ("trading_day_start", "evening", "expected a time"),
        ("theme", "blue", "theme must be"),
    ],
)
def test_invalid_values_are_refused_and_change_nothing(conn, key, value, message):
    with pytest.raises(ValueError, match=message):
        settings.set_setting(conn, key, value)
    assert settings.get_setting(conn, key) == DEFAULTS[key]


def test_an_unknown_key_is_refused_and_never_reaches_the_sql(conn):
    for key in ("colour", "theme; DROP TABLE user_settings", "user_id"):
        with pytest.raises(KeyError, match="unknown setting"):
            settings.get_setting(conn, key)
        with pytest.raises(KeyError, match="unknown setting"):
            settings.set_setting(conn, key, "x")
    assert settings.get_setting(conn, "theme") == "dark"


def test_a_start_time_without_a_leading_zero_is_stored_padded(conn):
    """SQLite stored "9:30" as typed; the Postgres column only accepts HH:MM, so the module pads it."""
    settings.set_setting(conn, "trading_day_start", "9:30")
    assert settings.get_setting(conn, "trading_day_start") == "09:30"
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():  # and the database holds the line itself
        conn.execute("UPDATE user_settings SET trading_day_start = '9:30'")


def test_trading_session(conn):
    session = settings.trading_session(conn)
    assert (session.timezone, session.start) == ("America/New_York", time(18, 0))
    settings.set_setting(conn, "trading_day_start", "00:00")
    settings.set_setting(conn, "trading_day_timezone", "America/Los_Angeles")
    session = settings.trading_session(conn)
    assert (session.timezone, session.start) == ("America/Los_Angeles", time(0, 0))


def test_each_user_has_their_own_settings(db, alice, bob):
    with db.tenant_session(alice) as a:
        settings.set_setting(a, "theme", "light")
    with db.tenant_session(bob) as b:
        assert settings.get_setting(b, "theme") == "dark"
        settings.set_setting(b, "timezone", "Asia/Tokyo")
    with db.tenant_session(alice) as a:
        assert settings.get_setting(a, "theme") == "light"
        assert settings.get_setting(a, "timezone") == DEFAULTS["timezone"]


# ---- the CSV timezone (migration 0006) ---------------------------------------------------------------------------


def test_a_new_user_follows_their_own_timezone_for_csv_files(conn):
    assert settings.get_csv_timezone(conn) is None
    assert settings.effective_csv_timezone(conn) == DEFAULTS["timezone"]
    settings.set_setting(conn, "timezone", "Europe/London")
    assert settings.effective_csv_timezone(conn) == "Europe/London"  # it follows, until a zone is chosen


def test_a_chosen_csv_timezone_wins_and_survives_a_change_of_own_timezone(conn):
    settings.set_csv_timezone(conn, "America/Chicago")
    settings.set_setting(conn, "timezone", "Europe/London")
    assert settings.get_csv_timezone(conn) == "America/Chicago"
    assert settings.effective_csv_timezone(conn) == "America/Chicago"


@pytest.mark.parametrize("cleared", [None, ""])
def test_the_csv_timezone_can_go_back_to_following_the_own_timezone(conn, cleared):
    settings.set_csv_timezone(conn, "America/Chicago")
    settings.set_csv_timezone(conn, cleared)
    assert settings.get_csv_timezone(conn) is None
    assert settings.effective_csv_timezone(conn) == DEFAULTS["timezone"]


def test_a_bad_csv_timezone_is_refused_and_changes_nothing(conn):
    settings.set_csv_timezone(conn, "America/Chicago")
    with pytest.raises(ValueError, match="unknown timezone"):
        settings.set_csv_timezone(conn, "Mars/Olympus")
    assert settings.get_csv_timezone(conn) == "America/Chicago"


def test_a_missing_settings_row_is_created_by_the_first_csv_timezone_write(owner, db):
    row = owner.execute("INSERT INTO users (idp_subject, email) VALUES ('idp|csv', 'csv@example.test') RETURNING id")
    with db.tenant_session(row.fetchone()["id"]) as conn:  # no provision_defaults: no settings row yet
        assert settings.get_csv_timezone(conn) is None
        settings.set_csv_timezone(conn, "America/Chicago")
        assert settings.get_csv_timezone(conn) == "America/Chicago"
        assert settings.get_setting(conn, "theme") == "dark"  # the other columns took their defaults


def test_csv_timezone_is_not_one_of_the_key_value_settings(conn):
    with pytest.raises(KeyError):
        settings.get_setting(conn, "csv_timezone")  # it has its own functions: it can be unset
