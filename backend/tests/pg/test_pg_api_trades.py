"""The trade log, one trade's page and the tag catalog against a real Postgres.

The trades come from importing the real sample export (8 trades from 14 fill pairs), so the fill pairs, the grouping
and the fees are what the importer really produces, not hand-made rows.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from helpers import SAMPLE, fixture_bytes
from pg_helpers import api_client as api
from pg_helpers import make_account

from tradejournal.pg import accounts, importer
from tradejournal.pg import journal as pg_journal

SORTS = ["date", "account", "symbol", "direction", "qty", "hold", "net_pnl"]


@pytest.fixture
def imported(db, alice):
    """Alice's Apex account with the sample export imported, and a note, a tag and a screenshot on one trade."""
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
        accounts.set_fee(conn, account.id, "*", Decimal("4.00"))
        importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
        first = conn.execute("SELECT id FROM trades ORDER BY entry_at, id LIMIT 1").fetchone()["id"]
        group = next(g for g in pg_journal.tag_groups(conn) if g.name == "Setup")
        pg_journal.set_notes(conn, first, "<p>Faded the open.</p>")
        pg_journal.set_trade_tags(conn, first, group.id, ["Opening drive"])
        conn.execute(
            "INSERT INTO screenshots (trade_id, stored_name, original_name) VALUES (%s, 'abc.png', 'chart.png')",
            (first,),
        )
    return {"account": account.id, "journaled": first}


def listing(client, **params):
    response = client.get("/api/v1/trades", params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    return response.json()


# ---- the log ---------------------------------------------------------------------------------------------------


def test_the_log_lists_the_trades_with_the_total_and_net_of_the_whole_list(db, alice, imported):
    page = listing(api(db, alice), limit=3, sort="date")
    assert page["total"] == 8 and page["net_pnl"] == "835.00"
    assert len(page["items"]) == 3
    row = page["items"][0]
    assert row["account"] == {
        "id": imported["account"],
        "name": "Apex 07",
        "firm": "Apex",
        "account_type": "evaluation",
    }
    assert row["trade_day"] == "2024-01-05" and row["symbol_root"] and row["direction"] in ("long", "short")
    assert row["entry_at"].endswith("Z") and row["exit_at"].endswith("Z")
    for money in ("gross_pnl", "fees", "net_pnl", "avg_entry", "avg_exit"):
        assert isinstance(row[money], str), money  # an exact decimal string, never a float
    assert Decimal(row["gross_pnl"]) - Decimal(row["fees"]) == Decimal(row["net_pnl"])


def test_pages_cover_every_trade_once_in_the_requested_order(db, alice, imported):
    client = api(db, alice)
    everything = [row["id"] for row in listing(client, sort="net_pnl")["items"]]
    paged: list[int] = []
    for offset in range(0, 8, 3):
        paged += [row["id"] for row in listing(client, sort="net_pnl", limit=3, offset=offset)["items"]]
    assert paged == everything and len(set(paged)) == 8
    assert listing(client, limit=3, offset=9)["items"] == []  # past the end is empty, not an error


@pytest.mark.parametrize("field", SORTS)
def test_every_sort_orders_the_list_and_its_reverse_is_the_exact_opposite(db, alice, imported, field):
    client = api(db, alice)
    up = listing(client, sort=field)["items"]
    down = listing(client, sort=f"-{field}")["items"]
    assert [r["id"] for r in down] == [r["id"] for r in reversed(up)]
    keys = {
        "date": lambda r: r["entry_at"],
        "account": lambda r: r["account"]["name"].casefold(),
        "symbol": lambda r: r["symbol"],
        "direction": lambda r: r["direction"],
        "qty": lambda r: r["total_qty"],
        "hold": lambda r: r["hold_seconds"],
        "net_pnl": lambda r: Decimal(r["net_pnl"]),
    }
    values = [keys[field](r) for r in up]
    assert values == sorted(values)


def test_the_default_order_is_newest_first(db, alice, imported):
    default = listing(api(db, alice))["items"]
    assert [r["entry_at"] for r in default] == sorted((r["entry_at"] for r in default), reverse=True)


def test_search_matches_the_contract_the_account_and_tags_ignoring_case_and_follows_the_totals(db, alice, imported):
    client = api(db, alice)
    root = listing(client)["items"][0]["symbol_root"]
    by_root = listing(client, q=root.lower())
    assert by_root["total"] == len(by_root["items"]) >= 1 and all(r["symbol_root"] == root for r in by_root["items"])
    assert Decimal(by_root["net_pnl"]) == sum(Decimal(r["net_pnl"]) for r in by_root["items"])
    assert listing(client, q="apex")["total"] == 8  # the account's name
    tagged = listing(client, q="OPENING DRIVE")
    assert [r["id"] for r in tagged["items"]] == [imported["journaled"]]  # a tag's name
    assert listing(client, q="no such thing")["total"] == 0
    assert listing(client, q="no such thing")["net_pnl"] == "0.00"


def test_rows_say_which_trades_have_notes_tags_and_screenshots(db, alice, imported):
    rows = {r["id"]: r for r in listing(api(db, alice))["items"]}
    mine = rows[imported["journaled"]]
    assert mine["has_notes"] is True and mine["screenshot_count"] == 1
    assert [t["name"] for t in mine["tags"]] == ["Opening drive"]
    others = [r for i, r in rows.items() if i != imported["journaled"]]
    assert all(not r["has_notes"] and r["screenshot_count"] == 0 and r["tags"] == [] for r in others)


def test_the_log_takes_the_same_filters_as_the_dashboard(db, alice, imported):
    client = api(db, alice)
    assert listing(client, account_type="funded")["total"] == 0
    assert listing(client, firms=["Apex"])["total"] == 8
    assert listing(client, start="2024-01-06")["total"] == 0
    assert listing(client, direction="long")["total"] + listing(client, direction="short")["total"] == 8


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"sort": "sideways"}, {"sort": "id"}, {"q": "x" * 101}, {"page": 2}],
)
def test_a_bad_page_or_sort_is_a_422_problem(db, alice, params):
    response = api(db, alice).get("/api/v1/trades", params=params)
    assert response.status_code == 422 and response.headers["content-type"] == "application/problem+json"


