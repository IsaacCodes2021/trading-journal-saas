"""The second copy of the screenshot bucket (filebackup.py): what is copied, what is forgotten and when, and what it refuses."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fake_s3 import FakeBucket

from tradejournal import filebackup
from tradejournal.bucketstore import StoreUnavailable
from tradejournal.filebackup import STATE_KEY, run
from tradejournal.filestore import FileMissing, LocalFileStore

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
ACCESS = "test" + "accesskey0123456789"
SECRET = "test" + "secret/with+odd=chars"  # noqa: S105 - made up for the fake server
ONE, TWO, THREE = "u/aaa/one.png", "u/aaa/two.png", "u/bbb/three.png"


@pytest.fixture
def source(tmp_path):
    return LocalFileStore(tmp_path / "source")


@pytest.fixture
def copy(tmp_path):
    return LocalFileStore(tmp_path / "copy")


def keys(store, prefix="u"):
    return sorted(f.key for f in store.list(prefix))


def go(source, copy, at=T0, **kw):
    kw.setdefault("apply", True)
    return run(source, copy, now=lambda: at, **kw)


# ---- copying ------------------------------------------------------------------------------------------------------------


def test_a_dry_run_counts_and_changes_nothing(source, copy):
    source.put(ONE, b"1")
    report = go(source, copy, apply=False)
    assert (report.in_source, report.to_copy, report.copied, report.dry_run) == (1, 1, 0, True)
    assert keys(copy) == [] and not list(copy.list("state"))


def test_what_the_copy_lacks_is_copied_byte_for_byte_and_a_second_run_has_nothing_to_do(source, copy):
    source.put(ONE, b"first")
    source.put(THREE, b"third")
    first = go(source, copy)
    assert (first.to_copy, first.copied) == (2, 2)
    assert copy.get(ONE) == b"first" and copy.get(THREE) == b"third"
    second = go(source, copy)
    assert (second.to_copy, second.copied, second.purged) == (0, 0, 0)


def test_only_as_many_as_the_limit_are_copied_in_one_run_and_the_rest_next_time(source, copy):
    for key in (ONE, TWO, THREE):
        source.put(key, b"x")
    assert go(source, copy, limit=2).copied == 2
    assert go(source, copy, limit=2).copied == 1
    assert keys(copy) == sorted([ONE, TWO, THREE])


def test_a_picture_with_the_wrong_size_in_the_copy_is_copied_again(source, copy):
    source.put(ONE, b"the real one")
    copy.put(ONE, b"torn")
    assert go(source, copy).copied == 1
    assert copy.get(ONE) == b"the real one"


def test_a_picture_deleted_between_the_listing_and_the_read_is_counted_not_fatal(source, copy):
    source.put(ONE, b"1")
    source.put(TWO, b"2")

    class Racy:
        def list(self, prefix=""):
            return source.list(prefix)

        def get(self, key):
            if key == ONE:
                raise FileMissing(key)
            return source.get(key)

    report = run(Racy(), copy, apply=True, now=lambda: T0)
    assert (report.copied, report.vanished) == (1, 1)
    assert keys(copy) == [TWO]


# ---- forgetting ---------------------------------------------------------------------------------------------------------


def test_a_picture_gone_from_the_source_is_kept_in_the_copy_and_its_clock_is_written_down(source, copy):
    source.put(ONE, b"1")
    source.put(TWO, b"2")
    go(source, copy)
    source.delete(ONE)
    report = go(source, copy, at=T0 + timedelta(days=1))
    assert (report.gone_from_source, report.purged) == (1, 0)
    assert copy.get(ONE) == b"1"
    assert json.loads(copy.get(STATE_KEY)) == {ONE: (T0 + timedelta(days=1)).isoformat()}


def test_the_clock_runs_from_the_first_time_it_was_seen_missing_not_from_the_latest_run(source, copy):
    source.put(ONE, b"1")
    source.put(TWO, b"2")
    go(source, copy)
    source.delete(ONE)
    go(source, copy, at=T0 + timedelta(days=1))
    go(source, copy, at=T0 + timedelta(days=20))
    assert json.loads(copy.get(STATE_KEY)) == {ONE: (T0 + timedelta(days=1)).isoformat()}
    assert go(source, copy, at=T0 + timedelta(days=30)).purged == 0  # 29 days since it was first seen missing
    report = go(source, copy, at=T0 + timedelta(days=31))
    assert report.purged == 1 and ONE not in keys(copy)
    assert json.loads(copy.get(STATE_KEY)) == {}


def test_the_keep_time_is_a_setting(source, copy):
    source.put(ONE, b"1")
    source.put(TWO, b"2")
    go(source, copy)
    source.delete(ONE)
    go(source, copy, at=T0, keep_days=3)
    assert go(source, copy, at=T0 + timedelta(days=2, hours=23), keep_days=3).purged == 0
    assert go(source, copy, at=T0 + timedelta(days=3), keep_days=3).purged == 1


def test_a_picture_that_comes_back_in_the_source_is_forgotten_by_the_clock(source, copy):
    source.put(ONE, b"1")
    source.put(TWO, b"2")
    go(source, copy)
    source.delete(ONE)
    go(source, copy, at=T0 + timedelta(days=5))
    source.put(ONE, b"1")
    go(source, copy, at=T0 + timedelta(days=6))
    assert json.loads(copy.get(STATE_KEY)) == {}
    assert go(source, copy, at=T0 + timedelta(days=60)).purged == 0 and copy.get(ONE) == b"1"


def test_a_source_that_lists_nothing_never_makes_it_forget_a_thing(source, copy):
    copy.put(ONE, b"1")
    copy.put(STATE_KEY, json.dumps({ONE: T0.isoformat()}).encode())
    report = go(source, copy, at=T0 + timedelta(days=400))
    assert report.refused and report.purged == 0
    assert copy.get(ONE) == b"1"


def test_no_more_than_the_cap_is_forgotten_in_one_run(source, copy):
    source.put(THREE, b"stays")
    for name in "abcde":
        copy.put(f"u/aaa/{name}.png", b"x")
    copy.put(STATE_KEY, json.dumps({f"u/aaa/{n}.png": T0.isoformat() for n in "abcde"}).encode())
    report = go(source, copy, at=T0 + timedelta(days=31), max_purge=2)
    assert (report.purged, report.purge_waiting) == (2, 3)
    assert len(keys(copy)) == 1 + 3  # the picture the source has, and three still waiting
    assert go(source, copy, at=T0 + timedelta(days=32), max_purge=10).purged == 3


def test_a_dry_run_forgets_nothing_and_writes_no_clock(source, copy):
    source.put(TWO, b"2")
    copy.put(ONE, b"1")
    copy.put(STATE_KEY, json.dumps({ONE: T0.isoformat()}).encode())
    report = go(source, copy, at=T0 + timedelta(days=40), apply=False)
    assert report.purged == 0 and report.gone_from_source == 1
    assert copy.get(ONE) == b"1" and json.loads(copy.get(STATE_KEY)) == {ONE: T0.isoformat()}


def test_a_state_file_that_cannot_be_read_restarts_the_clocks_and_forgets_nothing(source, copy):
    source.put(TWO, b"2")
    copy.put(ONE, b"1")
    copy.put(STATE_KEY, b"{ not json")
    report = go(source, copy, at=T0 + timedelta(days=400))
    assert report.purged == 0 and copy.get(ONE) == b"1"
    assert json.loads(copy.get(STATE_KEY)) == {ONE: (T0 + timedelta(days=400)).isoformat()}


def test_the_state_file_is_never_taken_for_a_picture(source, copy):
    source.put(ONE, b"1")
    source.put(TWO, b"2")
    go(source, copy)
    source.delete(ONE)
    go(source, copy, at=T0 + timedelta(days=1))
    report = go(source, copy, at=T0 + timedelta(days=2))
    assert STATE_KEY not in keys(copy, "u") and report.gone_from_source == 1


# ---- the command: settings, refusals and real HTTP ----------------------------------------------------------------------


@pytest.fixture
def two_buckets():
    one = FakeBucket(access_key=ACCESS, secret=SECRET, bucket="roundturn-files")
    two = FakeBucket(access_key=ACCESS, secret=SECRET, bucket="roundturn-copy")
    yield one, two
    one.stop()
    two.stop()


def env(monkeypatch, source=None, copy=None, **extra):
    for name in (
        "TJ_S3_ENDPOINT", "TJ_S3_BUCKET", "TJ_S3_ACCESS_KEY_ID", "TJ_S3_SECRET_ACCESS_KEY", "TJ_S3_REGION", "TJ_FILES_DIR",
        "TJ_BACKUP_S3_ENDPOINT", "TJ_BACKUP_S3_BUCKET", "TJ_BACKUP_S3_ACCESS_KEY_ID", "TJ_BACKUP_S3_SECRET_ACCESS_KEY",
        "TJ_BACKUP_S3_REGION",
    ):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    for prefix, server in (("TJ_S3_", source), ("TJ_BACKUP_S3_", copy)):
        if server is not None:
            monkeypatch.setenv(prefix + "ENDPOINT", server.url)
            monkeypatch.setenv(prefix + "BUCKET", server.bucket)
            monkeypatch.setenv(prefix + "ACCESS_KEY_ID", ACCESS)
            monkeypatch.setenv(prefix + "SECRET_ACCESS_KEY", SECRET)
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def test_the_command_copies_one_real_bucket_to_another_and_prints_only_counts(monkeypatch, two_buckets, capsys):
    one, two = two_buckets
    one.objects[ONE] = (b"hello", T0)
    one.objects[THREE] = (b"world", T0)
    env(monkeypatch, one, two)
    assert filebackup.main(["--apply"]) == 0
    assert {k: v[0] for k, v in two.objects.items()} == {ONE: b"hello", THREE: b"world"}
    shown = capsys.readouterr()
    assert "copied 2" in shown.out
    assert (
        "aaa" not in shown.out + shown.err
        and ACCESS not in shown.out + shown.err
        and SECRET not in shown.out + shown.err
    )


def test_the_command_without_apply_touches_nothing(monkeypatch, two_buckets):
    one, two = two_buckets
    one.objects[ONE] = (b"hello", T0)
    env(monkeypatch, one, two)
    assert filebackup.main([]) == 0
    assert two.objects == {}


def test_the_same_bucket_as_source_and_copy_is_refused_before_anything_runs(monkeypatch, two_buckets, capsys):
    one, _ = two_buckets
    one.objects[ONE] = (b"hello", T0)
    env(monkeypatch, one, one)
    assert filebackup.main(["--apply"]) == 2
    assert "same bucket" in capsys.readouterr().err
    assert one.requests == []


@pytest.mark.parametrize(
    ("which", "words"),
    [("source", "no source"), ("copy", "nowhere to copy")],
)
def test_a_missing_side_is_named(monkeypatch, two_buckets, capsys, which, words):
    one, two = two_buckets
    env(monkeypatch, one if which == "copy" else None, two if which == "source" else None)
    assert filebackup.main(["--apply"]) == 2
    assert words in capsys.readouterr().err


def test_a_half_set_copy_is_refused_naming_the_backup_settings_not_the_sources(monkeypatch, two_buckets, capsys):
    one, _ = two_buckets
    env(monkeypatch, one, None, TJ_BACKUP_S3_BUCKET="roundturn-copy")
    assert filebackup.main(["--apply"]) == 2
    err = capsys.readouterr().err
    assert "TJ_BACKUP_S3_ENDPOINT" in err and "go together" in err


@pytest.mark.parametrize("args", [["--keep-days", "0"], ["--limit", "0"], ["--max-purge", "-1"]])
def test_nonsense_numbers_are_refused(monkeypatch, two_buckets, args):
    env(monkeypatch, *two_buckets)
    assert filebackup.main(["--apply", *args]) == 2


def test_a_bucket_that_does_not_answer_is_a_plain_message_and_exit_1(monkeypatch, two_buckets, capsys):
    env(monkeypatch, *two_buckets)

    def broken(*args, **kwargs):
        raise StoreUnavailable("secret details that must not be shown")

    monkeypatch.setattr(filebackup, "run", broken)
    assert filebackup.main(["--apply"]) == 1
    err = capsys.readouterr().err
    assert "did not answer" in err and "secret details" not in err


def test_a_source_that_lists_nothing_while_the_copy_holds_files_exits_1_and_forgets_nothing(
    monkeypatch, two_buckets, capsys
):
    one, two = two_buckets
    two.objects[ONE] = (b"kept", T0)
    two.objects[STATE_KEY] = (json.dumps({ONE: "2020-01-01T00:00:00+00:00"}).encode(), T0)
    env(monkeypatch, one, two)
    assert filebackup.main(["--apply"]) == 1
    assert ONE in two.objects
    assert "nothing was forgotten" in capsys.readouterr().err


def test_a_dry_run_on_a_picture_newly_gone_writes_no_clock(source, copy):
    source.put(TWO, b"2")
    copy.put(ONE, b"1")
    go(source, copy, apply=False)
    assert not list(copy.list("state"))
