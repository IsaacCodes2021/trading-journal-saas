"""The journal tests (test_journal.py, test_tag_editing.py, test_journal_media.py) run against Postgres.

Left out on purpose: the tests that drive the importer or the query layer (they come back when those modules are
ported), the SQLite upgrade tests (replaced by the migration tests), and the pill-color tests (pure UI code).
"""

import psycopg
import pytest
from pg_helpers import make_account, make_trades

from tradejournal import journal as shared
from tradejournal.pg import accounts, journal

PRESETS = list(shared.TAG_COLOR_PRESETS)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@pytest.fixture
def trade_ids(conn):
    return make_trades(conn, make_account(conn).id)


def group_id(conn, name):
    return next(g.id for g in journal.tag_groups(conn) if g.name == name)


def counts(conn):
    return {t.name: t.trade_count for t in journal.tags(conn)}


# ---- test_journal.py -------------------------------------------------------------------------------------------


def test_default_groups(conn):
    assert [g.name for g in journal.tag_groups(conn)] == ["Setup", "Mistake", "Emotion"]


def test_tagging_a_trade(conn, trade_ids):
    setup = group_id(conn, "Setup")
    tags = journal.set_trade_tags(conn, trade_ids[0], setup, ["scalp", "  momentum  "])
    assert [t.name for t in tags] == ["scalp", "momentum"]
    assert [t.name for t in journal.trade_tags(conn, trade_ids[0])] == ["momentum", "scalp"]


def test_names_match_regardless_of_capitalization(conn, trade_ids):
    setup = group_id(conn, "Setup")
    journal.set_trade_tags(conn, trade_ids[0], setup, ["Scalp"])
    again = journal.set_trade_tags(conn, trade_ids[1], setup, ["SCALP", "scalp"])
    assert [t.name for t in again] == ["Scalp"]  # the stored spelling, once
    assert [(t.name, t.trade_count) for t in journal.tags(conn)] == [("Scalp", 2)]


def test_setting_one_group_leaves_other_groups_alone(conn, trade_ids):
    setup, mistake = group_id(conn, "Setup"), group_id(conn, "Mistake")
    trade = trade_ids[0]
    journal.set_trade_tags(conn, trade, setup, ["scalp"])
    journal.set_trade_tags(conn, trade, mistake, ["chased entry"])
    journal.set_trade_tags(conn, trade, setup, ["momentum"])  # replaces scalp only
    assert [t.name for t in journal.trade_tags(conn, trade)] == ["momentum", "chased entry"]
    journal.set_trade_tags(conn, trade, setup, [])
    assert [t.name for t in journal.trade_tags(conn, trade)] == ["chased entry"]
    assert counts(conn)["scalp"] == 0  # unused now but still available


def test_tags_by_trade(conn, trade_ids):
    setup, emotion = group_id(conn, "Setup"), group_id(conn, "Emotion")
    journal.set_trade_tags(conn, trade_ids[2], emotion, ["fomo"])
    journal.set_trade_tags(conn, trade_ids[2], setup, ["scalp"])
    journal.set_trade_tags(conn, trade_ids[5], setup, ["momentum"])
    by_trade = journal.tags_by_trade(conn)
    assert {trade: [t.name for t in tags] for trade, tags in by_trade.items()} == {
        trade_ids[2]: ["scalp", "fomo"],
        trade_ids[5]: ["momentum"],
    }


def test_a_tag_made_on_one_trade_is_offered_on_every_trade(conn, trade_ids):
    setup = group_id(conn, "Setup")
    scalp = journal.get_or_create_tag(conn, setup, "scalp")
    journal.add_trade_tag(conn, trade_ids[0], scalp.id)
    assert [t.name for t in journal.tags(conn)] == ["scalp"]
    journal.add_trade_tag(conn, trade_ids[3], scalp.id)
    journal.add_trade_tag(conn, trade_ids[3], scalp.id)  # adding twice is harmless
    assert counts(conn) == {"scalp": 2}

    journal.remove_trade_tag(conn, trade_ids[0], scalp.id)
    assert journal.trade_tags(conn, trade_ids[0]) == []
    assert [t.name for t in journal.trade_tags(conn, trade_ids[3])] == ["scalp"]  # untouched
    assert counts(conn) == {"scalp": 1}


def test_deleting_tags_and_groups(conn, trade_ids):
    setup = group_id(conn, "Setup")
    [scalp] = journal.set_trade_tags(conn, trade_ids[0], setup, ["scalp"])
    journal.delete_tag(conn, scalp.id)
    assert journal.trade_tags(conn, trade_ids[0]) == []

    custom = journal.create_group(conn, "Market condition")
    journal.set_trade_tags(conn, trade_ids[0], custom.id, ["trend day"])
    journal.delete_group(conn, custom.id)
    assert journal.trade_tags(conn, trade_ids[0]) == []
    assert "Market condition" not in [g.name for g in journal.tag_groups(conn)]


