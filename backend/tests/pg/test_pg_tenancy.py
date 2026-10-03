"""Two tenants: what user B can see, change and break of user A's data (doc 03 section 1, doc 04 GATE).

The three layers are tested separately so that each is shown to hold on its own:
row-level security, composite foreign keys, and the NOT NULL default on user_id.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from pg_helpers import make_account, make_trades

from tradejournal.pg import accounts, journal
from tradejournal.pg.session import Database

# Every table that carries a user_id. test_pg_schema checks this list against the catalog, so a new table that is
# missing here fails the build instead of quietly escaping these tests.
TENANT_TABLES = [
    "user_settings", "accounts", "fee_schedules", "imports", "trades", "fill_pairs", "import_fill_pairs",
    "tag_groups", "tags", "trade_tags", "day_notes", "screenshots", "account_ledger",
]  # fmt: skip
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def count(conn, table: str) -> int:
    return conn.execute(f'SELECT count(*) AS n FROM "{table}"').fetchone()["n"]  # noqa: S608 (fixed table names)


@pytest.fixture
def data(db, alice, tmp_path):
    """Alice has at least one row in every tenant table."""
    with db.tenant_session(alice) as conn:
        account = make_account(conn)
        accounts.set_fee(conn, account.id, "*", Decimal("4"))
        [trade_id, other_trade] = make_trades(conn, account.id, 2)
        setup = next(g for g in journal.tag_groups(conn) if g.name == "Setup")
        [tag] = journal.set_trade_tags(conn, trade_id, setup.id, ["scalp"])
        journal.set_notes(conn, trade_id, "<p>private</p>")
        shot = journal.add_screenshot(conn, tmp_path, trade_id, "a.png", PNG)
        import_id = conn.execute(
            "INSERT INTO imports (account_id, filename, file_sha256, source_timezone) "
            "VALUES (%s, 'a.csv', 'abc', 'America/Los_Angeles') RETURNING id",
            (account.id,),
        ).fetchone()["id"]
        pair_id = conn.execute(
            """INSERT INTO fill_pairs (account_id, trade_id, buy_ref, sell_ref, symbol, tick_size, price_format,
                   price_format_type, qty, buy_price, sell_price, pnl, bought_at, sold_at, duration_raw,
                   duration_seconds, direction)
               VALUES (%s, %s, '1', '2', 'NQH4', 0.25, '-2', '0', 1, 1, 2, 20, now(), now(), '1sec', 1, 'long')
               RETURNING id""",
            (account.id, trade_id),
        ).fetchone()["id"]
        conn.execute("INSERT INTO import_fill_pairs (import_id, fill_pair_id) VALUES (%s, %s)", (import_id, pair_id))
        conn.execute("INSERT INTO day_notes (trade_date, notes) VALUES ('2024-01-05', 'a note')")
        conn.execute(
            "INSERT INTO account_ledger (account_id, kind, amount, entry_date) VALUES (%s, 'cost', 99, '2024-01-01')",
            (account.id,),
        )
    return {
        "account": account,
        "trade": trade_id,
        "other_trade": other_trade,
        "tag": tag,
        "shot": shot,
        "pair": pair_id,
    }


# ---- layer 1: row-level security -------------------------------------------------------------------------------


# What every new user starts with (tradejournal.pg.defaults): one settings row and three categories. Bob has his
# own, and must see exactly those and none of Alice's.
BOBS_OWN_ROWS = {"user_settings": 1, "tag_groups": 3}


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_each_user_sees_only_their_own_rows(db, alice, bob, data, table):
    with db.tenant_session(alice) as a:
        assert count(a, table) >= 1  # the fixture really put a row there
    with db.tenant_session(bob) as b:
        assert count(b, table) == BOBS_OWN_ROWS.get(table, 0)
        assert b.execute(f'SELECT count(*) AS n FROM "{table}" WHERE user_id = %s', (alice,)).fetchone()["n"] == 0  # noqa: S608


def test_bob_cannot_read_alices_data_through_the_domain_functions(db, bob, data):
    with db.tenant_session(bob) as b:
        with pytest.raises(LookupError):
            accounts.get_account(b, data["account"].id)
        assert accounts.find_account(b, data["account"].name) is None
        assert accounts.list_accounts(b) == []
        assert accounts.fee_schedule(b, data["account"].id) == {}
        assert accounts.last_trading_day(b, data["account"].id) is None
        with pytest.raises(LookupError):
            journal.notes(b, data["trade"])
        assert journal.screenshots(b, data["trade"]) == []
        assert journal.tags(b) == [] and journal.journal_summary(b) == {} and journal.tags_by_trade(b) == {}
        # Bob has his own default categories, and none of Alice's tags.
        assert [g.name for g in journal.tag_groups(b)] == ["Setup", "Mistake", "Emotion"]


def test_bob_cannot_change_or_delete_alices_rows_even_by_guessing_ids(db, alice, bob, data, tmp_path):
    account_id, trade_id = data["account"].id, data["trade"]
    with db.tenant_session(bob) as b:
        assert b.execute("UPDATE accounts SET name = 'pwned' WHERE id = %s", (account_id,)).rowcount == 0
        assert b.execute("DELETE FROM accounts WHERE id = %s", (account_id,)).rowcount == 0
        assert b.execute("UPDATE trades SET notes = 'pwned' WHERE id = %s", (trade_id,)).rowcount == 0
        assert b.execute("DELETE FROM tags WHERE id = %s", (data["tag"].id,)).rowcount == 0
        assert journal.delete_screenshot(b, data["shot"].id) is None
        with pytest.raises(LookupError):
            accounts.archive_account(b, account_id, outcome="failed", ended_on=__import__("datetime").date(2024, 1, 1))
        with pytest.raises(LookupError):
            journal.add_screenshot(b, tmp_path, trade_id, "evil.png", PNG)
    with db.tenant_session(alice) as a:
        assert accounts.get_account(a, account_id).name == "Apex 07"
        assert journal.notes(a, trade_id) == "<p>private</p>"
        assert len(journal.screenshots(a, trade_id)) == 1
        assert [t.name for t in journal.tags(a)] == ["scalp"]


def test_bob_cannot_write_rows_that_belong_to_alice(db, alice, bob):
    with db.tenant_session(bob) as b:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), b.transaction():
            b.execute("INSERT INTO accounts (user_id, name) VALUES (%s, 'planted')", (alice,))
        mine = accounts.create_account(b, "Mine")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), b.transaction():
            b.execute("UPDATE accounts SET user_id = %s WHERE id = %s", (alice, mine.id))  # try to give a row away


def test_without_a_tenant_there_are_no_rows_and_no_inserts(app_url, data):
    with psycopg.connect(app_url) as raw:  # app_rw, but nobody said who the user is
        assert raw.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
        assert raw.execute("SELECT app_user_id()").fetchone()[0] is None
        with pytest.raises((psycopg.errors.NotNullViolation, psycopg.errors.InsufficientPrivilege)):
            raw.execute("INSERT INTO accounts (name) VALUES ('orphan')")


def test_the_tenant_does_not_leak_to_the_next_user_of_a_pooled_connection(app_url, alice, data):
    pool = Database(app_url, min_size=1, max_size=1)  # one connection, so the "next request" gets the same one
    pool.open()
    try:
        with pool.tenant_session(alice) as a:
            pid = a.info.backend_pid
            assert count(a, "accounts") == 1
        with pool._pool.connection() as same:
            assert same.info.backend_pid == pid  # proof that it is the very same connection
            assert same.execute("SELECT app_user_id() AS me").fetchone()["me"] is None
            assert count(same, "accounts") == 0
    finally:
        pool.close()


def test_a_failed_block_rolls_back(db, alice):
    with pytest.raises(RuntimeError), db.tenant_session(alice) as conn:
        accounts.create_account(conn, "Doomed")
        raise RuntimeError("boom")
    with db.tenant_session(alice) as conn:
        assert accounts.list_accounts(conn) == []


def test_a_tenant_session_needs_a_real_uuid(db):
    with pytest.raises(TypeError), db.tenant_session("not-a-uuid"):
        pass


def test_the_request_role_cannot_read_users_or_the_migration_table(app_url, data):
    with psycopg.connect(app_url) as raw:
        for table in ("users", "schema_migrations"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), raw.transaction():
                raw.execute(f"SELECT 1 FROM {table}")  # noqa: S608


def test_connections_come_back_in_utc(app_url):
    with psycopg.connect(app_url) as raw:  # no startup options: the database default alone must do it
        assert raw.execute("SHOW timezone").fetchone()[0] == "UTC"


# ---- layer 2: composite foreign keys ---------------------------------------------------------------------------


def test_a_row_cannot_point_at_another_users_parent(db, bob, data):
    with db.tenant_session(bob) as b:
        with pytest.raises(psycopg.errors.ForeignKeyViolation), b.transaction():
            accounts.set_fee(b, data["account"].id, "*", Decimal("1"))  # Alice's account id
        with pytest.raises(psycopg.errors.ForeignKeyViolation), b.transaction():
            journal.add_trade_tag(b, data["trade"], data["tag"].id)  # Alice's trade and tag


def test_the_foreign_keys_hold_even_with_row_level_security_switched_off(owner, alice, bob, data):
    """Layer 2 on its own: with the policy gone, a cross-tenant reference is still refused by the schema."""
    owner.execute("BEGIN")
    try:
        for table in ("fee_schedules", "accounts"):
            owner.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')  # noqa: S608
            owner.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')  # noqa: S608
        with pytest.raises(psycopg.errors.ForeignKeyViolation), owner.transaction():
            owner.execute(
                "INSERT INTO fee_schedules (user_id, account_id, symbol_root, fee_per_contract_rt) VALUES (%s, %s, '*', 1)",
                (bob, data["account"].id),
            )
        assert count(owner, "accounts") == 1  # RLS really is off in here: the owner sees Alice's account
    finally:
        owner.execute("ROLLBACK")


# ---- layer 3: NOT NULL default ---------------------------------------------------------------------------------


def test_every_insert_inherits_the_tenant_without_naming_it(db, alice):
    with db.tenant_session(alice) as conn:
        account = make_account(conn)
        assert conn.execute("SELECT user_id FROM accounts WHERE id = %s", (account.id,)).fetchone()["user_id"] == alice


# ---- how rows go away ------------------------------------------------------------------------------------------


def test_deleting_a_trade_unlinks_its_fill_pairs_but_keeps_the_owner(db, alice, data):
    """ON DELETE SET NULL (trade_id): a plain SET NULL would also null user_id, which is NOT NULL."""
    with db.tenant_session(alice) as conn:
        conn.execute("DELETE FROM trades WHERE id = %s", (data["trade"],))
        row = conn.execute("SELECT user_id, trade_id FROM fill_pairs WHERE id = %s", (data["pair"],)).fetchone()
    assert (row["user_id"], row["trade_id"]) == (alice, None)


def test_deleting_a_user_removes_everything_they_owned_and_nothing_else(db, owner, alice, bob, data):
    with db.tenant_session(bob) as b:
        make_account(b, "Bob's account")
    owner.execute("DELETE FROM users WHERE id = %s", (alice,))
    with db.tenant_session(alice) as a:
        assert all(count(a, table) == 0 for table in TENANT_TABLES)
    with db.tenant_session(bob) as b:
        assert count(b, "accounts") == 1
        assert count(b, "tag_groups") == 3


def test_ids_are_not_reused_across_users(db, alice, bob):
    with db.tenant_session(alice) as a, db.tenant_session(bob) as b:
        first, second = accounts.create_account(a, "A").id, accounts.create_account(b, "B").id
    assert first != second  # one sequence for everyone; the ids carry no meaning across tenants


def test_a_random_uuid_is_just_nobody(db, owner, alice, data):
    with db.tenant_session(uuid4()) as nobody:
        assert all(count(nobody, table) == 0 for table in TENANT_TABLES)
