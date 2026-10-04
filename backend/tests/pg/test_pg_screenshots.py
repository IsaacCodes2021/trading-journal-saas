"""Screenshots against a real Postgres and a real folder: the domain functions, then the routes (doc 03 section 6)."""

from __future__ import annotations

import re
import struct
import threading
from pathlib import Path

import pytest
from api_fakes import FakeAudit, FakeSessions
from fastapi.testclient import TestClient
from pg_helpers import NOON_UTC, ORIGIN, PNG_1X1, SECRET, make_account, make_trades, write_headers

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.pg_services import PgAccountService
from tradejournal.api.ports import Services
from tradejournal.api.ratelimit import RateLimits, Rule
from tradejournal.filestore import LocalFileStore
from tradejournal.pg import screenshots as shots
from tradejournal.pg import usage

PNG = PNG_1X1
KEY = re.compile(r"u/[0-9a-f-]{36}/[0-9a-f]{32}\.png")


def png_of_size(width: int, height: int, *, pad: int = 0) -> bytes:
    header = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )
    return header + b"\x00" * pad


def jpeg_of_size(width: int, height: int) -> bytes:
    """A JPEG header (start of image, then a one-component frame) and nothing else: all the server reads."""
    return (
        b"\xff\xd8\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", height, width) + b"\x01\x01\x11\x00"
    )


@pytest.fixture
def files(tmp_path):
    return LocalFileStore(tmp_path / "files")


@pytest.fixture
def trade(db, alice):
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
        return make_trades(conn, account.id, 2)[0]


def stored(files) -> list[str]:
    return [f.key for f in files.list()]


# ---- the domain functions -------------------------------------------------------------------------------------------


def test_an_image_is_stored_under_a_key_the_server_makes_and_recorded_with_what_its_header_said(
    db, alice, files, trade
):
    with db.tenant_session(alice) as conn:
        shot = shots.add(conn, files, alice, trade, "C:\\charts\\entry.png", PNG)
    assert KEY.fullmatch(shot.key) and shot.key.startswith(f"u/{alice}/")
    assert (shot.original_name, shot.content_type, shot.size_bytes, shot.width, shot.height) == (
        "entry.png",
        "image/png",
        len(PNG),
        1,
        1,
    )
    assert files.get(shot.key) == PNG
    assert stored(files) == [shot.key]


def test_the_type_the_extension_and_the_size_come_from_the_header_not_from_the_name(db, alice, files, trade):
    with db.tenant_session(alice) as conn:
        shot = shots.add(conn, files, alice, trade, "looks-like.png", jpeg_of_size(30, 50))
    assert (shot.content_type, shot.width, shot.height) == ("image/jpeg", 30, 50)  # wide 30, tall 50
    assert shot.key.endswith(".jpg") and shot.original_name == "looks-like.png"


def test_the_keys_of_two_uploads_of_the_same_file_differ(db, alice, files, trade):
    with db.tenant_session(alice) as conn:
        first = shots.add(conn, files, alice, trade, "a.png", PNG)
        second = shots.add(conn, files, alice, trade, "a.png", PNG)
    assert first.key != second.key and len(stored(files)) == 2


def test_the_counters_follow_the_files(db, alice, files, trade):
    with db.tenant_session(alice) as conn:
        first = shots.add(conn, files, alice, trade, "a.png", PNG)
        shots.add(conn, files, alice, trade, "b.png", PNG + b"extra")
    with db.tenant_session(alice) as conn:
        counted = usage.get_usage(conn)
        assert (counted.screenshots_count, counted.storage_bytes) == (2, len(PNG) * 2 + 5)
        shots.delete(conn, first.id)
    with db.tenant_session(alice) as conn:
        counted = usage.get_usage(conn)
        assert (counted.screenshots_count, counted.storage_bytes) == (1, len(PNG) + 5)


def test_a_name_with_nothing_in_it_gets_one_and_a_long_name_is_cut(db, alice, files, trade):
    with db.tenant_session(alice) as conn:
        blank = shots.add(conn, files, alice, trade, "", PNG)
        long = shots.add(conn, files, alice, trade, "x" * 500 + ".png", PNG)
    assert blank.original_name == "screenshot"
    assert len(long.original_name) == shots.MAX_NAME_LENGTH


