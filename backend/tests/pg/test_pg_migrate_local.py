"""Migrating a local SQLite journal into a Postgres tenant, with the golden check (milestone M2).

The source is a synthetic journal built with the old SQLite code: two accounts (one archived), imported trades, tags,
notes, screenshots, day notes, ledger entries, fees and changed settings. Nothing here reads real data.
"""

import io
import json
import shutil
import sqlite3
import struct
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import SAMPLE, fixture_bytes
from pg_helpers import PNG_1X1

from tradejournal import accounts as sqlite_accounts
from tradejournal import db as sqlite_db
from tradejournal import importer as sqlite_importer
from tradejournal import journal as sqlite_journal
from tradejournal import ledger as sqlite_ledger
from tradejournal import settings as sqlite_settings
from tradejournal.api.sanitize import sanitize_notes
from tradejournal.filestore import LocalFileStore
from tradejournal.pg import export as pg_export
from tradejournal.pg import migrate_local, usage
from tradejournal.pg import screenshots as pg_screenshots
from tradejournal.pg.migrate_local import MigrationRefused

D = Decimal
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # the old app only looked at the first bytes: this one claims to be 0 by 0


def build_source(directory: Path, *, with_files: bool = True, image: bytes = PNG, both_on_first: bool = False) -> Path:
    """A journal as the single-user app would have left it. Returns the path of journal.db."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "journal.db"
    shots = directory / "screenshots"
    conn = sqlite_db.connect(path)
    with sqlite_db.transaction(conn):
        eval_account = sqlite_accounts.create_account(
            conn, "Apex 07", firm="Apex", account_size=D("50000"), starting_balance=D("50000"),
            max_drawdown=D("2500"), trailing_type="eod", trail_lock_enabled=True, trail_lock_level=D("52600"),
            daily_loss_limit=D("1000.5"), profit_target=D("3000"),
        )  # fmt: skip
        funded = sqlite_accounts.create_account(conn, "Lucid 01", firm="Lucid", account_type="funded")
        sqlite_accounts.set_fee(conn, eval_account.id, "*", D("4.00"))
        sqlite_accounts.set_fee(conn, eval_account.id, "NQ", D("5.50"))
        sqlite_accounts.set_fee(conn, funded.id, "*", D("2.1"))
        sqlite_settings.set_setting(conn, "timezone", "America/New_York")
        sqlite_settings.set_setting(conn, "trading_day_start", "9:30")  # SQLite keeps it as typed
        sqlite_settings.set_setting(conn, "theme", "light")
    sqlite_importer.import_files(conn, eval_account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
    sqlite_importer.import_files(conn, funded.id, [("r.csv", fixture_bytes("reversal_2023-11-20.csv"))])
    with sqlite_db.transaction(conn):
        trades = conn.execute("SELECT id FROM trades ORDER BY entry_at, id").fetchall()
        first, second, third = (t["id"] for t in trades[:3])
        setup = next(g for g in sqlite_journal.tag_groups(conn) if g.name == "Setup")
        mistake = next(g for g in sqlite_journal.tag_groups(conn) if g.name == "Mistake")
        custom = sqlite_journal.create_group(conn, "Market context")
        sqlite_journal.set_trade_tags(conn, first, setup.id, ["Opening drive", "Pullback"])
        sqlite_journal.set_trade_tags(conn, second, mistake.id, ["Chased"])
        sqlite_journal.set_trade_tags(conn, third, custom.id, ["News day"])
        sqlite_journal.set_notes(conn, first, "<p>Held for the target. <b>Good</b> patience.</p>")
        sqlite_journal.set_notes(conn, second, "<p>Entered late, chased price.</p>")
        for index, trade_id in enumerate((first, first) if both_on_first else (first, second)):
            sqlite_journal.add_screenshot(conn, shots, trade_id, f"chart {index}.png", image + bytes([index]))
        conn.execute(
            "INSERT INTO day_notes (trade_date, notes, updated_at) VALUES ('2024-01-05', '<p>Tight day</p>', '2024-01-06T01:00:00Z')"
        )
        sqlite_ledger.add_entry(conn, eval_account.id, "evaluation_fee", D("167"), date(2024, 1, 2), "50K eval")
        sqlite_ledger.add_entry(conn, eval_account.id, "reset_fee", D("80.50"), date(2024, 1, 20))
        sqlite_ledger.add_entry(conn, funded.id, "payout", D("1500.25"), date(2024, 3, 1), "first payout")
        sqlite_accounts.archive_account(
            conn, funded.id, outcome="closed", ended_on=date(2023, 12, 1), note="rolled to a new one"
        )
    conn.close()
    if not with_files:
        shutil.rmtree(shots, ignore_errors=True)
    return path


@pytest.fixture
def source(tmp_path):
    return build_source(tmp_path)


def migrate(db, user, path, **kwargs):
    return migrate_local.run(db, user, path, screenshots_dir=path.parent / "screenshots", **kwargs)


def failures(report):
    return [check.name for check in report.checks if not check.ok and not check.informational]


def count(conn, table):
    return conn.execute(f'SELECT count(*) AS n FROM "{table}"').fetchone()["n"]  # noqa: S608 (fixed table names)


# ---- the happy path --------------------------------------------------------------------------------------------


def test_the_whole_journal_arrives_and_every_golden_check_passes(db, alice, source):
    report = migrate(db, alice, source)
    assert report.ok, failures(report)
    assert report.copied["accounts"] == 2 and report.copied["trades"] == 35 and report.copied["fill_pairs"] > 35
    assert report.missing_screenshot_files == []
    with db.tenant_session(alice) as conn:
        assert (count(conn, "accounts"), count(conn, "trades"), count(conn, "screenshots")) == (2, 35, 2)
        assert count(conn, "day_notes") == 1 and count(conn, "account_ledger") == 3


def test_there_are_enough_checks_to_mean_something(db, alice, source):
    report = migrate(db, alice, source)
    assert len(report.checks) >= 20
    assert sum(1 for c in report.checks if c.informational) == 2


def test_money_stays_exact_to_the_cent(db, alice, source):
    migrate(db, alice, source)
    with db.tenant_session(alice) as conn:
        apex = conn.execute("SELECT * FROM accounts WHERE name = 'Apex 07'").fetchone()
        assert (apex["daily_loss_limit"], apex["trail_lock_level"], apex["trail_lock_enabled"]) == (
            D("1000.50"),
            D("52600.00"),
            True,
        )
        assert conn.execute("SELECT sum(amount) AS total FROM account_ledger").fetchone()["total"] == D("1747.75")
        assert conn.execute(
            "SELECT fee_per_contract_rt AS f FROM fee_schedules ORDER BY fee_per_contract_rt LIMIT 1"
        ).fetchone()["f"] == D("2.1")
        assert conn.execute("SELECT sum(net_pnl) AS n FROM trades WHERE account_id = %s", (apex["id"],)).fetchone()[
            "n"
        ] == D("760.00")  # 835 at a flat $4, less 50 NQ contracts at $1.50 more


def test_notes_tags_screenshots_and_archive_details_come_across(db, alice, source):
    migrate(db, alice, source)
    with db.tenant_session(alice) as conn:
        notes = {r["notes"] for r in conn.execute("SELECT notes FROM trades WHERE notes <> ''")}
        assert notes == {"<p>Held for the target. <b>Good</b> patience.</p>", "<p>Entered late, chased price.</p>"}
        tags = {
            (r["g"], r["t"])
            for r in conn.execute(
                "SELECT g.name AS g, t.name AS t FROM trade_tags x JOIN tags t ON t.id = x.tag_id JOIN tag_groups g ON g.id = t.group_id"
            )
        }
        assert tags == {
            ("Setup", "Opening drive"),
            ("Setup", "Pullback"),
            ("Mistake", "Chased"),
            ("Market context", "News day"),
        }
        lucid = conn.execute(
            "SELECT outcome, ended_on, archive_note, account_type FROM accounts WHERE name = 'Lucid 01'"
        ).fetchone()
        assert (lucid["outcome"], lucid["ended_on"], lucid["archive_note"], lucid["account_type"]) == (
            "closed",
            date(2023, 12, 1),
            "rolled to a new one",
            "funded",
        )
        assert conn.execute("SELECT notes FROM day_notes").fetchone()["notes"] == "<p>Tight day</p>"
        assert {r["original_name"] for r in conn.execute("SELECT original_name FROM screenshots")} == {
            "chart 0.png",
            "chart 1.png",
        }


def test_settings_arrive_and_a_start_time_without_a_leading_zero_is_padded(db, alice, source):
    migrate(db, alice, source)
    with db.tenant_session(alice) as conn:
        row = conn.execute("SELECT timezone, trading_day_start, theme FROM user_settings").fetchone()
        assert (row["timezone"], row["trading_day_start"], row["theme"]) == ("America/New_York", "09:30", "light")


def test_the_source_groups_reuse_the_defaults_and_add_the_custom_one(db, alice, source):
    migrate(db, alice, source)
    with db.tenant_session(alice) as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM tag_groups ORDER BY sort_order, id")]
        assert names == ["Setup", "Mistake", "Emotion", "Market context"]


def test_the_stored_trades_are_consistent_with_todays_grouping_and_fee_rules(db, alice, source):
    report = migrate(db, alice, source)
    notes = {c.name: c for c in report.checks if c.informational}
    assert all(check.ok for check in notes.values()), {name: (c.source, c.target) for name, c in notes.items()}


def test_the_usage_counters_match_the_copy(db, alice, source):
    migrate(db, alice, source)
    from tradejournal.pg import usage

    with db.tenant_session(alice) as conn:
        u = usage.get_usage(conn)
        assert (u.accounts_count, u.trades_count, u.screenshots_count) == (2, 35, 2)


# ---- safety ----------------------------------------------------------------------------------------------------


def test_a_dry_run_writes_nothing_but_reports_everything(db, alice, source):
    report = migrate(db, alice, source, dry_run=True)
    assert report.dry_run and report.ok
    assert report.copied["trades"] == 35
    with db.tenant_session(alice) as conn:
        assert (count(conn, "accounts"), count(conn, "trades"), count(conn, "tags"), count(conn, "day_notes")) == (
            0,
            0,
            0,
            0,
        )
        assert (
            conn.execute("SELECT timezone FROM user_settings").fetchone()["timezone"] == "America/Los_Angeles"
        )  # untouched
    assert migrate(db, alice, source).ok  # and the real run is still possible afterwards


def test_it_refuses_a_user_who_already_has_data_and_changes_nothing(db, alice, source):
    migrate(db, alice, source)
    with pytest.raises(MigrationRefused, match="already has journal data"):
        migrate(db, alice, source)
    with db.tenant_session(alice) as conn:
        assert count(conn, "trades") == 35  # still exactly one copy


def test_a_failure_part_way_leaves_nothing_behind(db, alice, source, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(migrate_local.usage, "refresh", boom)  # the very last step
    with pytest.raises(RuntimeError, match="disk on fire"):
        migrate(db, alice, source)
    with db.tenant_session(alice) as conn:
        assert (count(conn, "accounts"), count(conn, "trades"), count(conn, "fill_pairs")) == (0, 0, 0)


def test_the_original_file_is_never_opened_or_changed(db, alice, source):
    before = (source.read_bytes(), sorted(p.name for p in source.parent.iterdir()))
    migrate(db, alice, source)
    assert (source.read_bytes(), sorted(p.name for p in source.parent.iterdir())) == before


def test_a_journal_in_wal_mode_with_unwritten_changes_is_copied_with_them(db, alice, tmp_path):
    path = build_source(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "INSERT INTO day_notes (trade_date, notes, updated_at) VALUES ('2024-02-02', 'only in the wal', '2024-02-02T00:00:00Z')"
    )
    conn.commit()  # lives in journal.db-wal until a checkpoint; the connection stays open, as an app that is running would
    try:
        assert (tmp_path / "journal.db-wal").exists()
        report = migrate(db, alice, path)
        assert report.ok, failures(report)
        with db.tenant_session(alice) as pg_conn:
            assert count(pg_conn, "day_notes") == 2
    finally:
        conn.close()


def test_a_missing_journal_is_a_clear_error(db, alice, tmp_path):
    with pytest.raises(FileNotFoundError, match="no journal"):
        migrate_local.run(db, alice, tmp_path / "nope.db")


def test_screenshot_files_that_are_missing_are_listed_not_fatal(db, alice, tmp_path):
    path = build_source(tmp_path)
    next((tmp_path / "screenshots").iterdir()).unlink()
    report = migrate(db, alice, path)
    assert report.ok and len(report.missing_screenshot_files) == 1


# ---- the screenshot files ------------------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return LocalFileStore(tmp_path / "store")


def header(kind: str, width: int, height: int) -> bytes:
    """Just the header of an image of the given kind (all the server reads), padded so it is not tiny."""
    if kind == "png":
        data = (
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">II", width, height)
            + b"\x08\x02\x00\x00\x00"
        )
    elif kind == "gif":
        data = b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00;"
    elif kind == "jpeg":
        data = b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    else:  # a lossy webp
        frame = b"\x10\x02\x00\x9d\x01\x2a" + struct.pack("<HH", width, height)
        body = b"WEBPVP8 " + struct.pack("<I", len(frame)) + frame
        data = b"RIFF" + struct.pack("<I", len(body)) + body
    return data + b"padding"


WIDE = header("png", 30, 50)  # 30 wide, 50 tall: not square, so a swap of the two shows


@pytest.fixture
def pictured(tmp_path):
    """A journal whose two screenshots are real images."""
    return build_source(tmp_path / "journal", image=WIDE)


def old_files(path: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in (path.parent / "screenshots").iterdir()}


def rows(db, user):
    with db.tenant_session(user) as conn:
        return conn.execute("SELECT * FROM screenshots ORDER BY id").fetchall()


def test_with_a_store_every_screenshot_file_is_taken_in_and_read_back_with_the_same_bytes(db, alice, pictured, store):
    report = migrate(db, alice, pictured, files=store)
    assert report.ok, failures(report)
    assert len(report.placed) == 2 and report.files_not_copied == [] and report.store_given
    originals = sorted(old_files(pictured).values())
    found = rows(db, alice)
    assert sorted(store.get(r["stored_name"]) for r in found) == originals
    for r in found:
        assert r["stored_name"].startswith(f"u/{alice}/") and r["stored_name"].endswith(".png")
        assert (r["content_type"], r["width"], r["height"]) == ("image/png", 30, 50)  # wide 30, tall 50
        assert r["size_bytes"] == len(store.get(r["stored_name"]))
    assert {r["original_name"] for r in found} == {"chart 0.png", "chart 1.png"}  # what the person called them
    assert any(c.name.startswith("screenshot files:") and c.ok for c in report.checks)
    with db.tenant_session(alice) as conn:
        assert usage.get_usage(conn).storage_bytes == sum(r["size_bytes"] for r in found) > 0  # counted, not zero


def test_a_dry_run_checks_every_file_and_writes_none(db, alice, pictured, store):
    report = migrate(db, alice, pictured, files=store, dry_run=True)
    assert report.ok and len(report.placed) == 2
    assert list(store.list()) == [] and rows(db, alice) == []
    assert not any(c.name.startswith("screenshot files:") for c in report.checks)  # nothing was written to read back
    assert "would be taken into the file store" in migrate_local.format_report(report)


def test_a_file_that_is_not_an_acceptable_image_is_not_copied_but_its_row_is_and_the_report_says_why(
    db, alice, tmp_path, store
):
    report = migrate(db, alice, build_source(tmp_path / "journal"), files=store)  # the old test bytes claim 0 by 0
    assert report.ok and report.placed == [] and len(report.files_not_copied) == 2
    assert all("damaged" in reason for _, reason in report.files_not_copied)
    assert list(store.list()) == []
    assert {r["stored_name"] for r in rows(db, alice)} == {name for name, _ in report.files_not_copied}  # the old names
    text = migrate_local.format_report(report)
    assert "not copied:" in text and "an export lists them as missing" in text


def test_a_file_over_ten_megabytes_is_not_copied_and_one_of_exactly_ten_is(db, alice, pictured, store):
    first, second = sorted((pictured.parent / "screenshots").iterdir())
    first.write_bytes(WIDE + b"\x00" * (pg_screenshots.MAX_FILE_BYTES - len(WIDE) + 1))  # one byte over
    second.write_bytes(WIDE + b"\x00" * (pg_screenshots.MAX_FILE_BYTES - len(WIDE)))  # exactly the limit
    report = migrate(db, alice, pictured, files=store)
    assert [name for name, _ in report.files_not_copied] == [first.name] and len(report.placed) == 1
    assert "larger than 10 MB" in report.files_not_copied[0][1]
    assert report.placed[0].size == pg_screenshots.MAX_FILE_BYTES


def test_a_file_that_is_missing_is_listed_as_missing_and_not_as_refused(db, alice, pictured, store):
    next(iter(sorted((pictured.parent / "screenshots").iterdir()))).unlink()
    report = migrate(db, alice, pictured, files=store)
    assert len(report.missing_screenshot_files) == 1 and report.files_not_copied == [] and len(report.placed) == 1
    assert report.ok


def test_a_trade_keeps_at_most_the_screenshots_one_may_have_and_the_rest_are_not_copied(
    db, alice, tmp_path, store, monkeypatch
):
    monkeypatch.setattr(pg_screenshots, "MAX_PER_TRADE", 1)
    path = build_source(tmp_path / "journal", image=PNG_1X1, both_on_first=True)
    report = migrate(db, alice, path, files=store)
    assert len(report.placed) == 1 and len(report.files_not_copied) == 1
    assert "already has the 1 screenshots" in report.files_not_copied[0][1]
    assert len(rows(db, alice)) == 2 and report.ok  # both rows, one file


def test_the_screenshot_space_one_person_has_is_not_exceeded_and_filling_it_exactly_is_allowed(
    db, alice, bob, pictured, store, monkeypatch
):
    one = len(WIDE) + 1  # each file is the image and one more byte
    monkeypatch.setattr(pg_screenshots, "MAX_STORED_BYTES", one + 1)  # room for one, not two
    report = migrate(db, alice, pictured, files=store)
    assert len(report.placed) == 1 and len(report.files_not_copied) == 1
    assert "screenshot space" in report.files_not_copied[0][1]

    monkeypatch.setattr(pg_screenshots, "MAX_STORED_BYTES", 2 * one)  # exactly both
    report = migrate(db, bob, pictured, files=store)  # the same journal for someone else: new keys, so no clash
    assert len(report.placed) == 2 and report.files_not_copied == []


@pytest.mark.parametrize("limit", ["MAX_PER_TRADE", "MAX_STORED_BYTES"])
def test_the_first_reason_a_file_is_refused_for_is_the_one_reported(db, alice, tmp_path, store, monkeypatch, limit):
    monkeypatch.setattr(pg_screenshots, limit, 0)  # every file would also break this limit
    report = migrate(db, alice, build_source(tmp_path / "journal"), files=store)  # but these claim to be 0 by 0
    assert len(report.files_not_copied) == 2
    assert all("damaged" in reason for _, reason in report.files_not_copied)


@pytest.mark.parametrize(
    ("kind", "extension", "content_type"),
    [
        ("gif", ".gif", "image/gif"),
        ("jpeg", ".jpg", "image/jpeg"),
        ("webp", ".webp", "image/webp"),
        ("png", ".png", "image/png"),
    ],
)
def test_each_kind_of_image_keeps_its_own_extension_and_type(db, alice, tmp_path, store, kind, extension, content_type):
    path = build_source(tmp_path / "journal", image=header(kind, 30, 50))
    report = migrate(db, alice, path, files=store)
    assert report.ok and len(report.placed) == 2
    for r in rows(db, alice):
        assert r["stored_name"].endswith(extension) and (r["content_type"], r["width"], r["height"]) == (
            content_type,
            30,
            50,
        )


def test_a_stored_name_with_a_folder_in_it_is_never_opened(db, alice, pictured, store):
    secret = pictured.parent / "secret.png"
    secret.write_bytes(PNG_1X1 + b"not-yours")
    with sqlite3.connect(pictured) as raw:
        raw.execute("UPDATE screenshots SET stored_name = '../secret.png' WHERE id = (SELECT min(id) FROM screenshots)")
    report = migrate(db, alice, pictured, files=store)
    assert len(report.placed) == 1  # only the honest one
    assert b"not-yours" not in b"".join(store.get(f.key) for f in store.list())
    assert "../secret.png" in {r["stored_name"] for r in rows(db, alice)}  # the row is copied as it was, file not taken


def test_a_failure_after_the_files_are_in_takes_them_out_again(db, alice, pictured, store, monkeypatch):
    def broken(conn):
        raise RuntimeError("the counters failed")

    monkeypatch.setattr(migrate_local.usage, "refresh", broken)
    with pytest.raises(RuntimeError, match="the counters failed"):
        migrate(db, alice, pictured, files=store)
    assert list(store.list()) == [] and rows(db, alice) == []  # nothing in the database, nothing in the store


def test_the_golden_check_catches_a_file_that_arrives_changed(db, alice, pictured, store, monkeypatch):
    real = LocalFileStore.put
    monkeypatch.setattr(LocalFileStore, "put", lambda self, key, data: real(self, key, data + b"x"))
    report = migrate(db, alice, pictured, files=store)
    assert not report.ok and any(c.name.startswith("screenshot files:") for c in report.checks if not c.ok)
    assert "[FAIL]" in migrate_local.format_report(report)


def test_the_golden_check_catches_a_file_that_never_arrived(db, alice, pictured, store, monkeypatch):
    monkeypatch.setattr(LocalFileStore, "put", lambda self, key, data: None)
    report = migrate(db, alice, pictured, files=store)
    assert not report.ok
    [check] = [c for c in report.checks if c.name.startswith("screenshot files:")]
    assert set(check.target.values()) == {"not in the store"}


def test_the_golden_check_catches_a_row_pointing_at_the_wrong_key(db, alice, pictured, store, monkeypatch):
    real = migrate_local._batch

    def wrong_key(conn, query, rows, **kwargs):
        if "INSERT INTO screenshots" in query:
            rows = [(r[0], f"u/elsewhere/{number}.png", *r[2:]) for number, r in enumerate(rows)]
        return real(conn, query, rows, **kwargs)

    monkeypatch.setattr(migrate_local, "_batch", wrong_key)
    report = migrate(db, alice, pictured, files=store, dry_run=True)
    assert not report.ok and any("screenshots" in name for name in failures(report))


def test_with_no_store_only_the_rows_are_copied_and_the_report_says_so(db, alice, pictured):
    report = migrate(db, alice, pictured)
    assert report.ok and report.placed == [] and not report.store_given
    assert {r["stored_name"] for r in rows(db, alice)} == set(old_files(pictured))  # the old names
    assert "No file store was given" in migrate_local.format_report(report)


def test_each_person_gets_keys_in_their_own_folder(db, alice, bob, tmp_path, store):
    migrate(db, alice, build_source(tmp_path / "a", image=PNG_1X1), files=store)
    migrate(db, bob, build_source(tmp_path / "b", image=PNG_1X1), files=store)
    assert {r["stored_name"].split("/")[1] for r in rows(db, alice)} == {str(alice)}
    assert {r["stored_name"].split("/")[1] for r in rows(db, bob)} == {str(bob)}
    assert len(list(store.list())) == 4


def test_a_migrated_journal_exports_every_image_and_none_is_missing(db, alice, pictured, store):
    migrate(db, alice, pictured, files=store)
    with db.tenant_session(alice, snapshot=True) as conn:
        result = pg_export.build(
            conn,
            profile={},
            exported_at=datetime(2026, 10, 4, tzinfo=UTC),
            app_version="t",
            clean_html=sanitize_notes,
            store=store,
        )
    with result.file as handle, zipfile.ZipFile(io.BytesIO(handle.read())) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["screenshot_files"] == {"included": True, "count": 2, "missing": []}
        assert sorted(archive.read(n) for n in archive.namelist() if n.startswith("screenshots/")) == sorted(
            old_files(pictured).values()
        )


def test_an_older_journal_is_brought_up_to_date_on_the_copy_only(db, alice, tmp_path):
    path = tmp_path / "journal.db"
    conn = sqlite3.connect(path)
    conn.executescript(sqlite_db.SCHEMA_V1)  # a version-1 journal: no archive columns, no ledger, no tag colors
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO accounts (name, firm, created_at) VALUES ('Old 01', 'Apex', '2022-01-01T00:00:00Z')")
    conn.commit()
    conn.close()
    original = path.read_bytes()
    report = migrate_local.run(db, alice, path)
    assert report.ok, failures(report)
    assert path.read_bytes() == original  # the upgrade happened on the temporary copy
    with db.tenant_session(alice) as pg_conn:
        row = pg_conn.execute("SELECT name, account_type, outcome FROM accounts").fetchone()
        assert (row["name"], row["account_type"], row["outcome"]) == ("Old 01", "evaluation", None)


# ---- tenancy ---------------------------------------------------------------------------------------------------


def test_the_copy_belongs_to_one_user_only(db, alice, bob, source, tmp_path):
    migrate(db, alice, source)
    with db.tenant_session(bob) as conn:
        assert (
            count(conn, "accounts"),
            count(conn, "trades"),
            count(conn, "screenshots"),
            count(conn, "day_notes"),
        ) == (0, 0, 0, 0)
        assert [r["name"] for r in conn.execute("SELECT name FROM tag_groups ORDER BY id")] == [
            "Setup",
            "Mistake",
            "Emotion",
        ]
    # Bob has his own journal file (screenshot file names are unique across the whole database, so two people cannot
    # share one); filling his journal collides with nothing of Alice's, and Alice's is untouched.
    assert migrate(db, bob, build_source(tmp_path / "bobs")).ok
    with db.tenant_session(alice) as conn:
        assert count(conn, "trades") == 35
    with db.tenant_session(bob) as conn:
        assert count(conn, "trades") == 35


# ---- the golden check catches a wrong copy ----------------------------------------------------------------------


def test_the_golden_check_fails_when_the_copy_drops_or_changes_something(db, alice, source, monkeypatch):
    """Mutations of the copy itself: each must turn at least one check red."""
    real_batch = migrate_local._batch

    def without(table_fragment):
        def batch(conn, query, rows, **kwargs):
            return real_batch(conn, query, [] if table_fragment in query else rows, **kwargs)

        return batch

    for fragment, expected in [
        ("INSERT INTO trade_tags", "which tag sits on which trade"),
        ("INSERT INTO day_notes", "day notes"),
        ("INSERT INTO account_ledger", "ledger"),
        ("INSERT INTO screenshots", "screenshots"),
        ("INSERT INTO fee_schedules", "fee schedules"),
    ]:
        monkeypatch.setattr(migrate_local, "_batch", without(fragment))
        report = migrate(db, alice, source, dry_run=True)
        assert not report.ok, fragment
        assert any(expected in name for name in failures(report)), (fragment, failures(report))
    monkeypatch.setattr(migrate_local, "_batch", real_batch)
    assert migrate(db, alice, source, dry_run=True).ok


def test_the_golden_check_notices_a_changed_amount(db, alice, source, monkeypatch):
    real = migrate_local._money
    monkeypatch.setattr(
        migrate_local,
        "_money",
        lambda text: None if text is None else real(text) + D("0.01") if text == "-475.00" else real(text),
    )
    report = migrate(db, alice, source, dry_run=True)
    assert not report.ok
    assert any("per account" in name or "net result" in name for name in failures(report))


def test_the_report_names_every_check_and_says_plainly_whether_to_trust_the_copy(db, alice, source):
    text = migrate_local.format_report(migrate(db, alice, source, dry_run=True))
    assert "DRY RUN" in text and "RESULT: every check passed." in text and "[ok  ]" in text and "FAIL" not in text


def test_a_failing_report_says_not_to_use_the_copy(db, alice, source, monkeypatch):
    monkeypatch.setattr(
        migrate_local,
        "_batch",
        lambda conn, query, rows, **kw: [] if "trade_tags" in query else psycopg_batch(conn, query, rows, **kw),
    )
    text = migrate_local.format_report(migrate(db, alice, source, dry_run=True))
    assert "[FAIL]" in text and "Do not use this copy" in text


psycopg_batch = migrate_local._batch  # captured before any test patches it


# ---- the command line --------------------------------------------------------------------------------------------


def test_the_command_prints_the_report_and_exits_zero_when_every_check_passes(app_url, alice, source, capsys):
    code = migrate_local.main(
        ["--source", str(source), "--user-id", str(alice), "--database-url", app_url, "--dry-run"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "DRY RUN: nothing was written." in out and "RESULT: every check passed." in out
    assert "35" in out and "[ok  ]" in out


def test_the_command_takes_the_files_into_the_folder_it_is_given(app_url, alice, pictured, store, capsys):
    args = [
        "--source",
        str(pictured),
        "--user-id",
        str(alice),
        "--database-url",
        app_url,
        "--files-dir",
        str(store.root),
    ]
    assert migrate_local.main(args) == 0
    out = capsys.readouterr().out
    assert "2 screenshot file(s) taken into the file store" in out and len(list(store.list())) == 2


def test_the_folder_can_come_from_the_environment_and_without_it_the_files_are_left_behind(
    app_url, alice, bob, pictured, store, capsys, monkeypatch
):
    monkeypatch.setenv("TJ_FILES_DIR", str(store.root))
    args = ["--source", str(pictured), "--user-id", str(alice), "--database-url", app_url, "--dry-run"]
    assert (
        migrate_local.main(args) == 0
        and "2 screenshot file(s) would be taken into the file store" in capsys.readouterr().out
    )
    monkeypatch.delenv("TJ_FILES_DIR")
    other = ["--source", str(pictured), "--user-id", str(bob), "--database-url", app_url, "--dry-run"]
    assert migrate_local.main(other) == 0 and "No file store was given" in capsys.readouterr().out
    assert list(store.list()) == []


def test_the_command_exits_three_with_a_plain_message_for_a_user_who_has_data(app_url, alice, source, capsys):
    args = ["--source", str(source), "--user-id", str(alice), "--database-url", app_url]
    assert migrate_local.main(args) == 0
    assert migrate_local.main(args) == 3
    assert "refused: this user already has journal data" in capsys.readouterr().err


def test_the_command_needs_a_database_url(alice, source, capsys, monkeypatch):
    monkeypatch.delenv("TJ_DATABASE_URL", raising=False)
    assert migrate_local.main(["--source", str(source), "--user-id", str(alice)]) == 2
    assert "TJ_DATABASE_URL" in capsys.readouterr().err


def test_the_report_never_prints_a_password_or_a_note(app_url, alice, source, capsys):
    migrate_local.main(["--source", str(source), "--user-id", str(alice), "--database-url", app_url, "--dry-run"])
    out = capsys.readouterr().out
    assert app_url.split("@")[0].split(":")[-1] not in out  # the role's password
    assert (
        "Held for the target" not in out and "chased price" not in out
    )  # the journal's own words stay out of the report


# ---- into a bucket ------------------------------------------------------------------------------------------------------


@pytest.fixture
def bucket_env(monkeypatch):
    from fake_s3 import FakeBucket

    access, secret = "test" + "accesskey0123456789", "test" + "secret/with+odd=chars"  # noqa: S105 - made up
    server = FakeBucket(access_key=access, secret=secret)
    monkeypatch.delenv("TJ_FILES_DIR", raising=False)
    for name, value in (
        ("TJ_S3_ENDPOINT", server.url),
        ("TJ_S3_BUCKET", server.bucket),
        ("TJ_S3_ACCESS_KEY_ID", access),
        ("TJ_S3_SECRET_ACCESS_KEY", secret),
    ):
        monkeypatch.setenv(name, value)
    yield server, secret
    server.stop()


def test_the_command_can_take_the_files_into_a_bucket_and_checks_each_against_its_checksum(
    app_url, alice, pictured, bucket_env, capsys
):
    server, secret = bucket_env
    args = ["--source", str(pictured), "--user-id", str(alice), "--database-url", app_url]
    assert migrate_local.main(args) == 0
    captured = capsys.readouterr()
    assert "2 screenshot file(s) taken into the file store" in captured.out and "every check passed" in captured.out
    assert len(server.keys()) == 2 and all(k.startswith(f"u/{alice}/") for k in server.keys())
    assert secret not in captured.out + captured.err


def test_a_dry_run_into_a_bucket_leaves_the_bucket_empty(app_url, alice, pictured, bucket_env, capsys):
    server, _ = bucket_env
    args = ["--source", str(pictured), "--user-id", str(alice), "--database-url", app_url, "--dry-run"]
    assert migrate_local.main(args) == 0
    assert "2 screenshot file(s) would be taken into the file store" in capsys.readouterr().out
    assert server.keys() == []


def test_a_folder_and_a_bucket_together_are_refused_before_anything_is_read(
    app_url, alice, pictured, store, bucket_env, capsys
):
    args = [
        "--source",
        str(pictured),
        "--user-id",
        str(alice),
        "--database-url",
        app_url,
        "--files-dir",
        str(store.root),
    ]
    assert migrate_local.main(args) == 2
    assert "choose one" in capsys.readouterr().err


def test_a_half_set_bucket_is_refused_by_name(app_url, alice, pictured, bucket_env, monkeypatch, capsys):
    monkeypatch.delenv("TJ_S3_BUCKET")
    args = ["--source", str(pictured), "--user-id", str(alice), "--database-url", app_url]
    assert migrate_local.main(args) == 2
    assert "go together" in capsys.readouterr().err
