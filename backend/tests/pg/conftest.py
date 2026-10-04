"""Fixtures for the Postgres tests (spike S2).

They need a real Postgres 18 with the two roles from ``tradejournal.pg.provision``. Connection URLs come from the
environment (CI) or from ``backend/.env.test`` (written by the provisioning script). Without them every test in this
folder is skipped, so the rest of the suite still runs anywhere.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from auth_world import world  # noqa: F401  (registers the sign-in `world` fixture for the pg tests)
from psycopg.rows import dict_row

from tradejournal.pg import accounts
from tradejournal.pg import migrate as pg_migrate
from tradejournal.pg.defaults import provision_defaults
from tradejournal.pg.provision import read_env_file
from tradejournal.pg.session import Database
from tradejournal.pg.system import PROFILES, SystemDatabase

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.test"


@pytest.fixture(scope="session")
def pg_urls() -> tuple[str, str]:
    env = {**read_env_file(ENV_FILE), **os.environ}
    owner, app = env.get("TJ_TEST_OWNER_URL"), env.get("TJ_TEST_APP_URL")
    if not owner or not app:
        if env.get("TJ_REQUIRE_POSTGRES"):  # CI sets this: a missing database must fail the build, never skip it
            pytest.fail("TJ_REQUIRE_POSTGRES is set but TJ_TEST_OWNER_URL and TJ_TEST_APP_URL are not")
        pytest.skip(
            "no Postgres: set TJ_TEST_OWNER_URL and TJ_TEST_APP_URL, or run python -m tradejournal.pg.provision"
        )
    return owner, app


@pytest.fixture(scope="session")
def owner_url(pg_urls) -> str:
    """The test database with a fresh schema: dropped and rebuilt from the migrations once per run."""
    owner, _ = pg_urls
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    pg_migrate.migrate(owner)
    return owner


@pytest.fixture(scope="session")
def app_url(pg_urls) -> str:
    return pg_urls[1]


@pytest.fixture(scope="session")
def db(owner_url, app_url):
    """The request-serving pool: connects as app_rw, so row-level security applies."""
    database = Database(app_url, max_size=4)
    database.open()
    yield database
    database.close()


@pytest.fixture(scope="session")
def system(owner_url):
    """The four system_session pools, each connecting as its own narrow role."""
    env = {**read_env_file(ENV_FILE), **os.environ}
    urls = {profile: env.get(f"TJ_TEST_{profile.upper()}_URL") for profile in PROFILES}
    if not all(urls.values()):
        if env.get("TJ_REQUIRE_POSTGRES"):
            pytest.fail("TJ_REQUIRE_POSTGRES is set but the TJ_TEST_<PROFILE>_URL system role URLs are not")
        pytest.skip("no system role URLs: run python -m tradejournal.pg.provision")
    database = SystemDatabase(urls, max_size=2)
    database.open()
    yield database
    database.close()


@pytest.fixture(scope="session")
def login_url(owner_url) -> str:
    """The address the sign-in role (app_login) connects with."""
    env = {**read_env_file(ENV_FILE), **os.environ}
    return env["TJ_TEST_LOGIN_URL"]


@pytest.fixture(scope="session")
def sweep_url(owner_url) -> str:
    """The address the sweep role (app_sweep) connects with."""
    env = {**read_env_file(ENV_FILE), **os.environ}
    return env["TJ_TEST_SWEEP_URL"]


@pytest.fixture(scope="session")
def admin_url(owner_url) -> str:
    """The address the operator's role (app_admin) connects with."""
    env = {**read_env_file(ENV_FILE), **os.environ}
    return env["TJ_TEST_ADMIN_URL"]


@pytest.fixture
def owner(owner_url):
    """An owner connection (it can create users and read everything), on an emptied database."""
    with psycopg.connect(owner_url, autocommit=True, row_factory=dict_row) as conn:
        # Cascades through every tenant table; the system tables that hang off no user (invites) are named, so that
        # no test sees another's leftovers.
        conn.execute("TRUNCATE users, invites, audit_log, identity_removals, grant_codes RESTART IDENTITY CASCADE")
        yield conn


def make_user(owner, db, email: str):
    row = owner.execute(
        "INSERT INTO users (idp_subject, email) VALUES (%s, %s) RETURNING id", (f"idp|{email}", email)
    ).fetchone()
    with db.tenant_session(row["id"]) as conn:
        provision_defaults(conn)
    return row["id"]


@pytest.fixture
def alice(owner, db):
    return make_user(owner, db, "alice@example.test")


@pytest.fixture
def bob(owner, db):
    return make_user(owner, db, "bob@example.test")


@pytest.fixture
def conn(db, alice):
    """Alice's tenant session, as the SQLite tests' `conn` fixture was the one journal."""
    with db.tenant_session(alice) as connection:
        yield connection


@pytest.fixture
def account(conn):
    """An Apex account charging $4.00 per contract round trip on every symbol (the SQLite tests' `account`)."""
    created = accounts.create_account(
        conn, "Apex 07", firm="Apex", account_size=Decimal("50000"), starting_balance=Decimal("50000")
    )
    accounts.set_fee(conn, created.id, "*", Decimal("4.00"))
    return created
