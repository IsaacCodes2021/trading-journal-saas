"""The bucket file store (bucketstore.py), over real HTTP against a fake S3 server (fake_s3.py).

The first group is the contract every file store keeps, run against the folder store and the bucket store alike, so the
two cannot drift apart. The rest is what only a network store has: signing, retries, refusals, timeouts, paging and settings.
"""

import http.client
from datetime import UTC, datetime, timedelta

import pytest
from fake_s3 import FakeBucket

from tradejournal.bucketstore import BucketFileStore, BucketSettings, StoreUnavailable
from tradejournal.filestore import BadKey, FileMissing, LocalFileStore

ACCESS = "test" + "accesskey0123456789"
SECRET = "test" + "secret/with+odd=chars"  # noqa: S105 - made up for the fake server
WRONG = "wrong" + "-secret"


@pytest.fixture
def server():
    bucket = FakeBucket(access_key=ACCESS, secret=SECRET)
    yield bucket
    bucket.stop()


def settings_for(server: FakeBucket, **over) -> BucketSettings:
    values = {"endpoint": server.url, "bucket": server.bucket, "access_key_id": ACCESS, "secret_access_key": SECRET}
    return BucketSettings(**(values | over))


@pytest.fixture
def bucket_store(server):
    return BucketFileStore(settings_for(server), sleep=lambda seconds: None, page_size=3)


@pytest.fixture(params=["folder", "bucket"])
def store(request, tmp_path, server):
    if request.param == "folder":
        return LocalFileStore(tmp_path / "files")
    return BucketFileStore(settings_for(server), sleep=lambda seconds: None, page_size=3)


# ---- the contract of every store ----------------------------------------------------------------------------------------


def test_a_file_comes_back_as_it_went_in(store):
    data = bytes(range(256)) * 400
    store.put("u/abc/one.png", data)
    assert store.get("u/abc/one.png") == data


def test_an_empty_file_is_a_file(store):
    store.put("u/abc/empty.png", b"")
    assert store.get("u/abc/empty.png") == b""
    assert [f.size for f in store.list("u/abc")] == [0]


def test_writing_again_replaces_the_file(store):
    store.put("a.png", b"first")
    store.put("a.png", b"second, and longer")
    assert store.get("a.png") == b"second, and longer"
    assert [f.key for f in store.list()] == ["a.png"]


def test_a_missing_file_is_file_missing(store):
    with pytest.raises(FileMissing):
        store.get("u/abc/none.png")


def test_deleting_removes_the_file_and_deleting_a_missing_one_is_fine(store):
    store.put("a.png", b"x")
    store.delete("a.png")
    store.delete("a.png")
    with pytest.raises(FileMissing):
        store.get("a.png")


def test_deleting_a_prefix_removes_that_persons_files_and_no_others(store):
    for key in (
        "u/abc/1.png",
        "u/abc/2.png",
        "u/abc/3.png",
        "u/abc/4.png",
        "u/abcd/1.png",
        "u/ab/1.png",
        "u/zzz/1.png",
    ):
        store.put(key, key.encode())
    assert store.delete_prefix("u/abc/") == 4
    assert [f.key for f in store.list()] == ["u/ab/1.png", "u/abcd/1.png", "u/zzz/1.png"]  # a longer id is not a match
    assert store.delete_prefix("u/abc") == 0  # nothing left, with or without the slash
    assert store.delete_prefix("u/nobody/") == 0


def test_an_empty_prefix_is_refused_so_a_bug_cannot_empty_the_store(store):
    store.put("u/abc/1.png", b"x")
    for prefix in ("", "/", "//"):
        with pytest.raises(BadKey):
            store.delete_prefix(prefix)
    assert [f.key for f in store.list()] == ["u/abc/1.png"]


