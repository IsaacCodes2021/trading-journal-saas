"""Managing tag categories and tags through the API (rename, recolour, move, merge, delete), against a real Postgres."""

from __future__ import annotations

from datetime import date

import pytest
from pg_helpers import add_trade, make_account
from pg_helpers import api_client as api

from tradejournal.pg import journal as pg_journal

TOO_BIG = 9_999_999_999_999_999_999


@pytest.fixture
def world(db, alice):
    """Alice's two trades and tags: 'Opening drive' and 'ORB' (Setup), 'FOMO' (Emotion); trade 1 has Opening drive and
    ORB, trade 2 has ORB."""
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
        add_trade(conn, account.id, date(2024, 3, 18), "10.00")
        add_trade(conn, account.id, date(2024, 3, 18), "-5.00", nth=5)
        trade_one, trade_two = [r["id"] for r in conn.execute("SELECT id FROM trades ORDER BY id")]
        groups = {g.name: g.id for g in pg_journal.tag_groups(conn)}
        drive = pg_journal.get_or_create_tag(conn, groups["Setup"], "Opening drive")
        orb = pg_journal.get_or_create_tag(conn, groups["Setup"], "ORB")
        fomo = pg_journal.get_or_create_tag(conn, groups["Emotion"], "FOMO")
        pg_journal.add_trade_tag(conn, trade_one, drive.id)
        pg_journal.add_trade_tag(conn, trade_one, orb.id)
        pg_journal.add_trade_tag(conn, trade_two, orb.id)
    return {"t1": trade_one, "t2": trade_two, "groups": groups, "drive": drive.id, "orb": orb.id, "fomo": fomo.id}


def catalog(client) -> dict:
    return client.get("/api/v1/tags").json()


def tag(client, tag_id) -> dict | None:
    return next((t for t in catalog(client)["tags"] if t["id"] == tag_id), None)


def tags_of(client, trade_id) -> list[str]:
    return [t["name"] for t in client.get(f"/api/v1/trades/{trade_id}").json()["tags"]]


# ---- the catalog ----------------------------------------------------------------------------------------------------


def test_the_catalog_offers_the_preset_colours_and_the_custom_ones_already_in_use(db, alice, world):
    client = api(db, alice)
    body = catalog(client)
    assert {"Blue", "Violet", "Green"} <= {p["name"] for p in body["presets"]} and body["custom_colors"] == []
    assert all(p["color"].startswith("#") for p in body["presets"])
    client.put(
        f"/api/v1/tags/{world['fomo']}", json={"name": "FOMO", "group_id": world["groups"]["Emotion"], "color": "#ABC"}
    )
    assert catalog(client)["custom_colors"] == ["#aabbcc"]  # "#ABC" is stored as "#aabbcc", and offered for reuse


# ---- categories -----------------------------------------------------------------------------------------------------


def test_a_category_is_added_renamed_and_deleted_with_its_tags_but_not_its_trades(db, alice, world):
    client = api(db, alice)
    made = client.post("/api/v1/tag-groups", json={"name": "  Market   regime "})
    assert made.status_code == 201
    names = [g["name"] for g in made.json()["groups"]]
    assert names[-1] == "Market regime" and len(names) == 4  # trimmed, collapsed, and added last
    group_id = made.json()["groups"][-1]["id"]
    renamed = client.put(f"/api/v1/tag-groups/{group_id}", json={"name": "Regime"}).json()
    assert "Regime" in [g["name"] for g in renamed["groups"]]
    # Deleting a category takes its tags and their links, and leaves the trades.
    gone = client.delete(f"/api/v1/tag-groups/{world['groups']['Setup']}")
    assert gone.status_code == 200
    left = gone.json()
    assert world["drive"] not in [t["id"] for t in left["tags"]] and "Setup" not in [g["name"] for g in left["groups"]]
    assert tags_of(client, world["t1"]) == [] and client.get("/api/v1/trades").json()["total"] == 2


