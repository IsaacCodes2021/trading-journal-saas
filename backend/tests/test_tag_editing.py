"""Editing tags from Settings: rename, recolor, move between categories, merge."""

import sqlite3

import pytest

from helpers import SAMPLE, fixture_bytes
from tradejournal import db, importer, journal
from tradejournal.ui import theme

PRESETS = list(journal.TAG_COLOR_PRESETS)


@pytest.fixture
def trade_ids(conn, account):
    importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
    return [row["id"] for row in conn.execute("SELECT id FROM trades ORDER BY entry_at")]


def group_id(conn, name):
    return next(g.id for g in journal.tag_groups(conn) if g.name == name)


def counts(conn):
    return {t.name: t.trade_count for t in journal.tags(conn)}


def test_new_tags_take_the_least_used_preset_color(conn):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
        first = journal.get_or_create_tag(conn, setup, "scalp")
        second = journal.get_or_create_tag(conn, setup, "momentum")
    assert (first.color, second.color) == (PRESETS[0], PRESETS[1])

    with db.transaction(conn):
        journal.update_tag(conn, first.id, name="scalp", group_id=setup, color=PRESETS[2])
        third = journal.get_or_create_tag(conn, setup, "breakout")
    assert third.color == PRESETS[0]  # free again now that scalp moved off it
    assert journal.get_or_create_tag(conn, setup, "SCALP").color == PRESETS[2]  # existing tag, unchanged


def test_colors_are_normalized_and_checked():
    assert journal.clean_color("#2A78D6") == "#2a78d6"
    assert journal.clean_color(" 2a78d6 ") == "#2a78d6"
    assert journal.clean_color("#AbC") == "#aabbcc"
    for bad in ("", "red", "#12345", "#gg0000", "rgb(1, 2, 3)"):
        with pytest.raises(ValueError):
            journal.clean_color(bad)


def test_the_database_only_stores_hex_colors(conn):
    setup = group_id(conn, "Setup")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tags (group_id, name, color) VALUES (?, 'x', 'red')", (setup,))


def test_renaming_recoloring_and_moving_keep_the_trades(conn, trade_ids):
    setup, mistake = group_id(conn, "Setup"), group_id(conn, "Mistake")
    with db.transaction(conn):
        [tag] = journal.set_trade_tags(conn, trade_ids[0], setup, ["chased"])
        journal.add_trade_tag(conn, trade_ids[1], tag.id)
        edited = journal.update_tag(conn, tag.id, name="  Chased   entry ", group_id=mistake, color="#FF00AA")
    assert edited == journal.Tag(tag.id, mistake, "Chased entry", "#ff00aa")
    [after] = journal.tags(conn)
    assert (after.name, after.group_id, after.color, after.trade_count) == ("Chased entry", mistake, "#ff00aa", 2)
    assert journal.trade_tags(conn, trade_ids[1]) == [journal.Tag(tag.id, mistake, "Chased entry", "#ff00aa")]


def test_changing_only_capitalization_is_a_rename(conn):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
        tag = journal.get_or_create_tag(conn, setup, "scalp")
        journal.update_tag(conn, tag.id, name="Scalp", group_id=setup, color=tag.color)
    assert [t.name for t in journal.tags(conn)] == ["Scalp"]


def test_renaming_onto_another_tag_is_refused(conn):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
        journal.get_or_create_tag(conn, setup, "Scalp")
        other = journal.get_or_create_tag(conn, setup, "quick")
    with pytest.raises(ValueError, match="already a tag called 'Scalp'"):
        journal.update_tag(conn, other.id, name="scalp", group_id=setup, color=other.color)
    # The same name in another category is a different tag.
    with db.transaction(conn):
        journal.update_tag(conn, other.id, name="scalp", group_id=group_id(conn, "Mistake"), color=other.color)
    assert len(journal.tags(conn)) == 2


def test_merging_moves_every_trade_once(conn, trade_ids):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
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
    with db.transaction(conn):
        tag = journal.get_or_create_tag(conn, setup, "Opening drive")
    assert journal.find_tag(conn, setup, "  opening   DRIVE ") == tag
    assert journal.find_tag(conn, group_id(conn, "Mistake"), "Opening drive") is None


def test_renaming_categories(conn):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
        renamed = journal.rename_group(conn, setup, " Setups ")
        journal.rename_group(conn, setup, "setups")  # capitalization only
    assert renamed.name == "Setups"
    assert [g.name for g in journal.tag_groups(conn)] == ["setups", "Mistake", "Emotion"]
    with pytest.raises(ValueError, match="already a category"):
        journal.rename_group(conn, setup, "MISTAKE")


def test_your_colors_are_the_custom_ones_in_use(conn):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
        a = journal.get_or_create_tag(conn, setup, "a")
        b = journal.get_or_create_tag(conn, setup, "b")
        c = journal.get_or_create_tag(conn, setup, "c")
        journal.update_tag(conn, a.id, name="a", group_id=setup, color="#123456")
        journal.update_tag(conn, b.id, name="b", group_id=setup, color="#fedcba")
        journal.update_tag(conn, c.id, name="c", group_id=setup, color="#123456")
    assert journal.custom_colors(conn) == ["#123456", "#fedcba"]  # presets and repeats left out
    with db.transaction(conn):
        journal.delete_tag(conn, b.id)
    assert journal.custom_colors(conn) == ["#123456"]


def test_upgrade_keeps_each_tags_old_color(tmp_path):
    """Before v6 a tag's color followed its id through the first eight presets."""
    path = tmp_path / "journal.db"
    old = sqlite3.connect(path, isolation_level=None)
    old.executescript("BEGIN;\n" + "\n".join(db.MIGRATIONS[:5]) + "\nPRAGMA user_version = 5;\nCOMMIT;")
    for tag_id in range(1, 11):
        old.execute("INSERT INTO tags (id, group_id, name) VALUES (?, 1, ?)", (tag_id, f"tag {tag_id}"))
    old.close()

    conn = db.connect(path)
    colors = {t.id: t.color for t in journal.tags(conn)}
    assert colors == {tag_id: PRESETS[(tag_id - 1) % 8] for tag_id in range(1, 11)}
    conn.close()


# -- pill colors --------------------------------------------------------------------


@pytest.mark.parametrize(
    "color", PRESETS + ["#000000", "#ffffff", "#ffff00", "#00ffff", "#7f7f7f", "#123456", "#ff00aa"]
)
def test_tag_text_is_readable_in_both_schemes(color):
    light, dark = theme.tag_text_colors(color)
    for scheme, text in (("light", light), ("dark", dark)):
        for background in theme.pill_backgrounds(color, scheme):
            assert theme.contrast(theme._rgb(text), background) >= theme.MIN_TEXT_CONTRAST, (color, scheme)


def test_tag_style_sets_both_schemes():
    style = theme.tag_style("#2a78d6")
    assert "--tj-tag-fg: #1c5cab" in style and "--tj-tag-fg-dark: #86b6ef" in style
    assert "--tj-tag-bg: rgba(42, 120, 214, 0.14)" in style
    assert "--tj-tag-bg-dark: rgba(42, 120, 214, 0.24)" in style


def test_check_marks_contrast_with_their_swatch():
    assert theme.on_color("#ffff00") == "#000000"
    assert theme.on_color("#2a2a2a") == "#ffffff"
