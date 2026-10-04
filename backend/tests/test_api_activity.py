"""`GET /api/v1/activity`: a person's own recent security events, as far as they may see them (no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.ports import ActivityEvent

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
LATE = datetime(2026, 10, 4, 12, 30, tzinfo=UTC)
EARLY = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)


@pytest.fixture
def world(tmp_path):
    services, sessions, accounts = make_services()
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET)
    client = TestClient(create_app(config, services), base_url=ORIGIN)
    alice = accounts.add("alice@example.test")
    token, _ = sessions.sign_in(alice.user.id)
    return client, services, alice, {"cookie": f"{SESSION_COOKIE}={token}"}, accounts, sessions


def test_it_needs_a_session(world):
    client, *_ = world
    assert client.get("/api/v1/activity").status_code == 401
    assert client.get("/api/v1/activity", headers={"cookie": f"{SESSION_COOKIE}=nonsense"}).status_code == 401


def test_it_lists_the_persons_own_events_newest_first_with_only_what_a_person_may_see(world):
    client, services, alice, headers, *_ = world
    services.audit.activity[alice.user.id] = [
        ActivityEvent(at=LATE, kind="sign_out_everywhere", ended=2, mfa=None),
        ActivityEvent(at=EARLY, kind="sign_in", ended=None, mfa=True),
    ]
    response = client.get("/api/v1/activity", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "events": [
            {"at": "2026-10-04T12:30:00Z", "kind": "sign_out_everywhere", "ended": 2, "mfa": None},
            {"at": "2026-10-03T08:00:00Z", "kind": "sign_in", "ended": None, "mfa": True},
        ]
    }


def test_it_asks_for_nobody_elses_events(world):
    client, services, alice, headers, accounts, _ = world
    bob = accounts.add("bob@example.test")
    services.audit.activity[bob.user.id] = [ActivityEvent(at=LATE, kind="export", ended=None, mfa=None)]
    assert client.get("/api/v1/activity", headers=headers).json() == {"events": []}
    assert services.audit.asked == [(alice.user.id, 30)]


def test_the_default_is_thirty_and_a_limit_may_be_asked_for_within_one_to_a_hundred(world):
    client, services, alice, headers, *_ = world
    client.get("/api/v1/activity", headers=headers)
    client.get("/api/v1/activity", params={"limit": 5}, headers=headers)
    client.get("/api/v1/activity", params={"limit": 100}, headers=headers)
    assert [limit for _, limit in services.audit.asked] == [30, 5, 100]


@pytest.mark.parametrize("limit", ["0", "-1", "101", "1000000", "ten", ""])
def test_a_limit_outside_one_to_a_hundred_is_refused_before_anything_is_read(world, limit):
    client, services, _, headers, *_ = world
    assert client.get("/api/v1/activity", params={"limit": limit}, headers=headers).status_code == 422
    assert services.audit.asked == []


def test_a_signed_in_person_with_nothing_yet_gets_an_empty_list(world):
    client, _, _, headers, *_ = world
    assert client.get("/api/v1/activity", headers=headers).json() == {"events": []}


def test_it_changes_nothing_and_writes_no_audit_row_of_its_own(world):
    client, services, _, headers, *_ = world
    client.get("/api/v1/activity", headers=headers)
    assert services.audit.events == []
    assert client.post("/api/v1/activity", headers=headers).status_code in (403, 405)


def test_the_real_port_skips_an_action_it_has_no_word_for_instead_of_failing(monkeypatch):
    """The database lists what a person may see and the port words each; if the two ever disagree, the person's page must
    still load (a test elsewhere makes them agree), and the unknown event must not reach them."""
    from contextlib import contextmanager
    from uuid import uuid4

    from tradejournal.api import audit as api_audit

    rows = [
        {"id": 2, "at": LATE, "action": "brand_new_event", "ended": None, "mfa": None},
        {"id": 1, "at": EARLY, "action": "login", "ended": None, "mfa": True},
    ]
    monkeypatch.setattr(api_audit.audit, "activity", lambda conn, user_id, limit: rows)

    class System:
        @contextmanager
        def system_session(self, profile):
            assert profile == "login"
            yield object()

    events = api_audit.PgAuditLog(System()).recent(uuid4(), 10)
    assert [(e.kind, e.mfa) for e in events] == [("sign_in", True)]
