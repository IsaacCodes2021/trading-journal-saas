"""Screenshots and the deletion sweep with the bucket as the file store (bucketstore.py), on a real Postgres and a fake S3.

The routes and the sweep are the ones the folder store passes already (test_pg_screenshots.py, test_pg_sweep.py); this checks
what is different when the bytes live somewhere that can be down: a refusal is a 503 in words and keeps nothing, a delete
the person asked for is not undone by a store that is down, and the sweep job reaches the files from another process.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fake_s3 import FakeBucket
from pg_helpers import PNG_1X1, make_account, make_trades
from test_pg_screenshots import make_client, upload

from tradejournal.bucketstore import BucketFileStore, BucketSettings
from tradejournal.pg import sweep

ACCESS = "test" + "accesskey0123456789"
SECRET = "test" + "secret/with+odd=chars"  # noqa: S105 - made up for the fake server
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def server():
    bucket = FakeBucket(access_key=ACCESS, secret=SECRET)
    yield bucket
    bucket.stop()


@pytest.fixture
def settings(server):
    return BucketSettings(server.url, server.bucket, ACCESS, SECRET)


@pytest.fixture
def trade(db, alice):
    with db.tenant_session(alice) as conn:
        return make_trades(conn, make_account(conn, "Apex 07").id, 2)[0]


@pytest.fixture
def client(db, system, alice, settings):
    return make_client(db, system, alice, None, bucket=settings)


def rows(db, user) -> int:
    with db.tenant_session(user) as conn:
        return conn.execute("SELECT count(*) AS n FROM screenshots").fetchone()["n"]


def test_an_upload_lands_in_the_bucket_under_the_persons_folder_and_comes_back_through_the_server(
    client, server, trade, alice
):
    created = upload(client, trade)
    assert created.status_code == 201
    [key] = server.keys()
    assert key.startswith(f"u/{alice}/") and key.endswith(".png")
    assert server.objects[key][0] == PNG_1X1
    image = client.get(
        created.json()["src"]
    )  # the picture is read through the server: no link to the bucket is ever given
    assert image.status_code == 200 and image.content == PNG_1X1
    assert image.headers["content-type"] == "image/png"
    assert server.url not in created.text and server.bucket not in created.text


def test_deleting_a_screenshot_removes_its_object(client, server, trade):
    shot = upload(client, trade).json()
    left = client.delete(f"/api/v1/trades/{trade}/screenshots/{shot['id']}")
    assert left.status_code == 200 and left.json() == []
    assert server.keys() == []


def test_a_file_that_has_gone_from_the_bucket_is_the_same_404_in_words_as_from_a_folder(client, server, trade):
    shot = upload(client, trade).json()
    server.objects.clear()
    gone = client.get(shot["src"])
    assert gone.status_code == 404 and "missing" in gone.json()["detail"]


def test_a_bucket_that_is_down_refuses_an_upload_in_words_and_keeps_nothing(client, server, db, alice, trade):
    server.fail_next(503, 3)
    refused = upload(client, trade)
    assert refused.status_code == 503
    assert refused.headers["content-type"] == "application/problem+json" and refused.headers["retry-after"] == "10"
    assert "pictures" in refused.json()["detail"] and server.url not in refused.text and SECRET not in refused.text
    assert rows(db, alice) == 0 and server.keys() == []
    assert upload(client, trade).status_code == 201  # and it works when the bucket is back


def test_a_bucket_that_is_down_refuses_a_picture_in_words_too(client, server, trade):
    shot = upload(client, trade).json()
    server.fail_next(500, 3)
    refused = client.get(shot["src"])
    assert refused.status_code == 503 and refused.headers["retry-after"] == "10"
    assert client.get(shot["src"]).status_code == 200


def test_a_delete_the_person_asked_for_is_not_undone_by_a_bucket_that_is_down(client, server, db, alice, trade):
    shot = upload(client, trade).json()
    server.fail_next(500, 3)  # the database delete commits; the file's removal then fails
    left = client.delete(f"/api/v1/trades/{trade}/screenshots/{shot['id']}")
    assert left.status_code == 200 and left.json() == []
    assert rows(db, alice) == 0
    assert (
        len(server.keys()) == 1
    )  # the object is left behind for the orphan report, and the person is not shown an error


def test_a_failure_while_recording_takes_the_object_out_again(client, server, db, alice, trade, monkeypatch):
    from tradejournal.pg import usage

    def broken(conn):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(usage, "refresh", broken)
    with pytest.raises(RuntimeError):
        upload(client, trade)
    assert server.keys() == []


def test_a_failure_while_recording_with_the_bucket_down_still_shows_the_real_failure(
    client, server, trade, monkeypatch
):
    from tradejournal.pg import usage

    def broken(conn):
        server.fail_next(500, 3)  # the cleanup that follows cannot reach the bucket
        raise RuntimeError("the database went away")

    monkeypatch.setattr(usage, "refresh", broken)
    with pytest.raises(RuntimeError, match="the database went away"):  # not the bucket's error hiding it
        upload(client, trade)


# ---- the sweep job, a separate process ----------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, sweep_url, settings):
    for name in ("TJ_KINDE_DOMAIN", "TJ_KINDE_M2M_CLIENT_ID", "TJ_KINDE_M2M_CLIENT_SECRET", "TJ_FILES_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TJ_SWEEP_DATABASE_URL", sweep_url)
    monkeypatch.setenv("TJ_S3_ENDPOINT", settings.endpoint)
    monkeypatch.setenv("TJ_S3_BUCKET", settings.bucket)
    monkeypatch.setenv("TJ_S3_ACCESS_KEY_ID", settings.access_key_id)
    monkeypatch.setenv("TJ_S3_SECRET_ACCESS_KEY", settings.secret_access_key)
    return monkeypatch


def ask_for_deletion(system, user):
    from tradejournal.pg import deletion

    with system.system_session("login") as conn:
        deletion.request(conn, user, now=NOW - timedelta(days=8))


def test_the_sweep_job_removes_a_deleted_persons_objects_from_the_bucket_and_nobody_elses(
    env, capsys, server, system, owner, alice, bob
):
    for owner_id in (alice, bob):
        for n in range(3):
            server.put_directly(f"u/{owner_id}/{n}.png", b"x")
    ask_for_deletion(system, alice)
    assert sweep.main(["deletions", "--apply", "--without-identity-provider"]) == 0
    captured = capsys.readouterr()
    assert "file folders removed 1, file folders still to remove 0" in captured.out
    assert server.keys() == [f"u/{bob}/{n}.png" for n in range(3)]
    assert server.url not in captured.out + captured.err and SECRET not in captured.out + captured.err


def test_a_bucket_that_is_down_leaves_the_folder_written_down_for_the_next_run(
    env, capsys, server, system, owner, alice
):
    server.put_directly(f"u/{alice}/1.png", b"x")
    ask_for_deletion(system, alice)
    server.fail_next(500, 3)
    assert sweep.main(["deletions", "--apply", "--without-identity-provider"]) == 0
    assert "file folders removed 0, file folders still to remove 1" in capsys.readouterr().out
    assert server.keys() == [f"u/{alice}/1.png"]
    assert sweep.main(["deletions", "--apply", "--without-identity-provider"]) == 0  # the next run finishes the job
    assert "file folders removed 1, file folders still to remove 0" in capsys.readouterr().out
    assert server.keys() == []


def test_a_half_set_bucket_stops_the_job_with_the_names_of_the_settings(env, capsys, system, owner, alice):
    env.delenv("TJ_S3_BUCKET")
    ask_for_deletion(system, alice)
    assert sweep.main(["deletions", "--apply", "--without-identity-provider"]) == 2
    err = capsys.readouterr().err
    assert "go together" in err and SECRET not in err


def test_the_store_the_job_builds_is_a_bucket_store(env):
    assert isinstance(sweep._files_from_env(), BucketFileStore)