def test_the_screenshots_of_a_trade_come_back_oldest_first(db, alice, files, trade):
    with db.tenant_session(alice) as conn:
        a = shots.add(conn, files, alice, trade, "a.png", PNG)
        b = shots.add(conn, files, alice, trade, "b.png", PNG)
    with db.tenant_session(alice) as conn:
        assert [s.id for s in shots.for_trade(conn, trade)] == [a.id, b.id]
        assert shots.get(conn, a.id) == a
        assert shots.get(conn, 999_999) is None


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        pytest.param(b"not an image at all", "not a PNG, JPEG, GIF or WebP", id="text"),
        pytest.param(
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>",
            "not a PNG, JPEG, GIF or WebP",
            id="svg",
        ),
        pytest.param(png_of_size(0, 0), "damaged", id="zero-size"),
        pytest.param(png_of_size(10_000, 5_001), "more than the 50 million", id="too-many-pixels"),
        pytest.param(png_of_size(65_535, 65_535), "65,535 by 65,535", id="huge"),
        pytest.param(png_of_size(1, 1, pad=shots.MAX_FILE_BYTES), "under 10 MB", id="too-big"),
    ],
)
def test_what_is_not_an_acceptable_image_is_refused_in_words_and_nothing_is_kept(
    db, alice, files, trade, data, fragment
):
    with pytest.raises(ValueError, match=fragment):
        with db.tenant_session(alice) as conn:
            shots.add(conn, files, alice, trade, "x.png", data)
    assert stored(files) == []
    with db.tenant_session(alice) as conn:
        assert shots.for_trade(conn, trade) == []


def test_a_file_of_exactly_ten_megabytes_is_accepted_and_one_byte_more_is_not(db, alice, files, trade):
    exact = png_of_size(1, 1, pad=shots.MAX_FILE_BYTES - len(png_of_size(1, 1)))
    assert len(exact) == shots.MAX_FILE_BYTES
    with db.tenant_session(alice) as conn:
        shots.add(conn, files, alice, trade, "big.png", exact)
        with pytest.raises(ValueError, match="under 10 MB"):
            shots.add(conn, files, alice, trade, "big.png", exact + b"\x00")


def test_a_trade_that_is_not_there_or_not_theirs_is_not_found_and_nothing_is_kept(db, alice, bob, files, trade):
    with pytest.raises(LookupError):
        with db.tenant_session(alice) as conn:
            shots.add(conn, files, alice, 999_999, "x.png", PNG)
    with pytest.raises(LookupError):
        with db.tenant_session(bob) as conn:
            shots.add(conn, files, bob, trade, "x.png", PNG)  # Alice's trade
    assert stored(files) == []


def test_a_trade_may_have_twenty_screenshots_and_no_more(db, alice, files, trade, monkeypatch):
    assert shots.MAX_PER_TRADE == 20
    monkeypatch.setattr(shots, "MAX_PER_TRADE", 3)
    with db.tenant_session(alice) as conn:
        for number in range(3):
            shots.add(conn, files, alice, trade, f"{number}.png", PNG)
        with pytest.raises(ValueError, match="3 screenshots at most"):
            shots.add(conn, files, alice, trade, "4.png", PNG)
    assert len(stored(files)) == 3


def test_the_limit_is_per_trade(db, alice, files, monkeypatch):
    monkeypatch.setattr(shots, "MAX_PER_TRADE", 1)
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
        first, second = make_trades(conn, account.id, 2)
        shots.add(conn, files, alice, first, "a.png", PNG)
        shots.add(conn, files, alice, second, "b.png", PNG)


def test_a_person_has_five_hundred_megabytes_and_the_message_says_what_to_do(db, alice, files, trade, monkeypatch):
    assert shots.MAX_STORED_BYTES == 500 * 1024 * 1024
    monkeypatch.setattr(shots, "MAX_STORED_BYTES", len(PNG) * 2)
    with db.tenant_session(alice) as conn:
        shots.add(conn, files, alice, trade, "a.png", PNG)
        shots.add(conn, files, alice, trade, "b.png", PNG)  # exactly at the limit: fine
        with pytest.raises(ValueError, match="Delete some"):
            shots.add(conn, files, alice, trade, "c.png", PNG)


