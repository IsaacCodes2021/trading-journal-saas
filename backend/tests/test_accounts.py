import sqlite3
from decimal import Decimal

import pytest

from tradejournal import accounts, db


def test_create_and_change_account_type(conn):
    with db.transaction(conn):
        account = accounts.create_account(conn, "Apex PA 01", firm="Apex", account_type="funded")
    assert account.account_type == "funded"
    assert accounts.create_account(conn, "Apex Eval 02", firm="Apex").account_type == "evaluation"

    with db.transaction(conn):
        updated = accounts.update_account(
            conn,
            account.id,
            name="Apex PA 01",
            firm="Apex",
            account_type="evaluation",
            account_size=Decimal("50000"),
            starting_balance=Decimal("50000"),
        )
    assert updated.account_type == "evaluation"


def test_unknown_account_type_is_rejected(conn):
    with pytest.raises(ValueError, match="account type"):
        accounts.create_account(conn, "Mystery", account_type="live")
    with pytest.raises(sqlite3.IntegrityError):  # the database enforces it too
        conn.execute("INSERT INTO accounts (name, firm, account_type, created_at) VALUES ('x', 'Other', 'live', 'now')")


@pytest.mark.parametrize("firm", accounts.FIRMS)
def test_every_listed_firm_can_be_used(conn, firm):
    assert accounts.create_account(conn, f"{firm} 50K", firm=firm).firm == firm


def test_unlisted_firm_is_rejected(conn):
    with pytest.raises(ValueError, match="firm"):
        accounts.create_account(conn, "Someone 50K", firm="Someone Capital")


def test_upgrading_an_existing_journal_marks_accounts_as_evaluations(tmp_path):
    # A journal created before account types existed (schema version 1).
    path = tmp_path / "journal.db"
    old = sqlite3.connect(path)
    old.executescript(f"BEGIN;\n{db.SCHEMA_V1}\nPRAGMA user_version = 1;\nCOMMIT;")
    old.execute(
        "INSERT INTO accounts (name, firm, starting_balance, created_at) "
        "VALUES ('Tradeify 50k flex eval', 'Other', '50000.00', '2026-09-30T00:00:00Z')"
    )
    old.commit()
    old.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    account = accounts.find_account(conn, "Tradeify 50k flex eval")
    assert account.account_type == "evaluation"
    assert account.starting_balance == Decimal("50000.00")
    conn.close()


def test_upgrade_to_free_firm_names_keeps_accounts_and_their_trades(tmp_path):
    """v3 rebuilds the accounts table; ids, data and every reference must survive."""
    from helpers import SAMPLE, fixture_bytes

    from tradejournal import importer

    path = tmp_path / "journal.db"
    old = sqlite3.connect(path, isolation_level=None)
    old.executescript(f"BEGIN;\n{db.SCHEMA_V1}\n{db.ACCOUNT_TYPE_V2}\nPRAGMA user_version = 2;\nCOMMIT;")
    old.execute(
        "INSERT INTO accounts (id, name, firm, account_type, starting_balance, created_at) "
        "VALUES (7, 'Tradeify 50k flex eval', 'Other', 'evaluation', '50000.00', '2026-09-30T00:00:00Z')"
    )
    old.execute("INSERT INTO fee_schedules VALUES (7, '*', '4.12')")  # a row pointing at the account
    old.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert accounts.fee_schedule(conn, 7) == {"*": Decimal("4.12")}
    importer.import_files(conn, 7, [(SAMPLE, fixture_bytes(SAMPLE))])
    with db.transaction(conn):
        moved = accounts.update_account(
            conn, 7, name="Tradeify 50k flex eval", firm="Tradeify", account_type="evaluation",
            account_size=None, starting_balance=Decimal("50000"),
        )  # fmt: skip
    assert moved.firm == "Tradeify"
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE account_id = 7").fetchone()[0] == 8
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # enforcement is back on
    with pytest.raises(sqlite3.IntegrityError):  # and still enforced
        conn.execute("INSERT INTO fee_schedules VALUES (999, '*', '1')")
    conn.close()
