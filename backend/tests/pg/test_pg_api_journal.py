"""Writing a trade's notes and tags through the API, against a real Postgres: what is stored, who may, what is refused."""

from __future__ import annotations

from datetime import date

import pytest
from pg_helpers import add_trade, make_account
from pg_helpers import api_client as api

from tradejournal.pg import journal as pg_journal

TOO_BIG = 9_999_999_999_999_999_999  # more than a Postgres bigint holds


@pytest.fixture
def journal(db, alice):
    """Alice's trade and her tag catalog: the default groups plus one tag of hers."""
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
        add_trade(conn, account.id, date(2024, 3, 18), "100.00")
        add_trade(conn, account.id, date(2024, 3, 18), "-40.00", nth=5)
        trade, other = [r["id"] for r in conn.execute("SELECT id FROM trades ORDER BY id")]
        groups = {g.name: g.id for g in pg_journal.tag_groups(conn)}
        tag = pg_journal.get_or_create_tag(conn, groups["Setup"], "Opening drive")
    return {"trade": trade, "other_trade": other, "groups": groups, "tag": tag.id}


def detail(client, trade_id):
    return client.get(f"/api/v1/trades/{trade_id}").json()


# ---- notes ----------------------------------------------------------------------------------------------------------


def test_a_note_is_stored_and_shown_on_the_trade_and_in_the_log(db, alice, journal):
    client = api(db, alice)
    saved = client.put(f"/api/v1/trades/{journal['trade']}/notes", json={"html": "  <p>Faded the open.</p>  "})
    assert saved.status_code == 200 and saved.json() == {"notes_html": "<p>Faded the open.</p>"}
    assert detail(client, journal["trade"])["notes_html"] == "<p>Faded the open.</p>"
    rows = {r["id"]: r for r in client.get("/api/v1/trades").json()["items"]}
    assert rows[journal["trade"]]["has_notes"] is True and rows[journal["other_trade"]]["has_notes"] is False


def test_a_note_with_no_visible_text_is_stored_as_empty(db, alice, journal):
    client = api(db, alice)
    client.put(f"/api/v1/trades/{journal['trade']}/notes", json={"html": "<p>x</p>"})
    for blank in ("", "<p></p>", "<p><br></p>", " <br> "):
        saved = client.put(f"/api/v1/trades/{journal['trade']}/notes", json={"html": blank})
        assert saved.json() == {"notes_html": ""}, blank
    assert detail(client, journal["trade"])["has_notes"] is False


def test_notes_have_a_size_limit_and_must_be_text(db, alice, journal):
    client = api(db, alice)
    url = f"/api/v1/trades/{journal['trade']}/notes"
    assert client.put(url, json={"html": "x" * 100_000}).status_code == 200
    assert client.put(url, json={"html": "x" * 100_001}).status_code == 422
    assert client.put(url, json={"html": 5}).status_code == 422
    assert client.put(url, json={}).status_code == 422
    assert client.put(url, json={"html": "x", "extra": 1}).status_code == 422  # a misspelled field is an error


def test_another_users_trade_and_a_missing_one_are_the_same_404_and_nothing_is_written(db, alice, bob, journal):
    client = api(db, alice)
    client.put(f"/api/v1/trades/{journal['trade']}/notes", json={"html": "<p>mine</p>"})
    theirs = api(db, bob).put(f"/api/v1/trades/{journal['trade']}/notes", json={"html": "<p>hijacked</p>"})
    assert theirs.status_code == 404 and theirs.headers["content-type"] == "application/problem+json"
    assert api(db, bob).put("/api/v1/trades/987654/notes", json={"html": "x"}).status_code == 404
    assert detail(client, journal["trade"])["notes_html"] == "<p>mine</p>"


# ---- tagging a trade ------------------------------------------------------------------------------------------------


def test_a_tag_goes_on_and_comes_off_and_both_are_safe_to_repeat(db, alice, journal):
    client = api(db, alice)
    url = f"/api/v1/trades/{journal['trade']}/tags/{journal['tag']}"
    for _ in range(2):  # twice: the second is a no-op, not an error
        on = client.put(url)
        assert on.status_code == 200 and [t["name"] for t in on.json()] == ["Opening drive"]
    assert [t["name"] for t in detail(client, journal["trade"])["tags"]] == ["Opening drive"]
    catalog = {t["name"]: t["trade_count"] for t in client.get("/api/v1/tags").json()["tags"]}
    assert catalog["Opening drive"] == 1
    for _ in range(2):
        off = client.delete(url)
        assert off.status_code == 200 and off.json() == []
    assert detail(client, journal["trade"])["tags"] == []
    assert {t["name"]: t["trade_count"] for t in client.get("/api/v1/tags").json()["tags"]}["Opening drive"] == 0


def test_tagging_needs_a_trade_and_a_tag_that_are_the_persons_own(db, alice, bob, journal):
    client = api(db, alice)
    with db.tenant_session(bob) as conn:
        bob_trade_account = make_account(conn, "Bob account")
        add_trade(conn, bob_trade_account.id, date(2024, 3, 20), "5.00")
        bob_trade = conn.execute("SELECT id FROM trades").fetchone()["id"]
        bob_group = pg_journal.tag_groups(conn)[0].id
        bob_tag = pg_journal.get_or_create_tag(conn, bob_group, "Bob only").id
    for method in (client.put, client.delete):
        assert method(f"/api/v1/trades/987654/tags/{journal['tag']}").status_code == 404  # no such trade
        assert method(f"/api/v1/trades/{journal['trade']}/tags/987654").status_code == 404  # no such tag
        assert method(f"/api/v1/trades/{bob_trade}/tags/{journal['tag']}").status_code == 404  # his trade
        assert method(f"/api/v1/trades/{journal['trade']}/tags/{bob_tag}").status_code == 404  # his tag
    assert detail(client, journal["trade"])["tags"] == []  # nothing happened, and no database error leaked out