def test_the_listing_gives_each_files_key_size_and_time_in_utc(store):
    before = datetime.now(UTC) - timedelta(seconds=5)
    store.put("u/abc/one.png", b"12345")
    store.put("u/abc/two.png", b"1234567")
    store.put("u/other/three.png", b"1")
    listed = list(store.list("u/abc"))
    assert [(f.key, f.size) for f in listed] == [("u/abc/one.png", 5), ("u/abc/two.png", 7)]
    for f in listed:
        assert f.modified.tzinfo is not None and f.modified.utcoffset() == timedelta(0)
        assert before <= f.modified <= datetime.now(UTC) + timedelta(seconds=5)
    assert [f.key for f in store.list()] == ["u/abc/one.png", "u/abc/two.png", "u/other/three.png"]
    assert list(store.list("u/nobody")) == []


@pytest.mark.parametrize(
    "key", ["", "../x.png", "u/../x.png", "/abs.png", "UPPER.png", "a b.png", "u//x.png", "a" * 121]
)
def test_a_key_the_server_would_never_make_is_refused_for_every_operation(store, key):
    with pytest.raises(BadKey):
        store.put(key, b"x")
    with pytest.raises(BadKey):
        store.get(key)
    with pytest.raises(BadKey):
        store.delete(key)


# ---- only a bucket ------------------------------------------------------------------------------------------------------


def test_a_refused_key_never_reaches_the_network(bucket_store, server):
    for key in ("../x.png", "UPPER.png", ""):
        with pytest.raises(BadKey):
            bucket_store.put(key, b"x")
    with pytest.raises(BadKey):
        bucket_store.delete_prefix("")
    assert server.requests == [] and server.refused == []


def test_the_bucket_holds_the_file_under_the_key_and_nothing_else(bucket_store, server):
    bucket_store.put("u/0f8fad5b-d9cb-469f-a165-9a84baee0a31/a.b-c_d.png", b"data")
    assert server.keys() == ["u/0f8fad5b-d9cb-469f-a165-9a84baee0a31/a.b-c_d.png"]
    assert server.objects["u/0f8fad5b-d9cb-469f-a165-9a84baee0a31/a.b-c_d.png"][0] == b"data"


def test_a_big_upload_goes_through_with_its_hash_checked(bucket_store, server):
    data = bytes(i % 251 for i in range(5 * 1024 * 1024))
    bucket_store.put("u/abc/big.png", data)
    assert bucket_store.get("u/abc/big.png") == data
    assert server.refused == []


def test_a_listing_is_read_a_page_at_a_time_in_order(server):
    store = BucketFileStore(settings_for(server), sleep=lambda seconds: None, page_size=3)
    keys = [f"u/abc/{n}.png" for n in range(8)]
    for key in keys:
        server.put_directly(key, b"x")
    assert [f.key for f in store.list("u/abc")] == keys
    lists = [q for method, key, q in server.requests if method == "GET" and not key]
    assert len(lists) == 3 and lists[0].get("continuation-token") is None  # 3 + 3 + 2
    assert all(q["max-keys"] == "3" and q["prefix"] == "u/abc/" for q in lists)
    assert (
        lists[1]["continuation-token"] and lists[2]["continuation-token"]
    )  # tokens with slashes and equals signs survive


def test_deleting_a_prefix_with_more_files_than_a_page_removes_them_all(server):
    store = BucketFileStore(settings_for(server), sleep=lambda seconds: None, page_size=2)
    for n in range(7):
        server.put_directly(f"u/abc/{n}.png", b"x")
    server.put_directly("u/zzz/keep.png", b"x")
    assert store.delete_prefix("u/abc/") == 7
    assert server.keys() == ["u/zzz/keep.png"]


def test_a_wrong_secret_is_refused_once_and_not_retried(server):
    store = BucketFileStore(settings_for(server, secret_access_key=WRONG), sleep=lambda seconds: pytest.fail("retried"))
    with pytest.raises(StoreUnavailable) as caught:
        store.put("a.png", b"x")
    assert "403" in str(caught.value) and SECRET not in str(caught.value) and WRONG not in str(caught.value)
    assert server.refused == ["signature"] and server.keys() == []


def test_an_unknown_access_key_is_refused(server):
    store = BucketFileStore(settings_for(server, access_key_id="someoneelse"), sleep=lambda seconds: None)
    with pytest.raises(StoreUnavailable):
        store.get("a.png")
    assert server.refused == ["unknown key"]


