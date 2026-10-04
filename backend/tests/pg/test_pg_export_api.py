"""`GET /api/v1/export` on the real app and a real Postgres: what the browser receives, and what the server records."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest
from api_fakes import FakeAudit, FakeSessions
from fastapi.testclient import TestClient
from helpers import SAMPLE, fixture_bytes
from pg_helpers import NOON_UTC, ORIGIN, SECRET, make_account

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.pg_services import PgAccountService
from tradejournal.api.ports import Services
from tradejournal.api.routers import export as export_router
from tradejournal.auth.iphash import hash_ip
from tradejournal.pg import export as pg_export
from tradejournal.pg import importer, journal


class World:
    def __init__(self, db, system, user_id) -> None:
        self.audit = FakeAudit()
        sessions = FakeSessions()
        self.token, _ = sessions.sign_in(user_id)
        self.services = Services(sessions=sessions, accounts=PgAccountService(db, system), audit=self.audit)
        config = ApiConfig(
            app_origin=ORIGIN, web_dist=Path("/nonexistent"), session_secret=SECRET, app_version="v-test"
        )
        self.app = create_app(config, self.services, database=db, clock=lambda: NOON_UTC)
        self.client = TestClient(self.app, base_url=ORIGIN, raise_server_exceptions=False)
        self.client.cookies.set(SESSION_COOKIE, self.token)


@pytest.fixture
def world(db, system, alice, bob):
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
        importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))])
        first = conn.execute("SELECT id FROM trades ORDER BY id LIMIT 1").fetchone()["id"]
        journal.set_notes(conn, first, "<p>my note</p>")
    with db.tenant_session(bob) as conn:
        make_account(conn, "Bob Secret Account")
    return World(db, system, alice)


def zip_of(response) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def test_it_downloads_a_zip_with_the_right_headers(world):
    response = world.client.get("/api/v1/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="roundturn-export-2024-03-22.zip"'
    assert response.headers["cache-control"] == "no-store"
    assert int(response.headers["content-length"]) == len(response.content) > 1000
    assert response.content[:2] == b"PK"


def test_the_zip_holds_the_signed_in_persons_data_and_nobody_elses(world):
    files = zip_of(world.client.get("/api/v1/export"))
    assert {"profile.json", "manifest.json", "accounts.csv", "trades.csv", "fill_pairs.csv", "ledger.csv"} <= set(files)
    assert [r["name"] for r in csv.DictReader(io.StringIO(files["accounts.csv"].decode()))] == ["Apex 07"]
    assert any(name.startswith("notes/trade-") and name.endswith(".txt") for name in files)
    blob = b"\n".join(files.values()).decode("utf-8", "replace")
    assert "Bob Secret Account" not in blob and "bob@example.test" not in blob


def test_the_profile_and_the_manifest_come_from_this_server_and_this_moment(world):
    files = zip_of(world.client.get("/api/v1/export"))
    profile = json.loads(files["profile.json"])
    assert profile["account"]["email"] == "alice@example.test"
    assert profile["settings"]["timezone"] == "America/Los_Angeles" and profile["settings"]["theme"] == "dark"
    manifest = json.loads(files["manifest.json"])
    assert manifest["exported_at"] == "2024-03-22T12:00:00Z" and manifest["app_version"] == "v-test"


def test_it_needs_a_session(world):
    for headers in ({}, {"cookie": f"{SESSION_COOKIE}=not-a-real-token"}):
        anonymous = TestClient(world.app, base_url=ORIGIN, headers=headers)
        assert anonymous.get("/api/v1/export").status_code == 401


def test_each_download_is_written_to_the_audit_log_with_facts_and_no_content(world, alice):
    world.client.get("/api/v1/export")
    [event] = world.audit.events
    assert event["action"] == "export" and event["user_id"] == alice
    assert event["ip_hash"] == hash_ip(SECRET, "testclient")
    assert (
        set(event["meta"]) == {"bytes", "trades", "notes"}
        and event["meta"]["trades"] > 0
        and event["meta"]["notes"] == 1
    )
    assert "Apex" not in json.dumps(event, default=str) and "my note" not in json.dumps(event, default=str)


def test_no_event_is_written_when_nothing_was_downloaded(world):
    anonymous = TestClient(world.app, base_url=ORIGIN)
    anonymous.get("/api/v1/export")
    assert world.audit.events == []


def test_only_a_few_exports_are_built_at_once_and_the_rest_are_told_to_wait(world):
    held = [export_router._RUNNING.acquire(blocking=False) for _ in range(export_router.MAX_AT_ONCE)]
    try:
        assert all(held)
        refused = world.client.get("/api/v1/export")
        assert refused.status_code == 429 and refused.headers["retry-after"] == "30"
        assert refused.headers["content-type"] == "application/problem+json"
        assert "Try again" in refused.json()["detail"]
        assert world.audit.events == []
    finally:
        for _ in held:
            export_router._RUNNING.release()
    assert world.client.get("/api/v1/export").status_code == 200  # and as soon as there is room, it works


def test_a_failed_export_gives_back_its_place_and_says_so_plainly(world, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pg_export, "build", boom)
    for _ in range(export_router.MAX_AT_ONCE + 2):  # more failures than places: none may keep its place
        failed = world.client.get("/api/v1/export")
        assert failed.status_code == 500 and failed.headers["content-type"] == "application/problem+json"
        assert "disk full" not in failed.text
    assert world.audit.events == []
    monkeypatch.undo()
    assert world.client.get("/api/v1/export").status_code == 200


def test_it_is_a_read_it_changes_nothing(world, db, alice):
    def snapshot():
        with db.tenant_session(alice) as conn:
            return {
                t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]  # noqa: S608 - literals from this file
                for t in ("accounts", "trades", "fill_pairs", "tags", "day_notes", "user_usage")
            }

    before = snapshot()
    world.client.get("/api/v1/export")
    assert snapshot() == before


def test_a_person_who_signed_out_elsewhere_cannot_download(world):
    world.services.sessions.revoke(world.token)
    assert world.client.get("/api/v1/export").status_code == 401


def test_the_route_builds_it_inside_a_read_only_snapshot_transaction(world, monkeypatch):
    seen = {}
    real = pg_export.build

    def watching(conn, **kwargs):
        seen["isolation"] = conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
        seen["read_only"] = conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"]
        return real(conn, **kwargs)

    monkeypatch.setattr(pg_export, "build", watching)
    assert world.client.get("/api/v1/export").status_code == 200
    assert seen == {"isolation": "repeatable read", "read_only": "on"}
