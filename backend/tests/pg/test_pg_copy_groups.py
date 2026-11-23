"""Copy-trading groups against a real Postgres (migration 0026, `docs/new-feature-copy-trading/decisions.csv`).

A leader's trades are stored once; each follower's copies are worked out when read. The clients run at NOON_UTC
(2024-03-22, a Friday), so "today" for a membership change is that trading day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from helpers import SAMPLE, fixture_bytes
from pg_helpers import api_client as api
from pg_helpers import make_account, make_trades

from tradejournal.pg import accounts, copy_groups, importer
from tradejournal.pg.copy_groups import MembershipEvent, Window, windows_of

EARLY = date(2024, 1, 5)  # before "today"
LATE = date(2024, 3, 25)  # after the follower B removal below takes effect
NOW = datetime(2024, 3, 22, 12, tzinfo=UTC)


@pytest.fixture
def world(db, alice):
    """Leader L (2 early trades, 3 late), followers A and B, and a second leader M (1 late trade). Each trade nets 16."""
    with db.tenant_session(alice) as conn:
        leader = make_account(conn, "LEA-123456-01")
        a = make_account(conn, "FOL-A-111")
        b = make_account(conn, "FOL-B-222")
        other = make_account(conn, "OTH-999999-09")
        early = make_trades(conn, leader.id, 2, day=EARLY)
        late = make_trades(conn, leader.id, 3, day=LATE)
        [other_trade] = make_trades(conn, other.id, 1, day=LATE)
    return {"L": leader.id, "A": a.id, "B": b.id, "M": other.id, "early": early, "late": late, "other": other_trade}


def make_group(client, name, leader, followers, **extra):
    body = {"name": name, "leader_account_id": leader, "follower_account_ids": followers, "acknowledge_fills": True}
    response = client.post("/api/v1/copy-groups", json={**body, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def change(client, group_id, kind, account_id, day, note=""):
    path = f"/api/v1/copy-groups/{group_id}/followers" + ("/remove" if kind == "remove" else "")
    return client.post(path, json={"account_id": account_id, "effective_date": day.isoformat(), "note": note})


def dashboard(client, **params):
    response = client.get("/api/v1/dashboard", params=params)
    assert response.status_code == 200, response.text
    return response.json()["summary"]


def trades(client, **params):
    response = client.get("/api/v1/trades", params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    return response.json()


def count(conn, table, where="TRUE", params=()):
    return conn.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]  # noqa: S608


# ---- windows (pure) ----------------------------------------------------------------------------------------------


def event(n, kind, day):
    return MembershipEvent(id=n, account_id=1, event_type=kind, effective_date=day, note="", created_at=NOW)


def test_windows_follow_the_log_and_a_removal_day_no_longer_counts():
    windows = windows_of(
        [event(1, "added", None), event(2, "removed", date(2024, 2, 1)), event(3, "added", date(2024, 3, 1))]
    )
    assert windows == [Window(None, date(2024, 2, 1)), Window(date(2024, 3, 1), None)]
    assert windows[0].covers(date(2023, 1, 1)) and windows[0].covers(date(2024, 1, 31))
    assert not windows[0].covers(date(2024, 2, 1)) and not windows[1].covers(date(2024, 2, 15))
    assert windows[1].covers(date(2024, 3, 1))


# ---- forward-only membership -------------------------------------------------------------------------------------


def test_a_removed_follower_keeps_its_past_copies_and_gets_none_after(db, alice, world):
    client = api(db, alice)
    group = make_group(client, "Main copy", world["L"], [world["A"], world["B"]])
    assert change(client, group["id"], "remove", world["B"], LATE, "blew the account").status_code == 200
    assert dashboard(client, account_ids=[world["B"]])["trade_count"] == 2  # the two early trades only
    assert dashboard(client, account_ids=[world["A"]])["trade_count"] == 5
    detail = client.get(f"/api/v1/trades/{world['late'][0]}").json()
    assert [a["id"] for a in detail["copied_to"]] == [world["A"]]
    detail = client.get(f"/api/v1/trades/{world['early'][0]}").json()
    assert [a["id"] for a in detail["copied_to"]] == [world["A"], world["B"]]
    events = client.get("/api/v1/copy-groups").json()[0]["events"]
    assert [(e["event_type"], e["effective_date"], e["note"]) for e in events][-1] == (
        "removed",
        "2024-03-25",
        "blew the account",
    )


def test_a_change_dated_before_today_is_refused_so_old_trades_keep_their_followers(db, alice, world):
    client = api(db, alice)
    group = make_group(client, "Main copy", world["L"], [world["A"]])
    response = change(client, group["id"], "remove", world["A"], date(2024, 3, 21))
    assert response.status_code == 422 and "today or later" in response.json()["detail"]
    response = change(client, group["id"], "add", world["B"], date(2024, 1, 1))
    assert response.status_code == 422
    assert dashboard(client, account_ids=[world["A"]])["trade_count"] == 5  # unchanged
    assert dashboard(client, account_ids=[world["B"]])["trade_count"] == 0


def test_a_follower_added_later_counts_only_from_its_day(db, alice, world):
    client = api(db, alice)
    group = make_group(client, "Main copy", world["L"], [world["A"]])
    assert change(client, group["id"], "add", world["B"], date(2024, 3, 22)).status_code == 200
    assert dashboard(client, account_ids=[world["B"]])["trade_count"] == 3  # the late trades
    # Removing and adding back is two more events, never an edit of the first.
    assert change(client, group["id"], "remove", world["B"], LATE).status_code == 200
    assert change(client, group["id"], "add", world["B"], LATE).status_code == 200
    assert dashboard(client, account_ids=[world["B"]])["trade_count"] == 3
    assert change(client, group["id"], "add", world["B"], LATE).status_code == 422  # already in


def test_copying_since_leaves_the_earlier_leader_trades_uncopied_and_unlabelled(db, alice, world):
    client = api(db, alice)
    make_group(client, "Main copy", world["L"], [world["A"]], copying_since="2024-02-01")
    assert dashboard(client, account_ids=[world["A"]])["trade_count"] == 3
    rows = {row["id"]: row for row in trades(client, account_ids=[world["L"]])["items"]}
    assert rows[world["early"][0]]["copy_group"] is None
    assert rows[world["late"][0]]["copy_group"]["name"] == "Main copy"


# ---- several groups, cumulative P&L --------------------------------------------------------------------------------


def test_an_account_in_two_groups_stacks_both_leaders_and_is_flagged(db, alice, world):
    client = api(db, alice)
    main = make_group(client, "Main copy", world["L"], [world["A"], world["B"]])
    side = make_group(client, "Side copy", world["M"], [world["A"]])
    summary = dashboard(client, account_ids=[world["A"]])
    assert summary["trade_count"] == 6 and summary["net_pnl"] == "96.00"  # 5 from L and 1 from M, 16 each
    groups = {g["name"]: g for g in client.get("/api/v1/copy-groups").json()}
    members = {m["account"]["id"]: m for m in groups["Main copy"]["members"]}
    assert members[world["A"]]["other_groups"] == [{"id": side["id"], "name": "Side copy"}]
    assert members[world["B"]]["other_groups"] == []
    assert members[world["L"]]["role"] == "leader" and members[world["L"]]["other_groups"] == []
    assert {m["account"]["id"]: m["net_pnl"] for m in groups["Side copy"]["members"]} == {
        world["M"]: "16.00",
        world["A"]: "16.00",
    }
    assert main["id"] != side["id"]


def test_the_group_total_is_every_member_leader_included(db, alice, world):
    client = api(db, alice)
    group = make_group(client, "Main copy", world["L"], [world["A"], world["B"]])
    change(client, group["id"], "remove", world["B"], LATE)
    [out] = client.get("/api/v1/copy-groups").json()
    nets = {m["account"]["id"]: (m["trade_count"], m["net_pnl"]) for m in out["members"]}
    assert nets == {world["L"]: (5, "80.00"), world["A"]: (5, "80.00"), world["B"]: (2, "32.00")}
    assert out["net_pnl"] == "192.00"
    assert [m["account"]["id"] for m in out["members"]][0] == world["L"]
    # The same group chosen in the dashboard's account filter gives the same total.
    assert dashboard(client, copy_group_ids=[group["id"]])["net_pnl"] == "192.00"


def test_a_follower_copy_counts_in_its_own_totals_and_follows_its_own_account_fields(db, alice, world):
    client = api(db, alice)
    make_group(client, "Main copy", world["L"], [world["A"]])
    assert dashboard(client)["trade_count"] == 6 + 5  # the stored six, and A's five copies
    with db.tenant_session(alice) as conn:
        accounts.archive_account(conn, world["A"], outcome="failed", ended_on=date(2024, 3, 1))
    assert dashboard(client)["trade_count"] == 6  # archived accounts are left out unless asked
    assert dashboard(client, show_archived=True)["trade_count"] == 11
    overview = {o["account"]["id"]: o for o in client.get("/api/v1/account-overviews").json()}
    assert overview[world["A"]]["progress"]["trade_count"] == 5


# ---- the trade log -------------------------------------------------------------------------------------------------


def test_the_trade_log_shows_a_copied_trade_once_under_its_group(db, alice, world):
    client = api(db, alice)
    group = make_group(client, "Main copy", world["L"], [world["A"], world["B"]])
    page = trades(client)
    assert page["total"] == 6 and page["net_pnl"] == "96.00"  # stored trades, each once
    led = [row for row in page["items"] if row["account"]["id"] == world["L"]]
    assert len(led) == 5 and all(row["copy_group"] == {"id": group["id"], "name": "Main copy"} for row in led)
    # A follower or the group in the filter still lists each trade once, on the leader.
    by_follower = trades(client, account_ids=[world["A"]])
    assert by_follower["total"] == 5 and {row["account"]["id"] for row in by_follower["items"]} == {world["L"]}
    assert trades(client, copy_group_ids=[group["id"]])["total"] == 5
    assert trades(client, account_ids=[world["A"], world["L"]])["total"] == 5
    assert trades(client, q="main copy")["total"] == 5


# ---- import is leader-only -----------------------------------------------------------------------------------------


def test_importing_a_leader_writes_no_row_for_any_follower(db, alice):
    with db.tenant_session(alice) as conn:
        leader = make_account(conn, "Apex 07")
        followers = [make_account(conn, f"Apex {n:02}").id for n in range(8, 28)]  # twenty followers
        accounts.set_fee(conn, leader.id, "*", Decimal("4.00"))
        copy_groups.create_group(
            conn, "Twenty", leader_account_id=leader.id, follower_account_ids=followers, acknowledged_at=NOW
        )
        importer.import_files(conn, leader.id, [(SAMPLE, fixture_bytes(SAMPLE))])
        for table in ("trades", "fill_pairs", "imports", "executions", "fee_schedules"):
            assert count(conn, table, "account_id = ANY(%s::bigint[])", (followers,)) == 0, table
        assert count(conn, "trades") == 8  # one row per trade, however many followers
        assert count(conn, "copy_group_membership_events") == 20
    client = api(db, alice)
    assert trades(client)["total"] == 8
    assert dashboard(client)["trade_count"] == 8 * 21


# ---- refusals ------------------------------------------------------------------------------------------------------


def test_a_group_needs_the_acknowledgment_a_name_and_a_leader_of_no_other_group(db, alice, world):
    client = api(db, alice)
    body = {"name": "X", "leader_account_id": world["L"], "follower_account_ids": [world["A"]]}
    assert client.post("/api/v1/copy-groups", json=body).status_code == 422
    assert client.post("/api/v1/copy-groups", json={**body, "acknowledge_fills": False}).status_code == 422
    make_group(client, "X", world["L"], [world["A"]])
    again = client.post("/api/v1/copy-groups", json={**body, "name": "Y", "acknowledge_fills": True})
    assert again.status_code == 422 and "already leads" in again.json()["detail"]
    same_name = client.post(
        "/api/v1/copy-groups",
        json={
            "name": " x ",
            "leader_account_id": world["M"],
            "follower_account_ids": [world["B"]],
            "acknowledge_fills": True,
        },
    )
    assert same_name.status_code == 422
    own = client.post(
        "/api/v1/copy-groups",
        json={
            "name": "Z",
            "leader_account_id": world["M"],
            "follower_account_ids": [world["M"]],
            "acknowledge_fills": True,
        },
    )
    assert own.status_code == 422


def test_deleting_a_group_keeps_every_trade_on_the_leader(db, alice, world):
    client = api(db, alice)
    group = make_group(client, "Main copy", world["L"], [world["A"]])
    assert client.delete(f"/api/v1/copy-groups/{group['id']}").status_code == 204
    assert client.get("/api/v1/copy-groups").json() == []
    assert dashboard(client)["trade_count"] == 6
    assert dashboard(client, account_ids=[world["A"]])["trade_count"] == 0


def test_another_person_cannot_see_or_change_a_group(db, alice, bob, world):
    group = make_group(api(db, alice), "Main copy", world["L"], [world["A"]])
    client = api(db, bob)
    assert client.get("/api/v1/copy-groups").json() == []
    assert client.delete(f"/api/v1/copy-groups/{group['id']}").status_code == 404
    assert change(client, group["id"], "add", world["B"], LATE).status_code == 404
    with db.tenant_session(bob) as conn:
        mine = make_account(conn, "Bob 1")
    response = client.post(
        "/api/v1/copy-groups",
        json={
            "name": "Steal",
            "leader_account_id": world["L"],
            "follower_account_ids": [mine.id],
            "acknowledge_fills": True,
        },
    )
    assert response.status_code == 404
    assert dashboard(client)["trade_count"] == 0