def test_bad_names_are_rejected(conn, trade_ids):
    setup = group_id(conn, "Setup")
    with pytest.raises(ValueError):
        journal.set_trade_tags(conn, trade_ids[0], setup, ["   "])
    with pytest.raises(ValueError):
        journal.set_trade_tags(conn, trade_ids[0], setup, ["x" * 41])
    with pytest.raises(ValueError, match="already a category"):
        journal.create_group(conn, "setup")


# ---- test_tag_editing.py ---------------------------------------------------------------------------------------


def test_new_tags_take_the_least_used_preset_color(conn):
    setup = group_id(conn, "Setup")
    first = journal.get_or_create_tag(conn, setup, "scalp")
    second = journal.get_or_create_tag(conn, setup, "momentum")
    assert (first.color, second.color) == (PRESETS[0], PRESETS[1])  # a regression guard: see the next test

    journal.update_tag(conn, first.id, name="scalp", group_id=setup, color=PRESETS[2])
    third = journal.get_or_create_tag(conn, setup, "breakout")
    assert third.color == PRESETS[0]  # free again now that scalp moved off it
    assert journal.get_or_create_tag(conn, setup, "SCALP").color == PRESETS[2]  # existing tag, unchanged


def test_the_color_counts_are_read_by_name_not_unpacked_as_pairs(conn):
    """The SQLite code did dict(cursor.fetchall()). With dict rows that silently builds {'color': 'uses'}, so every
    preset looks unused and every new tag gets the first color. This is the bug the SQL audit predicted."""
    setup = group_id(conn, "Setup")
    colors = [journal.get_or_create_tag(conn, setup, f"tag {n}").color for n in range(len(PRESETS))]
    assert colors == PRESETS  # each preset used once, in order, before any repeats


def test_the_database_only_stores_hex_colors(conn):
    setup = group_id(conn, "Setup")
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO tags (group_id, name, color) VALUES (%s, 'x', 'red')", (setup,))


def test_renaming_recoloring_and_moving_keep_the_trades(conn, trade_ids):
    setup, mistake = group_id(conn, "Setup"), group_id(conn, "Mistake")
    [tag] = journal.set_trade_tags(conn, trade_ids[0], setup, ["chased"])
    journal.add_trade_tag(conn, trade_ids[1], tag.id)
    edited = journal.update_tag(conn, tag.id, name="  Chased   entry ", group_id=mistake, color="#FF00AA")
    assert edited == shared.Tag(tag.id, mistake, "Chased entry", "#ff00aa")
    [after] = journal.tags(conn)
    assert (after.name, after.group_id, after.color, after.trade_count) == ("Chased entry", mistake, "#ff00aa", 2)
    assert journal.trade_tags(conn, trade_ids[1]) == [shared.Tag(tag.id, mistake, "Chased entry", "#ff00aa")]


def test_changing_only_capitalization_is_a_rename(conn):
    setup = group_id(conn, "Setup")
    tag = journal.get_or_create_tag(conn, setup, "scalp")
    journal.update_tag(conn, tag.id, name="Scalp", group_id=setup, color=tag.color)
    assert [t.name for t in journal.tags(conn)] == ["Scalp"]


def test_renaming_onto_another_tag_is_refused(conn):
    setup = group_id(conn, "Setup")
    journal.get_or_create_tag(conn, setup, "Scalp")
    other = journal.get_or_create_tag(conn, setup, "quick")
    with pytest.raises(ValueError, match="already a tag called 'Scalp'"):
        journal.update_tag(conn, other.id, name="scalp", group_id=setup, color=other.color)
    # The same name in another category is a different tag.
    journal.update_tag(conn, other.id, name="scalp", group_id=group_id(conn, "Mistake"), color=other.color)
    assert len(journal.tags(conn)) == 2


def test_merging_moves_every_trade_once(conn, trade_ids):
    setup = group_id(conn, "Setup")
    [scalp] = journal.set_trade_tags(conn, trade_ids[0], setup, ["Scalp"])
    quick = journal.get_or_create_tag(conn, setup, "quick")
    journal.add_trade_tag(conn, trade_ids[0], quick.id)  # carries both
    journal.add_trade_tag(conn, trade_ids[1], quick.id)
    journal.add_trade_tag(conn, trade_ids[2], quick.id)
    journal.merge_tags(conn, quick.id, scalp.id)
    assert counts(conn) == {"Scalp": 3}
    assert [t.name for t in journal.trade_tags(conn, trade_ids[0])] == ["Scalp"]
    with pytest.raises(ValueError):
        journal.merge_tags(conn, scalp.id, scalp.id)


def test_find_tag_ignores_capitalization_and_spacing(conn):
    setup = group_id(conn, "Setup")
    tag = journal.get_or_create_tag(conn, setup, "Opening drive")
    assert journal.find_tag(conn, setup, "  opening   DRIVE ") == tag
    assert journal.find_tag(conn, group_id(conn, "Mistake"), "Opening drive") is None