def test_the_room_one_person_uses_is_not_counted_against_another(db, alice, bob, files, trade, monkeypatch):
    monkeypatch.setattr(shots, "MAX_STORED_BYTES", len(PNG))
    with db.tenant_session(alice) as conn:
        shots.add(conn, files, alice, trade, "a.png", PNG)
    with db.tenant_session(bob) as conn:
        account = make_account(conn, "Bob 1")
        shots.add(conn, files, bob, make_trades(conn, account.id, 1)[0], "b.png", PNG)


def test_a_failure_while_recording_takes_the_file_out_again(db, alice, files, trade, monkeypatch):
    def broken(name):
        raise RuntimeError("the row could not be made")

    monkeypatch.setattr(shots, "clean_filename", broken)
    with pytest.raises(RuntimeError, match="could not be made"):
        with db.tenant_session(alice) as conn:
            shots.add(conn, files, alice, trade, "a.png", PNG)
    assert stored(files) == []


def test_a_delete_hands_back_what_it_removed_so_the_file_can_go_after_the_commit(db, alice, files, trade):
    with db.tenant_session(alice) as conn:
        shot = shots.add(conn, files, alice, trade, "a.png", PNG)
    with db.tenant_session(alice) as conn:
        removed = shots.delete(conn, shot.id)
    assert removed == shot
    assert files.get(shot.key) == PNG  # the function did not touch the file: that is the caller's job, after the commit
    with db.tenant_session(alice) as conn:
        assert shots.delete(conn, shot.id) is None


def test_another_person_cannot_read_or_delete_a_screenshot(db, alice, bob, files, trade):
    with db.tenant_session(alice) as conn:
        shot = shots.add(conn, files, alice, trade, "a.png", PNG)
    with db.tenant_session(bob) as conn:
        assert shots.get(conn, shot.id) is None
        assert shots.delete(conn, shot.id) is None
    with db.tenant_session(alice) as conn:
        assert shots.get(conn, shot.id) == shot


def test_a_row_from_before_sizes_were_recorded_is_served_by_the_end_of_its_name(db, alice, trade):
    with db.tenant_session(alice) as conn:
        conn.execute(
            "INSERT INTO screenshots (trade_id, stored_name, original_name) VALUES (%s, 'old-name.jpg', 'old.jpg')",
            (trade,),
        )
        [old] = shots.for_trade(conn, trade)
    assert (old.content_type, old.size_bytes, old.width, old.height) == ("image/jpeg", 0, None, None)


class MeetingStore(LocalFileStore):
    """A store whose `put` waits (up to a second) for the other uploads to arrive. `add` has counted by the time it puts,
    so without the per-person lock every upload meets at the barrier having counted zero and all are kept; with the
    lock only one is ever here, and its wait simply times out. (A race this rare does not show on its own.)"""

    def __init__(self, root: Path, parties: int) -> None:
        super().__init__(root)
        self.barrier = threading.Barrier(parties)

    def put(self, key: str, data: bytes) -> None:
        try:
            self.barrier.wait(timeout=1.0)
        except threading.BrokenBarrierError:
            pass
        super().put(key, data)


