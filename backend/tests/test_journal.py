import pytest

from helpers import SAMPLE, fixture_bytes
from tradejournal import db, importer, journal


@pytest.fixture
def trade_ids(conn, account):
    importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
    return [row["id"] for row in conn.execute("SELECT id FROM trades ORDER BY entry_at")]


def group_id(conn, name):
    return next(g.id for g in journal.tag_groups(conn) if g.name == name)


def test_default_groups(conn):
    assert [g.name for g in journal.tag_groups(conn)] == ["Setup", "Mistake", "Emotion"]


def test_tagging_a_trade(conn, trade_ids):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
        tags = journal.set_trade_tags(conn, trade_ids[0], setup, ["scalp", "  momentum  "])
    assert [t.name for t in tags] == ["scalp", "momentum"]
    assert [t.name for t in journal.trade_tags(conn, trade_ids[0])] == ["momentum", "scalp"]


def test_names_match_regardless_of_capitalization(conn, trade_ids):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
        journal.set_trade_tags(conn, trade_ids[0], setup, ["Scalp"])
        again = journal.set_trade_tags(conn, trade_ids[1], setup, ["SCALP", "scalp"])
    assert [t.name for t in again] == ["Scalp"]  # the stored spelling, once
    assert [(t.name, t.trade_count) for t in journal.tags(conn)] == [("Scalp", 2)]


def test_setting_one_group_leaves_other_groups_alone(conn, trade_ids):
    setup, mistake = group_id(conn, "Setup"), group_id(conn, "Mistake")
    trade = trade_ids[0]
    with db.transaction(conn):
        journal.set_trade_tags(conn, trade, setup, ["scalp"])
        journal.set_trade_tags(conn, trade, mistake, ["chased entry"])
        journal.set_trade_tags(conn, trade, setup, ["momentum"])  # replaces scalp only
    assert [t.name for t in journal.trade_tags(conn, trade)] == ["momentum", "chased entry"]
    with db.transaction(conn):
        journal.set_trade_tags(conn, trade, setup, [])
    assert [t.name for t in journal.trade_tags(conn, trade)] == ["chased entry"]
    # "scalp" is unused now but stays available for other trades.
    assert {t.name: t.trade_count for t in journal.tags(conn)}["scalp"] == 0


def test_tags_by_trade(conn, trade_ids):
    setup, emotion = group_id(conn, "Setup"), group_id(conn, "Emotion")
    with db.transaction(conn):
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
    with db.transaction(conn):
        scalp = journal.get_or_create_tag(conn, setup, "scalp")
        journal.add_trade_tag(conn, trade_ids[0], scalp.id)
    # The tag list is global: any other trade can pick it.
    assert [t.name for t in journal.tags(conn)] == ["scalp"]
    with db.transaction(conn):
        journal.add_trade_tag(conn, trade_ids[3], scalp.id)
        journal.add_trade_tag(conn, trade_ids[3], scalp.id)  # adding twice is harmless
    assert {t.name: t.trade_count for t in journal.tags(conn)} == {"scalp": 2}

    with db.transaction(conn):
        journal.remove_trade_tag(conn, trade_ids[0], scalp.id)
    assert journal.trade_tags(conn, trade_ids[0]) == []
    assert [t.name for t in journal.trade_tags(conn, trade_ids[3])] == ["scalp"]  # untouched
    assert {t.name: t.trade_count for t in journal.tags(conn)} == {"scalp": 1}


def test_deleting_tags_and_groups(conn, trade_ids):
    setup = group_id(conn, "Setup")
    with db.transaction(conn):
        [scalp] = journal.set_trade_tags(conn, trade_ids[0], setup, ["scalp"])
        journal.delete_tag(conn, scalp.id)
    assert journal.trade_tags(conn, trade_ids[0]) == []

    with db.transaction(conn):
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


def test_tags_survive_an_overlapping_import(conn, account):
    """A trade extended by a later export keeps its tags (store.merge_journal)."""
    from helpers import fixture_lines

    importer.import_files(conn, account.id, [("morning.csv", fixture_lines(SAMPLE, *range(2, 15)))])
    last = conn.execute("SELECT id FROM trades ORDER BY entry_at DESC LIMIT 1").fetchone()["id"]
    with db.transaction(conn):
        journal.set_trade_tags(conn, last, group_id(conn, "Setup"), ["scale out"])
    importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
    assert [t.name for t in journal.trade_tags(conn, last)] == ["scale out"]
