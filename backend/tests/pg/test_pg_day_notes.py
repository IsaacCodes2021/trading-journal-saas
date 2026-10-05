"""The note for a trading day, on a real Postgres: the domain functions, then the three routes (requirement F-P3)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pg_helpers import api_client

from tradejournal.pg import journal

DAY = date(2024, 1, 5)


def stored(db, user) -> list[tuple[date, str]]:
    with db.tenant_session(user) as conn:
        return [
            (r["trade_date"], r["notes"]) for r in conn.execute("SELECT trade_date, notes FROM day_notes ORDER BY 1")
        ]


# ---- the domain functions -------------------------------------------------------------------------------------------


def test_a_day_with_nothing_written_reads_as_empty(db, alice):
    with db.tenant_session(alice) as conn:
        assert journal.day_note(conn, DAY) == ""


def updated_at(db, user):
    with db.tenant_session(user) as conn:
        return conn.execute("SELECT updated_at FROM day_notes").fetchone()["updated_at"]


def test_a_note_is_stored_and_read_back_and_a_second_write_replaces_it(db, alice):
    with db.tenant_session(alice) as conn:
        journal.set_day_note(conn, DAY, "<p>Choppy</p>")
        assert journal.day_note(conn, DAY) == "<p>Choppy</p>"
    before = updated_at(db, alice)
    with db.tenant_session(alice) as conn:  # another transaction, so the clock has moved
        journal.set_day_note(conn, DAY, "<p>Choppy, then a trend</p>")
        assert journal.day_note(conn, DAY) == "<p>Choppy, then a trend</p>"
    assert stored(db, alice) == [(DAY, "<p>Choppy, then a trend</p>")]  # one row, not two
    assert updated_at(db, alice) > before  # and the time of the last change moved with it


def test_the_note_is_trimmed_and_keeps_its_formatting(db, alice):
    with db.tenant_session(alice) as conn:
        journal.set_day_note(conn, DAY, "  <p>One</p><ul><li>two</li></ul>\n")
        assert journal.day_note(conn, DAY) == "<p>One</p><ul><li>two</li></ul>"


@pytest.mark.parametrize("empty", ["", "   ", "<br>", "<p></p>", "<p> <br> </p>", None])
def test_a_note_with_no_visible_text_removes_the_row_so_the_list_of_days_stays_exact(db, alice, empty):
    with db.tenant_session(alice) as conn:
        journal.set_day_note(conn, DAY, "<p>Something</p>")
        journal.set_day_note(conn, DAY, empty)
        assert journal.day_note(conn, DAY) == ""
        assert journal.days_with_notes(conn, DAY, DAY) == []
    assert stored(db, alice) == []


def test_emptying_a_day_that_never_had_a_note_is_fine(db, alice):
    with db.tenant_session(alice) as conn:
        journal.set_day_note(conn, DAY, "")
        assert journal.day_note(conn, DAY) == ""


def test_the_days_with_notes_are_those_in_the_window_both_ends_included_oldest_first(db, alice):
    days = [DAY - timedelta(days=3), DAY, DAY + timedelta(days=1), DAY + timedelta(days=9)]
    with db.tenant_session(alice) as conn:
        for day in (days[3], days[0], days[2], days[1]):  # written out of order
            journal.set_day_note(conn, day, f"<p>{day}</p>")
        assert journal.days_with_notes(conn, DAY, DAY + timedelta(days=1)) == [days[1], days[2]]  # both ends
        assert journal.days_with_notes(conn, days[0], days[3]) == days
        assert journal.days_with_notes(conn, DAY + timedelta(days=2), DAY + timedelta(days=8)) == []
        assert journal.days_with_notes(conn, DAY, DAY) == [DAY]


def test_a_row_with_an_empty_note_is_not_listed(db, alice):
    with db.tenant_session(alice) as conn:
        conn.execute("INSERT INTO day_notes (trade_date, notes) VALUES (%s, '')", (DAY,))  # as an older version left it
        assert journal.days_with_notes(conn, DAY, DAY) == []


def test_two_people_have_their_own_note_for_the_same_day(db, alice, bob):
    with db.tenant_session(alice) as conn:
        journal.set_day_note(conn, DAY, "<p>ALICE-day</p>")
    with db.tenant_session(bob) as conn:
        assert journal.day_note(conn, DAY) == "" and journal.days_with_notes(conn, DAY, DAY) == []
        journal.set_day_note(conn, DAY, "<p>BOB-day</p>")
        journal.set_day_note(conn, DAY, "")  # and Bob emptying his own never touches Alice's
    with db.tenant_session(alice) as conn:
        assert journal.day_note(conn, DAY) == "<p>ALICE-day</p>"
    assert stored(db, alice) == [(DAY, "<p>ALICE-day</p>")] and stored(db, bob) == []


# ---- the routes -------------------------------------------------------------------------------------------------------


@pytest.fixture
def client(db, alice):
    return api_client(db, alice)


def test_a_day_with_no_note_answers_with_an_empty_one(client):
    response = client.get("/api/v1/day-notes/2024-01-05")
    assert response.status_code == 200 and response.json() == {"day": "2024-01-05", "notes_html": ""}


def test_a_note_is_saved_answered_with_what_was_stored_and_read_back(client):
    saved = client.put("/api/v1/day-notes/2024-01-05", json={"html": "  <p>Choppy <b>open</b></p> "})
    assert saved.status_code == 200 and saved.json() == {"day": "2024-01-05", "notes_html": "<p>Choppy <b>open</b></p>"}
    assert client.get("/api/v1/day-notes/2024-01-05").json() == saved.json()


def test_what_is_not_on_the_allow_list_is_removed_on_the_way_in(client):
    html = '<p onclick="x()">hi</p><script>alert(1)</script><img src=x onerror=alert(1)><a href="javascript:alert(1)">l</a>'
    stored_html = client.put("/api/v1/day-notes/2024-01-05", json={"html": html}).json()["notes_html"]
    assert "script" not in stored_html and "onclick" not in stored_html and "onerror" not in stored_html
    assert "javascript:" not in stored_html and "<img" not in stored_html and "hi" in stored_html


def test_an_emptied_note_is_removed_and_the_day_leaves_the_list(client):
    client.put("/api/v1/day-notes/2024-01-05", json={"html": "<p>x</p>"})
    window = {"start": "2024-01-01", "end": "2024-01-31"}
    assert client.get("/api/v1/day-notes", params=window).json() == {"days": ["2024-01-05"]}
    emptied = client.put("/api/v1/day-notes/2024-01-05", json={"html": "<p><br></p>"})
    assert emptied.json() == {"day": "2024-01-05", "notes_html": ""}
    assert client.get("/api/v1/day-notes", params=window).json() == {"days": []}


def test_the_list_gives_only_the_days_with_a_note_in_the_window_oldest_first_and_never_the_text(client):
    for day in ("2024-01-31", "2024-01-02", "2024-02-01", "2023-12-31", "2024-01-15"):
        client.put(f"/api/v1/day-notes/{day}", json={"html": f"<p>SECRET-{day}</p>"})
    response = client.get("/api/v1/day-notes", params={"start": "2024-01-01", "end": "2024-01-31"})
    assert response.json() == {"days": ["2024-01-02", "2024-01-15", "2024-01-31"]}
    assert "SECRET" not in response.text


@pytest.mark.parametrize("day", ["2000-01-01", "2100-12-31", "2024-02-29"])
def test_the_edges_of_the_range_and_a_leap_day_are_allowed(client, day):
    assert client.put(f"/api/v1/day-notes/{day}", json={"html": "<p>x</p>"}).status_code == 200
    assert client.get(f"/api/v1/day-notes/{day}").json()["notes_html"] == "<p>x</p>"


@pytest.mark.parametrize("day", ["1999-12-31", "2101-01-01", "0202-01-05", "not-a-date", "2024-13-01", "2023-02-29"])
def test_a_day_outside_the_range_or_not_a_date_is_a_422_and_writes_nothing(client, db, alice, day):
    assert client.get(f"/api/v1/day-notes/{day}").status_code == 422
    assert client.put(f"/api/v1/day-notes/{day}", json={"html": "<p>x</p>"}).status_code == 422
    assert stored(db, alice) == []


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"start": "2024-01-01"},
        {"end": "2024-01-31"},
        {"start": "2024-02-01", "end": "2024-01-01"},  # backwards
        {"start": "2024-01-01", "end": "2025-02-04"},  # more than 400 days
        {"start": "1999-01-01", "end": "2024-01-01"},
        {"start": "2024-01-01", "end": "2024-01-31", "limit": "5"},  # a misspelled or unknown parameter is an error
        {"start": "soon", "end": "later"},
    ],
)
def test_a_bad_window_is_a_422(client, params):
    assert client.get("/api/v1/day-notes", params=params).status_code == 422


@pytest.mark.parametrize(
    "window",
    [
        {"start": "2000-01-01", "end": "2000-01-31"},  # the first day that may have a note, as the start
        {"start": "2100-12-01", "end": "2100-12-31"},  # the last day that may have a note, as the end
    ],
)
def test_a_window_may_start_on_the_first_allowed_day_and_end_on_the_last(client, window):
    assert client.get("/api/v1/day-notes", params=window).status_code == 200


def test_a_window_of_a_single_day_is_allowed(client):
    client.put("/api/v1/day-notes/2024-01-05", json={"html": "<p>x</p>"})
    window = client.get("/api/v1/day-notes", params={"start": "2024-01-05", "end": "2024-01-05"})
    assert window.status_code == 200 and window.json() == {"days": ["2024-01-05"]}


def test_a_window_of_exactly_four_hundred_days_is_allowed_and_one_more_is_not(client):
    start = date(2024, 1, 1)
    ok = {"start": start.isoformat(), "end": (start + timedelta(days=399)).isoformat()}
    too_long = {"start": start.isoformat(), "end": (start + timedelta(days=400)).isoformat()}
    assert client.get("/api/v1/day-notes", params=ok).status_code == 200
    assert client.get("/api/v1/day-notes", params=too_long).status_code == 422


@pytest.mark.parametrize("body", [{}, {"html": "<p>x</p>", "extra": 1}, {"html": 5}, {"html": "x" * 100_001}])
def test_a_bad_body_is_a_422_and_writes_nothing(client, db, alice, body):
    assert client.put("/api/v1/day-notes/2024-01-05", json=body).status_code == 422
    assert stored(db, alice) == []


def test_a_note_at_the_size_limit_is_accepted(client):
    assert client.put("/api/v1/day-notes/2024-01-05", json={"html": "<p>" + "x" * 99_000 + "</p>"}).status_code == 200


def test_each_person_has_their_own_note_for_a_day_and_never_sees_the_others(db, alice, bob):
    ann, ben = api_client(db, alice), api_client(db, bob)
    ann.put("/api/v1/day-notes/2024-01-05", json={"html": "<p>ALICE-private</p>"})
    window = {"start": "2024-01-01", "end": "2024-01-31"}
    assert ben.get("/api/v1/day-notes/2024-01-05").json()["notes_html"] == ""
    assert ben.get("/api/v1/day-notes", params=window).json() == {"days": []}
    ben.put("/api/v1/day-notes/2024-01-05", json={"html": "<p>BOB-private</p>"})
    ben.put("/api/v1/day-notes/2024-01-05", json={"html": ""})
    assert ann.get("/api/v1/day-notes/2024-01-05").json()["notes_html"] == "<p>ALICE-private</p>"
    assert ann.get("/api/v1/day-notes", params=window).json() == {"days": ["2024-01-05"]}
