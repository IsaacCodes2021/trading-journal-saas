"""The Postgres account service, and the development sign-in built on it, against a real database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import SAMPLE, fixture_bytes
from pg_helpers import make_account

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.dev import DEV_SESSION_TOKEN, DevSessionStore
from tradejournal.api.pg_services import PgAccountService
from tradejournal.api.schemas import SettingsPatch
from tradejournal.auth.csrf import csrf_token
from tradejournal.pg import accounts, importer
from tradejournal.pg import settings as pg_settings

ORIGIN = "http://localhost:5173"
SECRET = b"k" * 32
BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def service(db, system) -> PgAccountService:
    return PgAccountService(db, system)


# ---- load ---------------------------------------------------------------------------------------------------------


def test_load_describes_the_user_with_their_stored_settings(service, alice):
    account = service.load(alice)
    assert account is not None
    assert account.user.id == alice and account.user.email == "alice@example.test"
    assert account.settings.model_dump() == {
        "timezone": "America/Los_Angeles",
        "csv_timezone": None,
        "trading_day_timezone": "America/New_York",
        "trading_day_start": "18:00",
        "theme": "dark",
    }
    assert (account.plan.effective, account.plan.source, account.is_admin) == ("free", "free", False)


def test_a_missing_suspended_or_deleting_user_is_not_signed_in(service, owner, alice):
    assert service.load(uuid4()) is None
    for status in ("suspended", "pending_deletion"):
        owner.execute("UPDATE users SET status = %s WHERE id = %s", (status, alice))
        assert service.load(alice) is None, status
    owner.execute("UPDATE users SET status = 'active', is_admin = true WHERE id = %s", (alice,))
    account = service.load(alice)
    assert account is not None and account.is_admin is True


# ---- update_settings ----------------------------------------------------------------------------------------------


def test_update_changes_only_the_fields_given_and_returns_all_of_them(service, alice):
    updated = service.update_settings(alice, SettingsPatch(theme="light", timezone="Europe/London"))
    assert (updated.theme, updated.timezone) == ("light", "Europe/London")
    assert (updated.trading_day_timezone, updated.trading_day_start) == ("America/New_York", "18:00")
    assert service.load(alice).settings == updated


def test_the_csv_timezone_can_be_set_and_cleared(service, alice):
    assert (
        service.update_settings(alice, SettingsPatch(csv_timezone="America/Chicago")).csv_timezone == "America/Chicago"
    )
    assert service.update_settings(alice, SettingsPatch(theme="light")).csv_timezone == "America/Chicago"  # untouched
    assert service.update_settings(alice, SettingsPatch(csv_timezone="")).csv_timezone is None  # follow `timezone`


def test_moving_the_trading_day_recalculates_the_trades_in_the_same_change(db, service, alice):
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
        accounts.set_fee(conn, account.id, "*", Decimal("4.00"))
        importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
        conn.execute("UPDATE trades SET trade_date = %s", (date(1999, 1, 1),))  # as if the day settings had changed
    service.update_settings(alice, SettingsPatch(theme="light"))  # not a day setting: the trades are left alone
    with db.tenant_session(alice) as conn:
        assert {r["trade_date"] for r in conn.execute("SELECT trade_date FROM trades")} == {date(1999, 1, 1)}
    service.update_settings(alice, SettingsPatch(trading_day_start="00:00"))
    with db.tenant_session(alice) as conn:
        assert {r["trade_date"] for r in conn.execute("SELECT trade_date FROM trades")} == {date(2024, 1, 5)}


def test_one_users_change_never_touches_anothers_settings(service, alice, bob):
    service.update_settings(alice, SettingsPatch(theme="light", trading_day_start="09:30"))
    other = service.load(bob).settings
    assert (other.theme, other.trading_day_start) == ("dark", "18:00")


# ---- the development sign-in --------------------------------------------------------------------------------------


def dev_client(app_url, login_url, user_id) -> TestClient:
    config = ApiConfig(
        app_origin=ORIGIN,
        web_dist=BACKEND / "nonexistent",
        session_secret=SECRET,
        database_url=app_url,
        login_database_url=login_url,
        dev_user_id=user_id,
    )
    return TestClient(create_app(config), base_url=ORIGIN)


def test_the_dev_session_store_knows_one_word():
    store = DevSessionStore(uuid4())
    assert store.lookup("something else") is None and store.lookup(None) is None
    assert store.lookup(DEV_SESSION_TOKEN) is not None


def test_a_dev_server_signs_every_request_in_as_the_configured_user_and_nobody_else(app_url, login_url, alice, bob):
    cookie = {"cookie": f"__Host-tj_session={DEV_SESSION_TOKEN}"}
    with dev_client(app_url, login_url, alice) as client:
        assert client.get("/api/v1/session").status_code == 401  # no cookie, no session, even in development
        assert client.get("/api/v1/session", headers={"cookie": "__Host-tj_session=guess"}).status_code == 401
        body = client.get("/api/v1/session", headers=cookie).json()
        assert body["user"]["email"] == "alice@example.test" and body["flags"]["is_admin"] is False
        assert body["settings"]["trading_day_start"] == "18:00" and body["plan"]["source"] == "free"
        # A write needs the CSRF token and a same-origin request, exactly as in production.
        write = {**cookie, "sec-fetch-site": "same-origin", "x-csrf-token": csrf_token(SECRET, "dev")}
        assert client.patch("/api/v1/settings", json={"theme": "light"}, headers=cookie).status_code == 403
        saved = client.patch("/api/v1/settings", json={"theme": "light", "csv_timezone": "Asia/Tokyo"}, headers=write)
        assert saved.status_code == 200 and saved.json()["theme"] == "light"
        assert client.get("/api/v1/session", headers=cookie).json()["settings"]["csv_timezone"] == "Asia/Tokyo"
        assert client.get("/api/v1/dashboard", headers=cookie).json()["summary"]["trade_count"] == 0
    with dev_client(app_url, login_url, bob) as client:  # another process, another user: bob's settings are untouched
        assert client.get("/api/v1/session", headers=cookie).json()["settings"]["theme"] == "dark"


def test_the_dev_server_for_a_user_that_does_not_exist_signs_nobody_in(app_url, login_url, alice):
    with dev_client(app_url, login_url, uuid4()) as client:
        assert (
            client.get("/api/v1/session", headers={"cookie": f"__Host-tj_session={DEV_SESSION_TOKEN}"}).status_code
            == 401
        )


def test_pg_settings_stay_the_source_of_truth(db, service, alice):
    service.update_settings(alice, SettingsPatch(trading_day_timezone="America/Chicago"))
    with db.tenant_session(alice) as conn:
        assert pg_settings.get_setting(conn, "trading_day_timezone") == "America/Chicago"
