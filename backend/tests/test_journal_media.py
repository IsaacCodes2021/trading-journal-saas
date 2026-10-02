"""Notes and screenshots."""

import pytest

from helpers import SAMPLE, fixture_bytes
from tradejournal import db, importer, journal

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


class Ids(list):
    """Trade ids, plus the import that created them."""

    import_id: int


@pytest.fixture
def trades(conn, account):
    report = importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
    ids = Ids(row["id"] for row in conn.execute("SELECT id FROM trades ORDER BY entry_at"))
    ids.import_id = report.files[0].import_id
    return ids


@pytest.mark.parametrize(
    "html, stored",
    [
        ("<b>Waited</b> for the pullback", "<b>Waited</b> for the pullback"),
        ("  plain text  ", "plain text"),
        ("<br>", ""),
        ("<div><br></div>", ""),
        ("&nbsp; ", ""),
        (None, ""),
    ],
)
def test_clean_notes(html, stored):
    assert journal.clean_notes(html) == stored


def test_notes_round_trip(conn, trades):
    with db.transaction(conn):
        journal.set_notes(conn, trades[0], "<p>Chased the <b>breakout</b>.</p>")
    assert journal.notes(conn, trades[0]) == "<p>Chased the <b>breakout</b>.</p>"
    with db.transaction(conn):
        journal.set_notes(conn, trades[0], "<div><br></div>")  # emptied in the editor
    assert journal.notes(conn, trades[0]) == ""
    with pytest.raises(LookupError):
        journal.notes(conn, 999_999)


@pytest.mark.parametrize("data, extension", [(PNG, ".png"), (JPEG, ".jpg"), (WEBP, ".webp"), (b"GIF89a" + b"\0" * 8, ".gif")])
def test_image_types_are_recognized_by_content(data, extension):
    assert journal.image_extension(data) == extension


@pytest.mark.parametrize("data", [b"", b"just some text", b"%PDF-1.7 not an image"])
def test_non_images_are_rejected(data):
    with pytest.raises(ValueError):
        journal.image_extension(data)


def test_too_large_images_are_rejected(monkeypatch):
    monkeypatch.setattr(journal, "MAX_SCREENSHOT_BYTES", 10)
    with pytest.raises(ValueError, match="under"):
        journal.image_extension(PNG)


def test_add_list_and_delete_screenshots(conn, trades, tmp_path):
    with db.transaction(conn):
        first = journal.add_screenshot(conn, tmp_path, trades[0], "C:\\charts\\entry.png", PNG)
        second = journal.add_screenshot(conn, tmp_path, trades[0], "exit.jpg", JPEG)
    assert first.original_name == "entry.png"  # folders stripped from the name
    assert first.stored_name.endswith(".png") and second.stored_name.endswith(".jpg")
    assert (tmp_path / first.stored_name).read_bytes() == PNG
    assert [s.id for s in journal.screenshots(conn, trades[0])] == [first.id, second.id]
    assert journal.screenshots(conn, trades[1]) == []

    with db.transaction(conn):
        name = journal.delete_screenshot(conn, first.id)
    journal.remove_file(tmp_path, name)
    assert not (tmp_path / first.stored_name).exists()
    assert [s.id for s in journal.screenshots(conn, trades[0])] == [second.id]
    assert journal.delete_screenshot(conn, first.id) is None


def test_a_rejected_screenshot_leaves_no_file(conn, trades, tmp_path):
    with pytest.raises(LookupError):
        journal.add_screenshot(conn, tmp_path, 999_999, "x.png", PNG)
    with pytest.raises(ValueError):
        journal.add_screenshot(conn, tmp_path, trades[0], "notes.txt", b"hello")
    assert list(tmp_path.iterdir()) == []


def test_journal_summary(conn, trades, tmp_path):
    with db.transaction(conn):
        journal.set_notes(conn, trades[0], "<p>good read</p>")
        journal.add_screenshot(conn, tmp_path, trades[1], "a.png", PNG)
        journal.add_screenshot(conn, tmp_path, trades[1], "b.png", PNG)
    assert journal.journal_summary(conn) == {trades[0]: (True, 0), trades[1]: (False, 2)}


def test_undoing_an_import_cleans_up_its_screenshot_files(conn, trades, tmp_path):
    with db.transaction(conn):
        shot = journal.add_screenshot(conn, tmp_path, trades[0], "a.png", PNG)
    (tmp_path / "stray.png").write_bytes(PNG)  # e.g. left by an interrupted save
    importer.undo_import(conn, trades.import_id)
    assert journal.screenshots(conn, trades[0]) == []  # rows go with the trade
    assert journal.remove_orphan_files(conn, tmp_path) == 2
    assert not (tmp_path / shot.stored_name).exists()
    assert journal.remove_orphan_files(conn, tmp_path / "missing") == 0