def test_a_bucket_that_is_not_there_is_never_read_as_a_missing_file_or_a_finished_deletion(server):
    store = BucketFileStore(settings_for(server, bucket="some-other-bucket"), sleep=lambda seconds: None)
    for action in (
        lambda: store.get("a.png"),  # not "that picture is gone"
        lambda: store.delete("a.png"),  # not "deleted"
        lambda: store.delete_prefix("u/abc/"),  # not "this person's files are removed": the sweep would stop retrying
        lambda: list(store.list("u/abc")),
        lambda: store.put("a.png", b"x"),
    ):
        with pytest.raises(StoreUnavailable):
            action()


def test_only_a_404_that_says_the_key_is_missing_is_a_missing_file(server):
    store = BucketFileStore(settings_for(server), sleep=lambda seconds: None)
    server.fail_next(404)  # a 404 with some other code
    with pytest.raises(StoreUnavailable):
        store.get("a.png")
    with pytest.raises(FileMissing):  # and the real thing
        store.get("a.png")


def test_a_clock_far_from_the_servers_is_refused(server):
    long_ago = lambda: datetime.now(UTC) - timedelta(hours=2)  # noqa: E731
    store = BucketFileStore(settings_for(server), clock=long_ago, sleep=lambda seconds: None)
    with pytest.raises(StoreUnavailable):
        store.put("a.png", b"x")
    assert server.refused == ["clock skew"]


def test_a_server_error_is_tried_again_after_a_growing_pause(server):
    pauses: list[float] = []
    store = BucketFileStore(settings_for(server), sleep=pauses.append)
    server.fail_next(500, 2)
    store.put("a.png", b"x")
    assert store.get("a.png") == b"x"
    assert pauses == [0.25, 0.5]


def test_a_busy_answer_is_tried_again_too(server):
    store = BucketFileStore(settings_for(server), sleep=lambda seconds: None)
    server.fail_next(429)
    store.put("a.png", b"x")
    assert server.keys() == ["a.png"]


def test_a_server_that_keeps_failing_is_an_error_after_three_tries(server):
    pauses: list[float] = []
    store = BucketFileStore(settings_for(server), sleep=pauses.append)
    server.fail_next(503, 3)
    with pytest.raises(StoreUnavailable, match="503"):
        store.put("a.png", b"x")
    assert pauses == [0.25, 0.5] and server.keys() == []
    store.put("a.png", b"x")  # and it works again when the server does


def test_a_dropped_connection_is_tried_again(server):
    store = BucketFileStore(settings_for(server), sleep=lambda seconds: None)
    server.drop_next(2)
    store.put("a.png", b"hello")
    assert store.get("a.png") == b"hello"


def test_a_server_that_cannot_be_reached_is_an_error_that_names_no_address(server, monkeypatch):
    def refuse(self):
        raise ConnectionRefusedError(10061, f"refused by {self.host}:{self.port}")  # a message that carries the address

    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)
    pauses: list[float] = []
    store = BucketFileStore(settings_for(server), sleep=pauses.append)
    with pytest.raises(StoreUnavailable) as caught:
        store.get("a.png")
    assert len(pauses) == 2  # three tries
    assert "ConnectionRefusedError" in str(caught.value)
    assert (
        server.host not in str(caught.value)
        and "127.0.0.1" not in str(caught.value)
        and "refused by" not in str(caught.value)
    )


def test_a_server_that_does_not_answer_in_time_is_an_error(server):
    store = BucketFileStore(settings_for(server), sleep=lambda seconds: None, attempts=1, timeout=0.3)
    server.slow_next(1.5)
    with pytest.raises(StoreUnavailable):
        store.get("a.png")


def test_the_attempts_can_be_set_and_never_go_below_one(server):
    store = BucketFileStore(settings_for(server), sleep=lambda seconds: None, attempts=0)
    server.fail_next(500)
    with pytest.raises(StoreUnavailable):
        store.put("a.png", b"x")  # one try, so the one arranged failure is the answer
    assert server.requests == []  # and a try really was made: it reached the server and was turned away
    store.put("a.png", b"x")
    assert server.keys() == ["a.png"]


