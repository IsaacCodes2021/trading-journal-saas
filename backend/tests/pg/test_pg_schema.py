"""The schema lint (doc 03 section 1, doc 04 GATE): a table that forgets row-level security fails the build.

Reads the catalog of the real, migrated database, so it checks what Postgres will enforce and not what the SQL file
says it meant.
"""

from __future__ import annotations

import pytest
from test_pg_tenancy import SYSTEM_TABLES, TENANT_TABLES

OWNER_ROLE, APP_ROLE = "tj_owner", "app_rw"


def tenant_tables_in_catalog(conn) -> set[str]:
    rows = conn.execute(
        """SELECT c.relname FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
           JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'user_id' AND NOT a.attisdropped
           WHERE c.relkind = 'r'"""
    )
    return {r["relname"] for r in rows}


def test_the_list_the_tenant_tests_use_matches_the_catalog(owner):
    # If you add a table with a user_id, add it to TENANT_TABLES in test_pg_tenancy.py so it is isolation-tested.
    assert tenant_tables_in_catalog(owner) - set(SYSTEM_TABLES) == set(TENANT_TABLES)
    assert set(SYSTEM_TABLES) <= tenant_tables_in_catalog(owner)  # and the system list names real tables


def test_the_request_role_is_neither_superuser_nor_able_to_bypass_rls(owner):
    for role in (APP_ROLE, OWNER_ROLE):
        row = owner.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        assert (row["rolsuper"], row["rolbypassrls"]) == (False, False), role


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_row_level_security_is_enabled_forced_and_has_a_tenant_policy(owner, table):
    row = owner.execute(
        "SELECT relrowsecurity AS enabled, relforcerowsecurity AS forced FROM pg_class WHERE oid = %s::regclass",
        (table,),
    ).fetchone()
    assert (row["enabled"], row["forced"]) == (True, True)
    policies = owner.execute(
        "SELECT polname, polcmd, pg_get_expr(polqual, polrelid) AS using_expr, "
        "       pg_get_expr(polwithcheck, polrelid) AS check_expr, "
        "       ARRAY(SELECT rolname FROM pg_roles WHERE oid = ANY(polroles)) AS roles "
        "FROM pg_policy WHERE polrelid = %s::regclass ORDER BY polname",
        (table,),
    ).fetchall()
    [tenant] = [p for p in policies if p["polname"] == "tenant"]
    assert "app_user_id()" in tenant["using_expr"] and "app_user_id()" in tenant["check_expr"]
    # Policies combine with OR, so an extra one widens access. The only one allowed is the operator's read of the
    # usage counters: SELECT only, and for app_admin alone.
    others = [p for p in policies if p["polname"] != "tenant"]
    assert [(p["polname"], p["polcmd"], p["roles"]) for p in others] == (
        [("admin_read", "r", ["app_admin"])] if table == "user_usage" else []
    )


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_an_index_starts_with_user_id(owner, table):
    """The policy predicate must be an index prefix, or every query scans the whole table."""
    leading = owner.execute(
        """SELECT a.attname FROM pg_index i
           JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]
           WHERE i.indrelid = %s::regclass AND i.indisvalid""",
        (table,),
    ).fetchall()
    assert "user_id" in {r["attname"] for r in leading}


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_user_id_is_not_null_and_defaults_to_the_tenant(owner, table):
    row = owner.execute(
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = 'user_id'",
        (table,),
    ).fetchone()
    assert row["is_nullable"] == "NO"
    assert "app_user_id()" in row["column_default"]


def test_foreign_keys_between_tenant_tables_are_composite_on_user_id(owner):
    rows = owner.execute(
        """SELECT con.conname, con.conrelid::regclass::text AS child, con.confrelid::regclass::text AS parent,
                  (SELECT attname FROM pg_attribute WHERE attrelid = con.conrelid AND attnum = con.conkey[1]) AS child_first,
                  (SELECT attname FROM pg_attribute WHERE attrelid = con.confrelid AND attnum = con.confkey[1]) AS parent_first
           FROM pg_constraint con
           JOIN pg_namespace n ON n.oid = con.connamespace AND n.nspname = 'public'
           WHERE con.contype = 'f'"""
    ).fetchall()
    tenant = set(TENANT_TABLES)
    between_tenant_tables = [r for r in rows if r["child"] in tenant and r["parent"] in tenant]
    assert len(between_tenant_tables) >= 10  # the lint is not passing because it found nothing
    for fk in between_tenant_tables:
        assert (fk["child_first"], fk["parent_first"]) == ("user_id", "user_id"), fk["conname"]


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_the_request_role_has_only_the_four_basic_privileges(owner, table):
    for privilege, expected in (
        ("SELECT", True), ("INSERT", True), ("UPDATE", True), ("DELETE", True),
        ("TRUNCATE", False), ("REFERENCES", False), ("TRIGGER", False),
    ):  # fmt: skip
        got = owner.execute("SELECT has_table_privilege(%s, %s, %s) AS ok", (APP_ROLE, table, privilege)).fetchone()[
            "ok"
        ]
        assert got is expected, (table, privilege)


@pytest.mark.parametrize("table", ["users", "schema_migrations"])
def test_the_request_role_has_no_access_to_account_or_bookkeeping_tables(owner, table):
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert not owner.execute(
            "SELECT has_table_privilege(%s, %s, %s) AS ok", (APP_ROLE, table, privilege)
        ).fetchone()["ok"]


def test_ids_are_generated_always(owner):
    rows = owner.execute(
        "SELECT table_name, is_identity, identity_generation FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'id' AND table_name <> 'users'"
    ).fetchall()
    assert rows and all(r["is_identity"] == "YES" and r["identity_generation"] == "ALWAYS" for r in rows)