def test_renaming_categories(conn):
    setup = group_id(conn, "Setup")
    renamed = journal.rename_group(conn, setup, " Setups ")
    journal.rename_group(conn, setup, "setups")  # capitalization only
    assert renamed.name == "Setups"
    assert [g.name for g in journal.tag_groups(conn)] == ["setups", "Mistake", "Emotion"]
    with pytest.raises(ValueError, match="already a category"):
        journal.rename_group(conn, setup, "MISTAKE")


def test_your_colors_are_the_custom_ones_in_use(conn):
    setup = group_id(conn, "Setup")
    a = journal.get_or_create_tag(conn, setup, "a")
    b = journal.get_or_create_tag(conn, setup, "b")
    c = journal.get_or_create_tag(conn, setup, "c")
    journal.update_tag(conn, a.id, name="a", group_id=setup, color="#123456")
    journal.update_tag(conn, b.id, name="b", group_id=setup, color="#fedcba")
    journal.update_tag(conn, c.id, name="c", group_id=setup, color="#123456")
    assert journal.custom_colors(conn) == ["#123456", "#fedcba"]  # presets and repeats left out
    journal.delete_tag(conn, b.id)
    assert journal.custom_colors(conn) == ["#123456"]


# ---- test_journal_media.py -------------------------------------------------------------------------------------


def test_notes_round_trip(conn, trade_ids):
    journal.set_notes(conn, trade_ids[0], "<p>Chased the <b>breakout</b>.</p>")
    assert journal.notes(conn, trade_ids[0]) == "<p>Chased the <b>breakout</b>.</p>"
    journal.set_notes(conn, trade_ids[0], "<div><br></div>")  # emptied in the editor
    assert journal.notes(conn, trade_ids[0]) == ""
    with pytest.raises(LookupError):
        journal.notes(conn, 999_999)


def test_add_list_and_delete_screenshots(conn, trade_ids, tmp_path):
    first = journal.add_screenshot(conn, tmp_path, trade_ids[0], "C:\\charts\\entry.png", PNG)
    second = journal.add_screenshot(conn, tmp_path, trade_ids[0], "exit.jpg", JPEG)
    assert first.original_name == "entry.png"  # folders stripped from the name
    assert first.stored_name.endswith(".png") and second.stored_name.endswith(".jpg")
    assert (tmp_path / first.stored_name).read_bytes() == PNG
    assert [s.id for s in journal.screenshots(conn, trade_ids[0])] == [first.id, second.id]
    assert journal.screenshots(conn, trade_ids[1]) == []

    name = journal.delete_screenshot(conn, first.id)
    journal.remove_file(tmp_path, name)
    assert not (tmp_path / first.stored_name).exists()
    assert [s.id for s in journal.screenshots(conn, trade_ids[0])] == [second.id]
    assert journal.delete_screenshot(conn, first.id) is None


def test_a_rejected_screenshot_leaves_no_file(conn, trade_ids, tmp_path):
    with pytest.raises(LookupError):
        journal.add_screenshot(conn, tmp_path, 999_999, "x.png", PNG)
    with pytest.raises(ValueError):
        journal.add_screenshot(conn, tmp_path, trade_ids[0], "notes.txt", b"hello")
    assert list(tmp_path.iterdir()) == []


def test_a_screenshot_whose_row_cannot_be_saved_leaves_no_file(conn, trade_ids, tmp_path):
    """The compensation path: the file is written first, so a failed insert must delete it again."""

    class FailsOnInsert:
        def __init__(self, real):
            self.real = real

        def execute(self, query, params=None):
            if query.lstrip().startswith("INSERT INTO screenshots"):
                raise OSError("the database went away")
            return self.real.execute(query, params)

    with pytest.raises(OSError, match="went away"):
        journal.add_screenshot(FailsOnInsert(conn), tmp_path, trade_ids[0], "a.png", PNG)
    assert list(tmp_path.iterdir()) == []


def test_journal_summary(conn, trade_ids, tmp_path):
    journal.set_notes(conn, trade_ids[0], "<p>good read</p>")
    journal.add_screenshot(conn, tmp_path, trade_ids[1], "a.png", PNG)
    journal.add_screenshot(conn, tmp_path, trade_ids[1], "b.png", PNG)
    assert journal.journal_summary(conn) == {trade_ids[0]: (True, 0), trade_ids[1]: (False, 2)}


def test_the_account_a_trade_belongs_to_is_enforced(conn):
    """A tag cannot be attached to a trade through a foreign key from another table's id space."""
    account = accounts.create_account(conn, "Eval")
    [trade] = make_trades(conn, account.id, 1)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO trade_tags (trade_id, tag_id) VALUES (%s, %s)", (trade, 999_999))