def test_uploads_at_once_cannot_all_slip_under_a_limit(db, alice, trade, tmp_path, monkeypatch):
    monkeypatch.setattr(shots, "MAX_PER_TRADE", 1)
    meeting = MeetingStore(tmp_path / "meeting", parties=4)
    outcomes: list[str] = []

    def upload():
        try:
            with db.tenant_session(alice) as conn:
                shots.add(conn, meeting, alice, trade, "a.png", PNG)
            outcomes.append("added")
        except ValueError:
            outcomes.append("refused")

    threads = [threading.Thread(target=upload) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["added"] + ["refused"] * 3
    assert len(list(meeting.list())) == 1
    with db.tenant_session(alice) as conn:
        assert len(shots.for_trade(conn, trade)) == 1


# ---- the routes -------------------------------------------------------------------------------------------------------


def make_client(db, system, user_id, files_dir: Path | None, **config) -> TestClient:
    sessions = FakeSessions()
    token, session = sessions.sign_in(user_id)
    services = Services(sessions=sessions, accounts=PgAccountService(db, system), audit=FakeAudit())
    settings = ApiConfig(
        app_origin=ORIGIN,
        web_dist=Path("/nonexistent"),
        session_secret=SECRET,
        files_dir=files_dir,
        **config,
    )
    app = create_app(settings, services, database=db, clock=lambda: NOON_UTC)
    return TestClient(app, base_url=ORIGIN, headers={"cookie": f"{SESSION_COOKIE}={token}", **write_headers(session)})


@pytest.fixture
def client(db, system, alice, tmp_path):
    return make_client(db, system, alice, tmp_path / "files")


def upload(client, trade, data=PNG, name="shot.png"):
    return client.post(f"/api/v1/trades/{trade}/screenshots", files={"file": (name, data, "image/png")})


def test_an_upload_is_201_with_the_address_of_its_image_and_the_bytes_come_back_from_that_address(client, trade):
    response = upload(client, trade, name="C:\\charts\\my entry.png")
    assert response.status_code == 201
    body = response.json()
    assert body["original_name"] == "my entry.png" and body["src"] == f"/files/{body['id']}"
    served = client.get(body["src"])
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["cache-control"] == "no-store"
    assert served.headers["content-disposition"] == f'inline; filename="screenshot-{body["id"]}.png"'
    assert "my entry" not in served.headers["content-disposition"]  # a name the uploader chose never reaches a header


def test_an_image_is_served_under_the_type_its_header_gave_not_the_one_the_browser_declared(client, trade):
    shot = upload(client, trade, jpeg_of_size(30, 50), "claims-to-be.png").json()  # `upload` declares image/png
    served = client.get(shot["src"])
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["content-disposition"] == f'inline; filename="screenshot-{shot["id"]}.jpg"'


def test_an_image_of_exactly_ten_megabytes_is_taken_through_the_route_and_one_byte_more_is_not(client, trade):
    exact = png_of_size(1, 1, pad=shots.MAX_FILE_BYTES - len(png_of_size(1, 1)))
    assert upload(client, trade, exact).status_code == 201
    assert upload(client, trade, exact + b"\x00").status_code == 413


def test_the_trade_lists_its_screenshots_with_their_addresses(client, trade):
    first, second = upload(client, trade).json(), upload(client, trade).json()
    listed = client.get(f"/api/v1/trades/{trade}").json()["screenshots"]
    assert [(s["id"], s["src"]) for s in listed] == [(first["id"], first["src"]), (second["id"], second["src"])]
    assert client.get(f"/api/v1/trades/{trade}").json()["screenshot_count"] == 2


@pytest.mark.parametrize(
    ("data", "status", "fragment"),
    [
        pytest.param(b"hello", 422, "not a PNG, JPEG, GIF or WebP", id="text"),
        pytest.param(png_of_size(20_000, 20_000), 422, "more than the 50 million", id="too-many-pixels"),
        pytest.param(png_of_size(1, 1, pad=shots.MAX_FILE_BYTES), 413, "larger than 10 MB", id="too-big"),
    ],
)
def test_a_bad_upload_is_refused_in_words_and_keeps_nothing(client, trade, tmp_path, data, status, fragment):
    response = upload(client, trade, data)
    assert response.status_code == status and fragment in response.json()["detail"]
    assert list((tmp_path / "files").rglob("*.png")) == []


def test_an_upload_for_a_trade_that_is_not_there_is_a_404(client):
    response = upload(client, 999_999)
    assert response.status_code == 404 and response.json()["detail"] == "That trade does not exist."


def test_an_upload_without_the_csrf_token_changes_nothing(db, system, alice, trade, tmp_path):
    bare = make_client(db, system, alice, tmp_path / "files")
    bare.headers.pop("x-csrf-token")
    assert upload(bare, trade).status_code == 403
    assert list((tmp_path / "files").rglob("*")) == [] or not any(p.is_file() for p in (tmp_path / "files").rglob("*"))


def test_uploads_are_limited_per_person(db, system, alice, trade, tmp_path):
    limited = make_client(
        db,
        system,
        alice,
        tmp_path / "files",
        rate_limits=RateLimits(screenshot_upload=Rule("screenshot_upload", 2, 3600)),
    )
    assert [upload(limited, trade).status_code for _ in range(3)] == [201, 201, 429]


def test_the_default_allows_a_review_session_of_a_hundred_and_twenty_an_hour():
    assert RateLimits().screenshot_upload == Rule("screenshot_upload", 120, 3600)


def test_a_screenshot_is_deleted_with_its_file_and_the_ones_left_are_listed(client, trade, tmp_path):
    first, second = upload(client, trade).json(), upload(client, trade).json()
    response = client.delete(f"/api/v1/trades/{trade}/screenshots/{first['id']}")
    assert response.status_code == 200 and [s["id"] for s in response.json()] == [second["id"]]
    assert client.get(first["src"]).status_code == 404
    assert client.get(second["src"]).status_code == 200
    assert len([p for p in (tmp_path / "files").rglob("*") if p.is_file()]) == 1


def test_deleting_something_that_is_not_there_or_is_on_another_trade_is_a_404(client, db, alice, trade):
    shot = upload(client, trade).json()
    with db.tenant_session(alice) as conn:
        other = make_trades(conn, conn.execute("SELECT id FROM accounts").fetchone()["id"], 1)[0]
    gone = client.delete(f"/api/v1/trades/{trade}/screenshots/999999")
    wrong_trade = client.delete(f"/api/v1/trades/{other}/screenshots/{shot['id']}")
    assert (gone.status_code, wrong_trade.status_code) == (404, 404)
    assert (
        gone.json()
        == wrong_trade.json()
        == {
            "type": "about:blank",
            "title": "Not Found",
            "status": 404,
            "detail": "That screenshot does not exist.",
            "errors": None,
        }
    )
    assert client.get(shot["src"]).status_code == 200  # the one on the other trade's id was not touched
    assert client.delete(f"/api/v1/trades/{trade}/screenshots/{shot['id']}").status_code == 200
    assert client.delete(f"/api/v1/trades/{trade}/screenshots/{shot['id']}").status_code == 404


def test_a_file_that_has_gone_missing_from_the_store_is_a_404_in_words(client, trade, tmp_path):
    shot = upload(client, trade).json()
    for path in (tmp_path / "files").rglob("*.png"):
        path.unlink()
    response = client.get(shot["src"])
    assert response.status_code == 404 and response.json()["detail"] == "That screenshot's file is missing."


def test_a_missing_screenshot_and_another_persons_are_the_same_404(client, db, system, bob, tmp_path, trade):
    shot = upload(client, trade).json()
    as_bob = make_client(db, system, bob, tmp_path / "files")
    theirs, missing = as_bob.get(shot["src"]), as_bob.get("/files/999999")
    assert theirs.status_code == missing.status_code == 404 and theirs.json() == missing.json()


def test_anyone_not_signed_in_gets_a_401_for_an_image(db, alice, trade, tmp_path):
    services = Services(sessions=FakeSessions(), accounts=PgAccountService(db, None), audit=FakeAudit())
    app = create_app(
        ApiConfig(app_origin=ORIGIN, web_dist=Path("/nonexistent"), session_secret=SECRET, files_dir=tmp_path / "f"),
        services,
        database=db,
    )
    assert TestClient(app, base_url=ORIGIN).get("/files/1").status_code == 401


def test_a_server_with_nowhere_to_keep_files_says_so_and_still_shows_the_trade(db, system, alice, trade):
    bare = make_client(db, system, alice, None)
    assert upload(bare, trade).status_code == 503
    assert upload(bare, trade).json()["detail"] == "Screenshots are not set up on this server yet."
    assert bare.get("/files/1").status_code == 503
    assert bare.delete(f"/api/v1/trades/{trade}/screenshots/1").status_code == 503
    assert bare.get(f"/api/v1/trades/{trade}").json()["screenshots"] == []


def test_a_screenshot_from_before_the_files_were_kept_here_has_no_address_on_a_server_without_a_store(
    db, system, alice, trade
):
    with db.tenant_session(alice) as conn:
        conn.execute(
            "INSERT INTO screenshots (trade_id, stored_name, original_name) VALUES (%s, 'x.png', 'x.png')", (trade,)
        )
    bare = make_client(db, system, alice, None)
    [listed] = bare.get(f"/api/v1/trades/{trade}").json()["screenshots"]
    assert listed["src"] is None


def test_the_folder_comes_from_the_environment():
    assert ApiConfig.from_env({"TJ_FILES_DIR": " /var/data/files "}).files_dir == Path("/var/data/files")
    assert ApiConfig.from_env({}).files_dir is None
    assert ApiConfig.from_env({"TJ_FILES_DIR": "  "}).files_dir is None