# ---- one trade -------------------------------------------------------------------------------------------------


def test_a_trades_page_has_its_fill_pairs_notes_tags_and_screenshots(db, alice, imported):
    client = api(db, alice)
    detail = client.get(f"/api/v1/trades/{imported['journaled']}").json()
    assert detail["notes_html"] == "<p>Faded the open.</p>" and detail["has_notes"] is True
    assert [t["name"] for t in detail["tags"]] == ["Opening drive"]
    assert [(s["original_name"], s["src"]) for s in detail["screenshots"]] == [("chart.png", None)]
    assert detail["screenshot_count"] == 1 and detail["fill_pairs"]
    assert sum(Decimal(p["pnl"]) for p in detail["fill_pairs"]) == Decimal(detail["gross_pnl"])
    assert all(p["bought_at"].endswith("Z") and isinstance(p["buy_price"], str) for p in detail["fill_pairs"])
    assert isinstance(detail["points"], str)
    # Every trade's pairs add up to the 14 rows of the export.
    pair_counts = [len(client.get(f"/api/v1/trades/{r['id']}").json()["fill_pairs"]) for r in listing(client)["items"]]
    assert sum(pair_counts) == 14 and max(pair_counts) > 1  # some trades are scaled in or out


def test_a_missing_trade_and_another_users_trade_are_the_same_404(db, alice, bob, imported):
    assert api(db, alice).get("/api/v1/trades/999999").status_code == 404
    theirs = api(db, bob).get(f"/api/v1/trades/{imported['journaled']}")
    assert theirs.status_code == 404 and theirs.headers["content-type"] == "application/problem+json"
    assert api(db, bob).get(f"/api/v1/trades/{imported['journaled']}/neighbors").json() == {
        "previous": None,
        "next": None,
    }


def test_neighbors_follow_the_order_filters_and_search_of_the_list_the_person_came_from(db, alice, imported):
    client = api(db, alice)
    ids = [r["id"] for r in listing(client, sort="net_pnl")["items"]]
    middle = client.get(f"/api/v1/trades/{ids[3]}/neighbors", params={"sort": "net_pnl"}).json()
    assert middle == {"previous": ids[2], "next": ids[4]}
    assert client.get(f"/api/v1/trades/{ids[0]}/neighbors", params={"sort": "net_pnl"}).json()["previous"] is None
    assert client.get(f"/api/v1/trades/{ids[-1]}/neighbors", params={"sort": "net_pnl"}).json()["next"] is None
    reverse = client.get(f"/api/v1/trades/{ids[3]}/neighbors", params={"sort": "-net_pnl"}).json()
    assert reverse == {"previous": ids[4], "next": ids[2]}
    # Outside the list (filtered away) there are no neighbors; inside a search, only its own matches count.
    gone = client.get(f"/api/v1/trades/{ids[3]}/neighbors", params={"q": "no such thing"}).json()
    assert gone == {"previous": None, "next": None}
    only = client.get(f"/api/v1/trades/{imported['journaled']}/neighbors", params={"q": "opening drive"}).json()
    assert only == {"previous": None, "next": None}


# ---- tags -------------------------------------------------------------------------------------------------------


def test_the_tag_catalog_has_the_groups_and_each_tags_trade_count(db, alice, imported):
    catalog = api(db, alice).get("/api/v1/tags").json()
    assert {"Setup", "Mistake", "Emotion"} <= {g["name"] for g in catalog["groups"]}
    [tag] = [t for t in catalog["tags"] if t["name"] == "Opening drive"]
    assert tag["trade_count"] == 1 and tag["color"].startswith("#")
    assert tag["group_id"] in {g["id"] for g in catalog["groups"]}


def test_nothing_of_one_users_journal_reaches_another(db, alice, bob, imported):
    as_bob = api(db, bob)
    assert listing(as_bob)["total"] == 0 and listing(as_bob, q="opening drive")["total"] == 0
    assert all(t["name"] != "Opening drive" for t in as_bob.get("/api/v1/tags").json()["tags"])