# ---- making a tag ---------------------------------------------------------------------------------------------------


def test_a_new_tag_gets_a_color_and_the_same_name_in_any_case_is_the_same_tag(db, alice, journal):
    client = api(db, alice)
    made = client.post("/api/v1/tags", json={"group_id": journal["groups"]["Mistake"], "name": "  Chased   the move "})
    assert made.status_code == 201
    tag = made.json()
    assert (
        tag["name"] == "Chased the move"
        and tag["color"].startswith("#")
        and tag["group_id"] == journal["groups"]["Mistake"]
    )
    again = client.post("/api/v1/tags", json={"group_id": journal["groups"]["Mistake"], "name": "CHASED THE MOVE"})
    assert again.json()["id"] == tag["id"]
    assert tag["id"] in {t["id"] for t in client.get("/api/v1/tags").json()["tags"]}


def test_a_bad_name_or_category_is_refused_in_words(db, alice, bob, journal):
    client = api(db, alice)
    group = journal["groups"]["Setup"]
    blank = client.post("/api/v1/tags", json={"group_id": group, "name": "   "})
    assert blank.status_code == 422 and blank.json()["detail"] == "Name is empty."
    long = client.post("/api/v1/tags", json={"group_id": group, "name": "x" * 150})
    assert long.status_code == 422 and "Keep names under" in long.json()["detail"]
    assert client.post("/api/v1/tags", json={"group_id": group, "name": ""}).status_code == 422
    assert client.post("/api/v1/tags", json={"group_id": 987654, "name": "ok"}).status_code == 404
    with db.tenant_session(bob) as conn:
        bobs_group = pg_journal.tag_groups(conn)[0].id
    assert client.post("/api/v1/tags", json={"group_id": bobs_group, "name": "sneaky"}).status_code == 404
    with db.tenant_session(bob) as conn:
        assert all(t.name != "sneaky" for t in pg_journal.tags(conn))


# ---- the guards every change has --------------------------------------------------------------------------------------


def test_a_change_without_the_csrf_token_or_from_another_site_changes_nothing(db, alice, journal):
    client = api(db, alice)
    trade, tag = journal["trade"], journal["tag"]
    wrong = {"x-csrf-token": "wrong"}
    cross = {"sec-fetch-site": "cross-site"}
    attempts = [
        ("put", f"/api/v1/trades/{trade}/notes", {"html": "<p>x</p>"}),
        ("put", f"/api/v1/trades/{trade}/tags/{tag}", None),
        ("post", "/api/v1/tags", {"group_id": journal["groups"]["Setup"], "name": "Forged"}),
    ]
    for method, url, body in attempts:
        for headers in (wrong, cross):
            response = getattr(client, method)(url, headers=headers, **({"json": body} if body else {}))
            assert response.status_code == 403, (method, url, headers)
    after = detail(client, trade)
    assert after["notes_html"] == "" and after["tags"] == []
    assert all(t["name"] != "Forged" for t in client.get("/api/v1/tags").json()["tags"])


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", f"/api/v1/trades/{TOO_BIG}", None),
        ("get", f"/api/v1/trades/{TOO_BIG}/neighbors", None),
        ("put", f"/api/v1/trades/{TOO_BIG}/notes", {"html": "x"}),
        ("put", f"/api/v1/trades/1/tags/{TOO_BIG}", None),
        ("delete", f"/api/v1/trades/{TOO_BIG}/tags/1", None),
        ("get", f"/api/v1/dashboard?account_ids={TOO_BIG}", None),
        ("post", "/api/v1/tags", {"group_id": TOO_BIG, "name": "x"}),
        ("get", "/api/v1/trades/0", None),
    ],
)
def test_an_id_too_big_for_the_database_is_a_bad_request_not_a_server_error(db, alice, journal, method, path, body):
    response = getattr(api(db, alice), method)(path, **({"json": body} if body else {}))
    assert response.status_code == 422, (path, response.status_code)


def test_a_note_is_cleaned_on_its_way_in_and_what_is_stored_is_what_comes_back(db, alice, journal):
    client = api(db, alice)
    dirty = '<p>Faded <b>the</b> open</p><script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:alert(1)">x</a>'
    saved = client.put(f"/api/v1/trades/{journal['trade']}/notes", json={"html": dirty})
    assert saved.status_code == 200
    stored = saved.json()["notes_html"]
    assert stored == '<p>Faded <b>the</b> open</p><a rel="noopener noreferrer">x</a>'
    assert detail(client, journal["trade"])["notes_html"] == stored  # the page shows exactly what was kept
    rows = {r["id"]: r for r in client.get("/api/v1/trades").json()["items"]}
    assert rows[journal["trade"]]["has_notes"] is True
    only_script = client.put(f"/api/v1/trades/{journal['trade']}/notes", json={"html": "<script>alert(1)</script>"})
    assert only_script.json() == {"notes_html": ""}  # nothing visible was left, so there is no note
    assert detail(client, journal["trade"])["has_notes"] is False
