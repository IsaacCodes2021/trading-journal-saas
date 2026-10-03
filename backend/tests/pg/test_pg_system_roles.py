"""The system_session roles (doc 03 section 1, doc 04 GATE): what each can do, and above all what none of them can.

Everything is checked twice: against the catalog (what Postgres says the role may do) and by connecting as the role
and trying (what actually happens), because a grant that exists on paper but is shadowed, or a default privilege nobody
listed, only shows up in the second.
"""

from __future__ import annotations

import psycopg
import pytest
from pg_helpers import make_account, make_trades
from test_pg_schema import tenant_tables_in_catalog
from test_pg_tenancy import TENANT_TABLES

from tradejournal.pg import importer, usage
from tradejournal.pg.provision import SYSTEM_ROLES
from tradejournal.pg.system import PROFILES, SystemDatabase

ROLES = list(SYSTEM_ROLES.values())
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
COLUMN_PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "REFERENCES"}


def count(conn, table):
    return conn.execute(f'SELECT count(*) AS n FROM "{table}"').fetchone()["n"]  # noqa: S608 (fixed table names)


# ---- the roles themselves --------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_a_system_role_is_a_plain_login_role(owner, role):
    row = owner.execute(
        "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
        "FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    assert row == {
        "rolcanlogin": True,
        "rolsuper": False,
        "rolbypassrls": False,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolreplication": False,
    }


def test_every_profile_has_exactly_one_role():
    assert set(SYSTEM_ROLES) == set(PROFILES)
    assert len(set(SYSTEM_ROLES.values())) == len(PROFILES)


# ---- no system role touches a tenant table (the gate) -----------------------------------------------------------


@pytest.mark.parametrize("table", TENANT_TABLES)
@pytest.mark.parametrize("role", ROLES)
def test_no_system_role_has_any_privilege_on_a_tenant_table_in_the_catalog(owner, role, table):
    for privilege in PRIVILEGES:
        # has_any_column_privilege is true for a table-level grant and for a grant on a single column alike, but it only
        # knows the four privileges a column can carry.
        function = "has_any_column_privilege" if privilege in COLUMN_PRIVILEGES else "has_table_privilege"
        granted = owner.execute(f"SELECT {function}(%s, %s, %s) AS ok", (role, table, privilege)).fetchone()["ok"]  # noqa: S608
        if (role, table, privilege) == ("app_admin", "user_usage", "SELECT"):
            assert granted, "the operator must be able to read the usage counters"
        else:
            assert not granted, (role, table, privilege)


def test_the_catalog_check_covers_every_table_with_a_user_id(owner):
    assert tenant_tables_in_catalog(owner) == set(TENANT_TABLES)