def test_a_listing_that_cannot_be_read_is_an_error_and_not_an_empty_store(bucket_store, monkeypatch):
    monkeypatch.setattr(BucketFileStore, "_call", lambda self, *a, **k: (200, b"<html>not a listing"))
    with pytest.raises(StoreUnavailable):
        list(bucket_store.list("u/abc"))
    monkeypatch.setattr(
        BucketFileStore,
        "_call",
        lambda self, *a, **k: (200, b"<ListBucketResult><Contents><Key>a.png</Key></Contents></ListBucketResult>"),
    )
    with pytest.raises(StoreUnavailable):  # an entry with no size or time is not guessed at
        list(bucket_store.list())


def test_the_failure_is_an_os_error_so_code_that_catches_disk_errors_catches_it():
    assert issubclass(StoreUnavailable, OSError)


def test_a_store_that_can_be_asked_for_an_empty_prefix_listing_is_not_confused_by_a_trailing_slash(
    bucket_store, server
):
    server.put_directly("u/abc/1.png", b"x")
    assert (
        [f.key for f in bucket_store.list("u/abc/")] == [f.key for f in bucket_store.list("u/abc")] == ["u/abc/1.png"]
    )


# ---- the settings -------------------------------------------------------------------------------------------------------

GOOD = {
    "TJ_S3_ENDPOINT": "https://0123456789abcdef.r2.cloudflarestorage.com",
    "TJ_S3_BUCKET": "roundturn-files",
    "TJ_S3_ACCESS_KEY_ID": ACCESS,
    "TJ_S3_SECRET_ACCESS_KEY": SECRET,
}


def test_the_four_settings_make_a_bucket_and_the_region_defaults_to_auto():
    settings = BucketSettings.from_env(GOOD)
    assert settings is not None and settings.region == "auto" and settings.https
    assert settings.bucket == "roundturn-files"
    assert BucketSettings.from_env(GOOD | {"TJ_S3_REGION": "us-east-1"}).region == "us-east-1"
    assert BucketSettings.from_env(GOOD | {"TJ_S3_REGION": "  "}).region == "auto"


def test_no_settings_means_no_bucket_and_whitespace_counts_as_nothing():
    assert BucketSettings.from_env({}) is None
    assert BucketSettings.from_env({name: "  " for name in GOOD}) is None


@pytest.mark.parametrize("missing", sorted(GOOD))
def test_the_four_go_together(missing):
    env = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(ValueError, match="go together"):
        BucketSettings.from_env(env)
    with pytest.raises(ValueError, match="go together"):
        BucketSettings.from_env(GOOD | {missing: "   "})


def test_the_secret_is_never_in_the_text_of_the_settings():
    settings = BucketSettings.from_env(GOOD)
    assert SECRET not in repr(settings) and SECRET not in str(settings)


@pytest.mark.parametrize(
    "over",
    [
        {"endpoint": "ftp://x.example"},
        {"endpoint": "https://x.example/path"},
        {"endpoint": "https://x.example?x=1"},
        {"endpoint": "x.example"},
        {"bucket": "Upper"},
        {"bucket": "a"},
        {"bucket": "-bad"},
        {"bucket": "has space"},
        {"bucket": "a" * 64},
        {"region": "US EAST"},
        {"region": ""},
        {"access_key_id": ""},
        {"access_key_id": "has space"},
        {"secret_access_key": ""},
        {"secret_access_key": "has space"},
        {"secret_access_key": "line\nbreak"},
    ],
)
def test_a_setting_that_is_not_what_it_names_is_refused(over):
    values = {
        "endpoint": "https://x.example",
        "bucket": "roundturn-files",
        "access_key_id": ACCESS,
        "secret_access_key": SECRET,
    }
    with pytest.raises(ValueError):
        BucketSettings(**(values | over))


def test_plain_http_is_allowed_by_the_settings_and_is_for_the_config_to_forbid_in_production():
    assert not BucketSettings("http://127.0.0.1:9000", "roundturn-files", ACCESS, SECRET).https
