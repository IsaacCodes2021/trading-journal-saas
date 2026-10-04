"""The data export (requirement F-A5) against a real Postgres: everything of yours, exactly, and nothing of anyone else's."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import SAMPLE, fixture_bytes
from pg_helpers import make_account, make_trades
from test_pg_tenancy import PNG, TENANT_TABLES

from tradejournal.api.sanitize import sanitize_notes
from tradejournal.ledger import KINDS
from tradejournal.pg import accounts, importer, journal, ledger
from tradejournal.pg import export as pg_export

WHEN = datetime(2026, 10, 4, 15, 30, 5, tzinfo=UTC)
TRICKY_ACCOUNT = 'Zoë "Z", Inc.\nline two'  # a comma, quotes, a line break and a non-ASCII letter in one name
TRICKY_NOTE = 'paid "in full", twice\nsecond line'
BOB_SECRETS = ("Bob Secret Account", "bob-secret-note", "bob-secret-day", "bob@example.test")


def build(db, user, **kwargs):
    """Export `user`'s data the way the route does and return the ZIP's files by name, plus the raw bytes."""
    with db.tenant_session(user, snapshot=True) as conn:
        result = pg_export.build(
            conn,
            profile={"account": {"email": "alice@example.test"}, "settings": {"theme": "dark"}},
            exported_at=kwargs.pop("exported_at", WHEN),
            app_version="test-1",
            clean_html=kwargs.pop("clean_html", sanitize_notes),
        )
    with result.file as handle:
        raw = handle.read()
    assert len(raw) == result.size
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}, raw, result


def rows_of(files: dict[str, bytes], name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(files[name].decode("utf-8"), newline="")))


@pytest.fixture
def stored(db, alice, bob, tmp_path):
    """Alice with something in every table; Bob with enough that leaking any of it would show."""
    with db.tenant_session(alice) as conn:
        account = make_account(conn, TRICKY_ACCOUNT)
        make_account(conn, "Second account", firm="Lucid", account_type="funded")
        accounts.set_fee(conn, account.id, "NQ", Decimal("4.0000"))
        importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
        trade_ids = [r["id"] for r in conn.execute("SELECT id FROM trades ORDER BY id")]
        group = journal.tag_groups(conn)[0]
        tag = journal.get_or_create_tag(conn, group.id, "A+ setup")
        journal.add_trade_tag(conn, trade_ids[0], tag.id)
        journal.set_notes(conn, trade_ids[0], "<p>Good <b>entry</b> &amp; exit</p><ul><li>one</li><li>two</li></ul>")
        conn.execute(
            "INSERT INTO day_notes (trade_date, notes) VALUES (%s, %s)", (date(2024, 1, 5), "<p>Choppy day</p>")
        )
        ledger.add_entry(conn, account.id, next(iter(KINDS)), Decimal("12.50"), date(2024, 1, 6), TRICKY_NOTE)
        journal.add_screenshot(conn, tmp_path, trade_ids[1], "shot one.png", PNG)
    with db.tenant_session(bob) as conn:
        theirs = make_account(conn, "Bob Secret Account")
        bobs_trade = make_trades(conn, theirs.id, 3)[0]
        journal.set_notes(conn, bobs_trade, "<p>bob-secret-note</p>")
        conn.execute(
            "INSERT INTO day_notes (trade_date, notes) VALUES (%s, %s)", (date(2024, 1, 5), "<p>bob-secret-day</p>")
        )
    return account.id, trade_ids


def count(db, user, table):
    with db.tenant_session(user) as conn:
        return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608 - a literal from this file


# ---- what is covered -----------------------------------------------------------------------------------------------


def test_every_table_that_holds_a_persons_rows_is_exported_or_has_a_reason_it_is_not():
    # TENANT_TABLES is pinned to the catalog by the schema lint, so a new table fails here until someone decides.
    assert set(pg_export.COVERAGE) | set(pg_export.NOT_EXPORTED) == set(TENANT_TABLES)
    assert not set(pg_export.COVERAGE) & set(pg_export.NOT_EXPORTED)
    assert all(pg_export.NOT_EXPORTED.values())  # each exclusion has its reason written down


def test_every_exported_table_has_every_column_except_the_ones_left_out_on_purpose(db, alice, stored, owner):
    files, _, _ = build(db, alice)
    for table in pg_export.TABLES:
        in_catalog = [
            r["column_name"]
            for r in owner.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s "
                "ORDER BY ordinal_position",
                (table.table,),
            )
        ]
        header = next(csv.reader(io.StringIO(files[table.file].decode())))
        assert header == [c for c in in_catalog if c not in table.leave_out], table.table
        assert "user_id" in table.leave_out  # the person's own id is in profile.json, not repeated on every row


def test_the_columns_left_out_are_only_internal_or_found_elsewhere_in_the_file(db, alice, stored):
    files, _, _ = build(db, alice)
    assert "notes" not in rows_of(files, "trades.csv")[0] and any(n.startswith("notes/trade-") for n in files)
    assert (
        "stored_name" not in rows_of(files, "screenshots.csv")[0]
        and "original_name" in rows_of(files, "screenshots.csv")[0]
    )


# ---- completeness and exactness ------------------------------------------------------------------------------------


def test_every_row_of_every_table_is_in_the_file_and_the_counts_say_so(db, alice, stored):
    files, _, result = build(db, alice)
    for table in pg_export.TABLES:
        assert len(rows_of(files, table.file)) == count(db, alice, table.table), table.table
        assert result.counts[table.file] == count(db, alice, table.table)
    assert result.counts["trades.csv"] == len(stored[1]) and result.counts["trades.csv"] > 0
    assert (
        count(db, alice, "fill_pairs") > 0 and count(db, alice, "import_fill_pairs") > 0
    )  # the fixture is not vacuous


def test_money_prices_times_and_dates_are_exact_text(db, alice, stored):
    files, _, _ = build(db, alice)
    with db.tenant_session(alice) as conn:
        trade = conn.execute("SELECT * FROM trades ORDER BY id LIMIT 1").fetchone()
        pair = conn.execute("SELECT * FROM fill_pairs ORDER BY id LIMIT 1").fetchone()
        fee = conn.execute("SELECT * FROM fee_schedules LIMIT 1").fetchone()
    row = rows_of(files, "trades.csv")[0]
    assert row["id"] == str(trade["id"])
    for column in ("gross_pnl", "fees", "net_pnl", "avg_entry", "avg_exit", "points", "ticks", "tick_size"):
        assert row[column] == format(trade[column], "f"), column  # decimal text, scale as stored, never 1E+2 or a float
    assert row["entry_at"] == trade["entry_at"].astimezone(UTC).isoformat().replace("+00:00", "Z")
    assert row["entry_at"].endswith("Z") and row["trade_date"] == trade["trade_date"].isoformat()
    pair_row = rows_of(files, "fill_pairs.csv")[0]
    assert (pair_row["buy_price"], pair_row["pnl"]) == (format(pair["buy_price"], "f"), format(pair["pnl"], "f"))
    ledger_row = rows_of(files, "ledger.csv")[0]
    assert ledger_row["amount"] == "12.50" and ledger_row["entry_date"] == "2024-01-06"
    assert (
        rows_of(files, "fee_schedules.csv")[0]["fee_per_contract_rt"]
        == format(fee["fee_per_contract_rt"], "f")
        == "4.000000"
    )


def test_commas_quotes_line_breaks_and_non_ascii_text_come_back_unchanged(db, alice, stored):
    files, _, _ = build(db, alice)
    assert TRICKY_ACCOUNT in {r["name"] for r in rows_of(files, "accounts.csv")}
    assert rows_of(files, "ledger.csv")[0]["note"] == TRICKY_NOTE
    assert "Zoë" in files["accounts.csv"].decode("utf-8")  # real UTF-8, not escapes


def test_an_empty_cell_is_a_null_or_an_empty_text(db, alice, stored):
    files, _, _ = build(db, alice)
    second = next(r for r in rows_of(files, "accounts.csv") if r["name"] == "Second account")
    assert second["outcome"] == "" and second["ended_on"] == "" and second["max_drawdown"] == ""


# ---- only this person's data -------------------------------------------------------------------------------------------


def test_nothing_of_anyone_elses_is_in_the_file(db, alice, bob, stored):
    files, raw, _ = build(db, alice)
    blob = b"\n".join(files.values()).decode("utf-8", "replace")
    for secret in BOB_SECRETS:
        assert secret not in blob, secret
    assert len(rows_of(files, "accounts.csv")) == 2 and count(db, bob, "accounts") == 1


def test_each_person_gets_their_own_and_a_person_with_no_data_gets_an_empty_but_complete_file(db, alice, bob, stored):
    bobs, _, _ = build(db, bob)
    assert [r["name"] for r in rows_of(bobs, "accounts.csv")] == ["Bob Secret Account"]
    assert TRICKY_ACCOUNT not in bobs["accounts.csv"].decode()
    with db.tenant_session(bob) as conn:
        conn.execute("DELETE FROM accounts")
        conn.execute("DELETE FROM day_notes")
    nothing, _, result = build(db, bob)
    assert rows_of(nothing, "accounts.csv") == [] and nothing["accounts.csv"].startswith(b"id,")
    assert set(nothing) >= {"profile.json", "manifest.json", *(t.file for t in pg_export.TABLES)}
    assert result.counts["notes"] == 0 and not [n for n in nothing if n.startswith("notes/")]


# ---- notes -------------------------------------------------------------------------------------------------------------


def test_each_note_is_a_small_html_file_and_a_text_file_and_empty_notes_make_none(db, alice, stored):
    _, trade_ids = stored
    files, _, result = build(db, alice)
    names = sorted(n for n in files if n.startswith("notes/"))
    assert names == [
        "notes/day-2024-01-05.html",
        "notes/day-2024-01-05.txt",
        f"notes/trade-{trade_ids[0]}.html",
        f"notes/trade-{trade_ids[0]}.txt",
    ]
    assert result.counts["notes"] == 2
    html = files[f"notes/trade-{trade_ids[0]}.html"].decode()
    assert "<b>entry</b>" in html and "<li>two</li>" in html and "<title>Trade" in html
    assert files[f"notes/trade-{trade_ids[0]}.txt"].decode() == "Good entry & exit\n- one\n- two\n"
    assert files["notes/day-2024-01-05.txt"].decode() == "Choppy day\n"


def test_a_note_file_carries_a_policy_that_forbids_scripts_and_is_cleaned_again_on_the_way_out(db, alice, stored):
    _, trade_ids = stored
    with db.tenant_session(alice) as conn:  # an old row, from before notes were sanitized on write
        conn.execute(
            "UPDATE trades SET notes = %s WHERE id = %s",
            (
                '<p>kept</p><script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:alert(1)">x</a>',
                trade_ids[2],
            ),
        )
    files, _, _ = build(db, alice)
    html = files[f"notes/trade-{trade_ids[2]}.html"].decode()
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    for forbidden in ("<script", "onerror", "javascript:", "alert"):
        assert forbidden not in html
    assert files[f"notes/trade-{trade_ids[2]}.txt"].decode() == "kept\nx\n"


# ---- the manifest ---------------------------------------------------------------------------------------------------------


def test_the_manifest_lists_every_file_once_with_its_size_hash_and_row_count(db, alice, stored):
    files, _, _ = build(db, alice)
    manifest = json.loads(files["manifest.json"])
    assert manifest["format"] == "roundturn-export" and manifest["schema_version"] == 1
    assert manifest["exported_at"] == "2026-10-04T15:30:05Z" and manifest["app_version"] == "test-1"
    assert set(manifest["files"]) == set(files) - {"manifest.json"}  # every file but itself
    for name, described in manifest["files"].items():
        assert described["bytes"] == len(files[name]), name
        assert described["sha256"] == hashlib.sha256(files[name]).hexdigest(), name
    for table in pg_export.TABLES:
        assert manifest["files"][table.file]["rows"] == len(rows_of(files, table.file))
    assert manifest["how_to_read"]["not_included"] == ["user_usage"]


def test_the_profile_says_whose_account_it_is(db, alice, stored):
    files, _, _ = build(db, alice)
    assert json.loads(files["profile.json"]) == {
        "account": {"email": "alice@example.test"},
        "settings": {"theme": "dark"},
    }


# ---- the file itself ---------------------------------------------------------------------------------------------------------


def test_the_zip_is_valid_has_only_plain_names_and_is_the_same_bytes_every_time(db, alice, stored):
    files, raw, _ = build(db, alice)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.testzip() is None
        for info in archive.infolist():
            assert not info.filename.startswith(("/", "\\")) and ".." not in info.filename and "\\" not in info.filename
            assert info.date_time == (2026, 10, 4, 15, 30, 4)  # the export moment, to ZIP's two-second resolution
            assert info.compress_type == zipfile.ZIP_DEFLATED
    again = build(db, alice)[1]
    assert again == raw  # same data and same moment: byte for byte the same file
    assert build(db, alice, exported_at=datetime(2026, 10, 5, tzinfo=UTC))[1] != raw
    assert len(files) == len(set(files))


def test_a_large_journal_is_read_in_batches_and_ends_up_complete(db, alice, stored, monkeypatch):
    account_id, _ = stored
    with db.tenant_session(alice) as conn:  # 4,000 more trades, from one statement
        conn.execute(
            """INSERT INTO trades (account_id, symbol, symbol_root, direction, entry_at, exit_at, trade_date, hold_seconds,
                   total_qty, max_position, fill_pair_count, tick_size, avg_entry, avg_exit, points, ticks, gross_pnl,
                   fees, net_pnl)
               SELECT %s, 'NQH4', 'NQ', 'long', now(), now(), date '2024-02-01', 1, 1, 1, 1, 0.25, 1, 2, 1, 4, 20, 4, 16
               FROM generate_series(1, 4000)""",
            (account_id,),
        )
    monkeypatch.setattr(pg_export, "BATCH", 300)  # many round trips to the cursor, not one
    monkeypatch.setattr(pg_export, "SPOOL_BYTES", 1024)  # and the file is on disk, not in memory
    files, _, result = build(db, alice)
    assert result.counts["trades.csv"] == count(db, alice, "trades") >= 4000
    assert len(rows_of(files, "trades.csv")) == result.counts["trades.csv"]
    assert len({r["id"] for r in rows_of(files, "trades.csv")}) == result.counts["trades.csv"]  # no row twice or lost


def test_all_of_it_is_read_as_of_one_moment_and_nothing_can_be_written(db, alice):
    with db.tenant_session(alice, snapshot=True) as conn:
        assert conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] == "repeatable read"
        assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("INSERT INTO tag_groups (name) VALUES ('nope')")
    with db.tenant_session(alice) as conn:  # an ordinary session is unchanged, and the pooled connection was reset
        assert conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] == "read committed"
        assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "off"


def test_a_snapshot_still_sees_only_its_own_tenant(db, alice, bob, stored):
    with db.tenant_session(alice, snapshot=True) as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM accounts")}
    assert "Bob Secret Account" not in names and TRICKY_ACCOUNT in names


def test_a_failed_export_leaves_nothing_behind(db, alice, stored):
    def broken(_html: str) -> str:
        raise RuntimeError("the cleaner failed")

    with pytest.raises(RuntimeError, match="the cleaner failed"):
        build(db, alice, clean_html=broken)
    assert build(db, alice)[2].counts["trades.csv"] > 0  # and the next one is fine