@pytest.mark.parametrize(("name", "status"), [("setup", 409), ("  SETUP ", 409), ("", 422), ("   ", 422)])
def test_a_category_name_must_be_new_and_not_blank(db, alice, world, name, status):
    client = api(db, alice)
    before = len(catalog(client)["groups"])
    response = client.post("/api/v1/tag-groups", json={"name": name})
    assert response.status_code == status and response.headers["content-type"] == "application/problem+json"
    assert len(catalog(client)["groups"]) == before


def test_a_category_name_messages_are_in_words_and_a_rename_may_only_change_capitalization(db, alice, world):
    client = api(db, alice)
    clash = client.post("/api/v1/tag-groups", json={"name": "setup"})
    assert clash.json()["detail"] == "There is already a category called setup."
    assert client.post("/api/v1/tag-groups", json={"name": "x" * 150}).json()["detail"].startswith("Keep names under")
    own = client.put(f"/api/v1/tag-groups/{world['groups']['Setup']}", json={"name": "SETUP"})
    assert own.status_code == 200 and "SETUP" in [g["name"] for g in own.json()["groups"]]
    taken = client.put(f"/api/v1/tag-groups/{world['groups']['Setup']}", json={"name": "mistake"})
    assert taken.status_code == 409


# ---- tags -------------------------------------------------------------------------------------------------------------


def test_a_tag_is_renamed_recoloured_and_moved_and_its_trades_keep_it(db, alice, world):
    client = api(db, alice)
    edit = client.put(
        f"/api/v1/tags/{world['drive']}",
        json={"name": "Gap fade", "group_id": world["groups"]["Mistake"], "color": "#D03B3B"},
    )
    assert edit.status_code == 200
    changed = next(t for t in edit.json()["tags"] if t["id"] == world["drive"])
    assert (changed["name"], changed["group_id"], changed["color"], changed["trade_count"]) == (
        "Gap fade",
        world["groups"]["Mistake"],
        "#d03b3b",
        1,
    )
    assert "Gap fade" in tags_of(client, world["t1"]) and "Opening drive" not in tags_of(client, world["t1"])
    # Saving a tag with its own name (say, only a new colour) is not a clash with itself.
    assert (
        client.put(
            f"/api/v1/tags/{world['orb']}",
            json={"name": "orb", "group_id": world["groups"]["Setup"], "color": "#2a78d6"},
        ).status_code
        == 200
    )


def test_a_tag_cannot_take_a_name_already_used_in_its_category_and_a_bad_colour_is_refused_in_words(db, alice, world):
    client = api(db, alice)
    setup = world["groups"]["Setup"]
    clash = client.put(f"/api/v1/tags/{world['drive']}", json={"name": "orb", "group_id": setup, "color": "#2a78d6"})
    assert clash.status_code == 409 and clash.json()["detail"] == "There is already a tag called ORB."
    assert tag(client, world["drive"])["name"] == "Opening drive"
    for color in ("blue", "#12", "#gggggg", ""):
        refused = client.put(
            f"/api/v1/tags/{world['drive']}", json={"name": "Opening drive", "group_id": setup, "color": color}
        )
        assert refused.status_code == 422, color
    blank = client.put(f"/api/v1/tags/{world['drive']}", json={"name": "  ", "group_id": setup, "color": "#2a78d6"})
    assert blank.status_code == 422 and blank.json()["detail"] == "Name is empty."


def test_merging_moves_every_trade_to_the_remaining_tag_once(db, alice, world):
    client = api(db, alice)
    merged = client.post(f"/api/v1/tags/{world['drive']}/merge", json={"into_id": world["orb"]})
    assert merged.status_code == 200
    body = merged.json()
    assert world["drive"] not in [t["id"] for t in body["tags"]]
    assert next(t for t in body["tags"] if t["id"] == world["orb"])["trade_count"] == 2
    assert tags_of(client, world["t1"]) == ["ORB"]  # it carried both, and now has the survivor once
    assert tags_of(client, world["t2"]) == ["ORB"]
    again = client.post(f"/api/v1/tags/{world['orb']}/merge", json={"into_id": world["orb"]})
    assert again.status_code == 422 and "itself" in again.json()["detail"]