@pytest.mark.parametrize("table", [t for t in TENANT_TABLES if t != "user_usage"])
def test_the_admin_role_cannot_read_a_tenant_table_when_it_tries(system, alice, table):
    """The doc 04 gate: connect as app_admin and fail to read any tenant table."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("admin") as conn:
        conn.execute(f'SELECT count(*) FROM "{table}"')  # noqa: S608 (fixed table names)


@pytest.mark.parametrize("profile", ["login", "webhook", "sweep"])
def test_the_other_system_roles_cannot_read_a_tenant_table_either(system, alice, profile):
    for table in TENANT_TABLES:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session(profile) as conn:
            conn.execute(f'SELECT count(*) FROM "{table}"')  # noqa: S608 (fixed table names)


def test_the_admin_role_cannot_see_what_a_tenant_wrote_even_through_the_one_table_it_can_read(system, db, alice):
    with db.tenant_session(alice) as conn:
        make_trades(conn, make_account(conn).id, 3)
        usage.refresh(conn)
    with system.system_session("admin") as conn:
        # Counters, yes. Rows, no: user_usage has no column that holds any of the user's data.
        [row] = conn.execute("SELECT * FROM user_usage WHERE user_id = %s", (alice,)).fetchall()
        assert (row["accounts_count"], row["trades_count"]) == (1, 3)
        assert set(row) == {
            "user_id", "accounts_count", "trades_count", "screenshots_count", "storage_bytes",
            "last_import_at", "updated_at",
        }  # fmt: skip


# ---- the users table: who can read and change what -------------------------------------------------------------


def test_nobody_but_the_owner_can_write_is_admin(owner):
    for role in ["app_rw", *ROLES]:
        for privilege in ("INSERT", "UPDATE"):
            assert not owner.execute(
                "SELECT has_column_privilege(%s, 'users', 'is_admin', %s) AS ok", (role, privilege)
            ).fetchone()["ok"], (role, privilege)


def test_login_can_create_and_find_a_user_and_the_new_user_is_active_and_not_an_operator(system, owner):
    with system.system_session("login") as conn:
        row = conn.execute(
            """INSERT INTO users (idp_subject, email, email_verified_at)
               VALUES ('idp|new', 'new@example.test', now())
               ON CONFLICT (idp_subject) DO UPDATE SET email = EXCLUDED.email, last_login_at = now()
               RETURNING id, status, is_admin, last_login_at"""
        ).fetchone()
        assert (row["status"], row["is_admin"], row["last_login_at"]) == ("active", False, None)
        again = conn.execute(
            """INSERT INTO users (idp_subject, email) VALUES ('idp|new', 'changed@example.test')
               ON CONFLICT (idp_subject) DO UPDATE SET email = EXCLUDED.email, last_login_at = now()
               RETURNING id, email, last_login_at"""
        ).fetchone()
        assert again["id"] == row["id"]
        assert (again["email"], again["last_login_at"] is not None) == ("changed@example.test", True)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO users (idp_subject, email, is_admin) VALUES ('idp|evil', 'evil@example.test', true)",
        "INSERT INTO users (idp_subject, email, status) VALUES ('idp|evil', 'evil@example.test', 'suspended')",
        "UPDATE users SET is_admin = true",
        "UPDATE users SET status = 'active'",
        "DELETE FROM users",
    ],
)
def test_login_cannot_make_an_operator_change_a_status_or_delete(system, alice, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("login") as conn:
        conn.execute(statement)


def test_webhook_reads_only_id_email_and_status(system, alice):
    with system.system_session("webhook") as conn:
        assert [r["email"] for r in conn.execute("SELECT id, email, status FROM users")] == ["alice@example.test"]
    for statement in (
        "SELECT idp_subject FROM users",
        "SELECT is_admin FROM users",
        "UPDATE users SET status = 'suspended'",
        "DELETE FROM users",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("webhook") as conn:
            conn.execute(statement)


def test_admin_lists_users_and_changes_only_their_status(system, alice, bob):
    with system.system_session("admin") as conn:
        emails = {r["email"] for r in conn.execute("SELECT id, email, status, is_admin, created_at FROM users")}
        assert emails == {"alice@example.test", "bob@example.test"}
        conn.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (alice,))
    with system.system_session("admin") as conn:
        assert conn.execute("SELECT status FROM users WHERE id = %s", (alice,)).fetchone()["status"] == "suspended"
        conn.execute("UPDATE users SET status = 'active' WHERE id = %s", (alice,))
    for statement in (
        "UPDATE users SET email = 'x@example.test'",
        "UPDATE users SET is_admin = true",
        "SELECT idp_subject FROM users",  # the identity provider's key is not the operator's business
        "DELETE FROM users",
        "INSERT INTO users (idp_subject, email) VALUES ('idp|x', 'x@example.test')",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("admin") as conn:
            conn.execute(statement)


def test_admin_cannot_write_the_usage_counters(system, alice):
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("admin") as conn:
        conn.execute("UPDATE user_usage SET trades_count = 0")


def test_admin_sees_every_users_counters_while_a_tenant_still_sees_only_its_own(system, db, alice, bob):
    with db.tenant_session(alice) as a:
        make_account(a)
    with db.tenant_session(bob) as b:
        make_account(b, "B1")
        make_account(b, "B2")
    with system.system_session("admin") as conn:
        counts = {
            r["user_id"]: r["accounts_count"] for r in conn.execute("SELECT user_id, accounts_count FROM user_usage")
        }
    assert counts == {alice: 1, bob: 2}
    with db.tenant_session(alice) as a:
        assert [r["user_id"] for r in a.execute("SELECT user_id FROM user_usage")] == [alice]


# ---- the sweep deletes a whole user without touching a tenant table -----------------------------------------------


def test_sweep_deletes_a_user_and_everything_they_own_and_nothing_else(system, db, owner, alice, bob):
    for user in (alice, bob):
        with db.tenant_session(user) as conn:
            account = make_account(conn)
            make_trades(conn, account.id, 3)
            conn.execute("INSERT INTO day_notes (trade_date, notes) VALUES ('2024-01-05', 'a note')")
    # Counted through tenant sessions: forced row-level security hides tenant rows from the owner role as well.
    with db.tenant_session(alice) as conn:
        assert all(
            count(conn, table) > 0 for table in ("accounts", "trades", "day_notes", "user_settings", "user_usage")
        )

    with system.system_session("sweep") as conn:
        assert conn.execute("DELETE FROM users WHERE id = %s", (alice,)).rowcount == 1

    with db.tenant_session(alice) as conn:  # the same tenant id, now with no user behind it: every table is empty
        assert {table: count(conn, table) for table in TENANT_TABLES} == dict.fromkeys(TENANT_TABLES, 0)
    with db.tenant_session(bob) as conn:
        assert (count(conn, "accounts"), count(conn, "trades"), count(conn, "day_notes")) == (1, 3, 1)
    assert [r["id"] for r in owner.execute("SELECT id FROM users")] == [bob]


def test_sweep_reads_only_id_and_status(system, alice):
    with system.system_session("sweep") as conn:
        assert [r["id"] for r in conn.execute("SELECT id, status FROM users")] == [alice]
    for statement in ("SELECT email FROM users", "UPDATE users SET status = 'suspended'"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("sweep") as conn:
            conn.execute(statement)


# ---- the request role and the system roles stay apart ---------------------------------------------------------


def test_the_tenant_role_still_cannot_reach_users_or_become_a_system_role(owner, app_url):
    for table in ("users", "schema_migrations"):
        assert not owner.execute("SELECT has_any_column_privilege('app_rw', %s, 'SELECT') AS ok", (table,)).fetchone()[
            "ok"
        ]
    with psycopg.connect(app_url) as raw:
        for role in ROLES:
            with pytest.raises(psycopg.errors.InsufficientPrivilege), raw.transaction():
                raw.execute(f"SET ROLE {role}")


def test_a_system_role_cannot_become_the_tenant_role_or_the_owner(system):
    for profile in PROFILES:
        for target in ("app_rw", "tj_owner"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session(profile) as conn:
                conn.execute(f"SET ROLE {target}")


# ---- the session mechanics -------------------------------------------------------------------------------------


def test_a_system_session_commits_on_success_and_rolls_back_on_error(system, owner):
    with system.system_session("login") as conn:
        conn.execute("INSERT INTO users (idp_subject, email) VALUES ('idp|kept', 'kept@example.test')")
    with pytest.raises(RuntimeError), system.system_session("login") as conn:
        conn.execute("INSERT INTO users (idp_subject, email) VALUES ('idp|lost', 'lost@example.test')")
        raise RuntimeError("boom")
    subjects = {r["idp_subject"] for r in owner.execute("SELECT idp_subject FROM users")}
    assert subjects == {"idp|kept"}


def test_each_profile_connects_as_its_own_role(system):
    for profile, role in SYSTEM_ROLES.items():
        with system.system_session(profile) as conn:
            assert conn.execute("SELECT current_user AS who").fetchone()["who"] == role


def test_system_sessions_are_in_utc(system):
    with system.system_session("admin") as conn:
        assert conn.execute("SHOW timezone").fetchone()["TimeZone"] == "UTC"


def test_an_unknown_profile_or_an_unconfigured_one_is_an_error(system):
    with pytest.raises(ValueError, match="unknown system profile"), system.system_session("root"):
        pass
    with pytest.raises(ValueError, match="unknown system profiles"):
        SystemDatabase({"login": "postgresql://x", "root": "postgresql://y"})
    partial = SystemDatabase({})  # no URLs at all
    with pytest.raises(LookupError, match="'admin'"), partial.system_session("admin"):
        pass


def test_importing_through_a_tenant_session_is_unaffected_by_the_system_roles(db, alice):
    """A smoke test that the new grants did not disturb the request role."""
    with db.tenant_session(alice) as conn:
        account = make_account(conn)
        assert importer.list_imports(conn, account.id) == []