def test_deleting_a_tag_removes_it_from_its_trades_and_nothing_else(db, alice, world):
    client = api(db, alice)
    body = client.delete(f"/api/v1/tags/{world['orb']}").json()
    assert world["orb"] not in [t["id"] for t in body["tags"]] and world["drive"] in [t["id"] for t in body["tags"]]
    assert tags_of(client, world["t1"]) == ["Opening drive"] and tags_of(client, world["t2"]) == []


# ---- whose tags, and what is refused ------------------------------------------------------------------------------------


def test_nobody_can_change_another_persons_tags_or_categories(db, alice, bob, world):
    with db.tenant_session(bob) as conn:
        bobs_group = pg_journal.tag_groups(conn)[0].id
        bobs_tag = pg_journal.get_or_create_tag(conn, bobs_group, "Bob only").id
    as_bob = api(db, bob)
    attempts = [
        ("put", f"/api/v1/tag-groups/{world['groups']['Setup']}", {"name": "Hijacked"}),
        ("delete", f"/api/v1/tag-groups/{world['groups']['Setup']}", None),
        ("put", f"/api/v1/tags/{world['orb']}", {"name": "x", "group_id": bobs_group, "color": "#2a78d6"}),
        ("post", f"/api/v1/tags/{world['orb']}/merge", {"into_id": bobs_tag}),
        ("post", f"/api/v1/tags/{bobs_tag}/merge", {"into_id": world["orb"]}),
        ("delete", f"/api/v1/tags/{world['orb']}", None),
    ]
    for method, url, body in attempts:
        response = getattr(as_bob, method)(url, **({"json": body} if body is not None else {}))
        assert response.status_code == 404, (method, url)
    # Bob moving his own tag into alice's category is refused too (her category looks like none to him).
    into_hers = as_bob.put(
        f"/api/v1/tags/{bobs_tag}", json={"name": "Bob only", "group_id": world["groups"]["Setup"], "color": "#2a78d6"}
    )
    assert into_hers.status_code == 404
    mine = catalog(api(db, alice))
    assert {t["name"] for t in mine["tags"]} == {"Opening drive", "ORB", "FOMO"} and len(mine["groups"]) == 3
    assert [t["name"] for t in catalog(as_bob)["tags"]] == ["Bob only"]


def test_a_change_without_the_csrf_token_changes_nothing(db, alice, world):
    client = api(db, alice)
    forged = {"x-csrf-token": "wrong"}
    assert client.delete(f"/api/v1/tags/{world['orb']}", headers=forged).status_code == 403
    assert client.post("/api/v1/tag-groups", json={"name": "Forged"}, headers=forged).status_code == 403
    after = catalog(client)
    assert world["orb"] in [t["id"] for t in after["tags"]] and len(after["groups"]) == 3


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", f"/api/v1/tag-groups/{TOO_BIG}", {"name": "x"}),
        ("delete", f"/api/v1/tag-groups/{TOO_BIG}", None),
        ("put", f"/api/v1/tags/{TOO_BIG}", {"name": "x", "group_id": 1, "color": "#2a78d6"}),
        ("put", "/api/v1/tags/1", {"name": "x", "group_id": TOO_BIG, "color": "#2a78d6"}),
        ("post", f"/api/v1/tags/{TOO_BIG}/merge", {"into_id": 1}),
        ("post", "/api/v1/tags/1/merge", {"into_id": TOO_BIG}),
        ("delete", f"/api/v1/tags/{TOO_BIG}", None),
    ],
)
def test_an_id_too_big_for_the_database_is_a_bad_request(db, alice, world, method, path, body):
    response = getattr(api(db, alice), method)(path, **({"json": body} if body is not None else {}))
    assert response.status_code == 422, (path, response.status_code)
