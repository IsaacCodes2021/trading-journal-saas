"""The file store (filestore.py): a folder of files under keys the server makes, and keys that cannot escape it."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from tradejournal import filestore
from tradejournal.filestore import BadKey, FileMissing, LocalFileStore, check_key


@pytest.fixture
def store(tmp_path):
    return LocalFileStore(tmp_path / "files")


def test_a_file_comes_back_as_it_went_in(store):
    store.put("u/abc/one.png", b"\x89PNG data")
    assert store.get("u/abc/one.png") == b"\x89PNG data"


def test_the_folder_is_made_when_it_is_missing(tmp_path):
    root = tmp_path / "does" / "not" / "exist"
    LocalFileStore(root).put("a.png", b"x")
    assert (root / "a.png").read_bytes() == b"x"


def test_writing_again_replaces_the_file(store):
    store.put("a.png", b"first")
    store.put("a.png", b"second")
    assert store.get("a.png") == b"second"


def test_a_missing_file_is_a_file_missing_error_and_not_a_bare_os_error(store):
    with pytest.raises(FileMissing):
        store.get("u/abc/none.png")
    assert issubclass(FileMissing, LookupError)


def test_deleting_removes_the_file_and_deleting_a_missing_one_is_fine(store):
    store.put("a.png", b"x")
    store.delete("a.png")
    store.delete("a.png")
    with pytest.raises(FileMissing):
        store.get("a.png")


def test_a_failed_write_leaves_no_partial_file_under_the_key_or_beside_it(store, monkeypatch):
    store.put("u/abc/one.png", b"old")

    def broken(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        store.put("u/abc/one.png", b"new")
    monkeypatch.undo()
    assert store.get("u/abc/one.png") == b"old"
    assert [f.key for f in store.list()] == ["u/abc/one.png"]
    assert not [p for p in (store.root / "u" / "abc").iterdir() if p.name.startswith(".part-")]


def test_a_write_is_one_step_a_reader_never_sees_part_of_a_file(store, monkeypatch):
    seen = []
    real = os.replace

    def spy(source, target):
        seen.append((os.path.basename(source).startswith(".part-"), os.path.exists(target)))
        real(source, target)

    monkeypatch.setattr(os, "replace", spy)
    store.put("a.png", b"data")
    assert seen == [(True, False)]  # written under a temporary name, then renamed onto the real one


# ---- keys -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../x.png",
        "a/../b.png",
        "a/..",
        "/etc/passwd",
        "/a.png",
        "a//b.png",
        "a/",
        ".hidden",
        "a/.hidden",
        "A/b.png",
        "a\\b.png",
        "a b.png",
        "a\x00b.png",
        "a/b.png/..",
        "a..b.png",  # two dots anywhere are refused, not only as a whole folder name
        "a/b..png",
        "é.png",
        "a" * 121,
        "C:/x.png",
        "a/b:c",
    ],
)
def test_a_key_that_is_not_a_plain_relative_lower_case_path_is_refused(store, key):
    with pytest.raises(BadKey):
        check_key(key)
    with pytest.raises(BadKey):
        store.put(key, b"x")
    with pytest.raises(BadKey):
        store.get(key)
    with pytest.raises(BadKey):
        store.delete(key)


@pytest.mark.parametrize(
    "key",
    ["a.png", "u/0b6e2c1a-4f7d-4a55-9c1e-7d2f3a8b9e10/5f2c0a.jpg", "u/x/y/z.webp", "a" * 120, "a-b_c.d/e.f"],
)
def test_the_keys_the_server_makes_are_accepted(store, key):
    assert check_key(key) == key
    store.put(key, b"x")
    assert store.get(key) == b"x"


def test_the_longest_key_is_a_hundred_and_twenty_characters():
    assert filestore.MAX_KEY_LENGTH == 120
    check_key("a" * 120)
    with pytest.raises(BadKey):
        check_key("a" * 121)


def test_a_folder_that_is_a_link_to_somewhere_else_is_not_followed_out_of_the_store(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"secret")
    store = LocalFileStore(tmp_path / "files")
    try:
        os.symlink(outside, store.root / "u", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine does not allow links")
    with pytest.raises(BadKey):
        store.get("u/secret.png")
    with pytest.raises(BadKey):
        store.put("u/new.png", b"x")
    assert not (outside / "new.png").exists()


def test_a_refusal_writes_nothing_outside_the_folder(tmp_path):
    inner = LocalFileStore(tmp_path / "files")
    with pytest.raises(BadKey):
        inner.put("../escaped.png", b"x")
    assert not (tmp_path / "escaped.png").exists()


# ---- prefixes and listing -----------------------------------------------------------------------------------------


def test_everything_under_a_prefix_can_be_deleted_and_nothing_else(store):
    for key in ("u/aaa/1.png", "u/aaa/2.png", "u/aaa/deeper/3.png", "u/bbb/1.png"):
        store.put(key, b"x")
    assert store.delete_prefix("u/aaa/") == 3
    assert [f.key for f in store.list()] == ["u/bbb/1.png"]
    assert not (store.root / "u" / "aaa").exists()  # the folder goes too


def test_deleting_a_prefix_nobody_has_is_zero(store):
    assert store.delete_prefix("u/nobody/") == 0


def test_a_prefix_is_not_a_substring_match(store):
    store.put("u/aaa/1.png", b"x")
    store.put("u/aaab/1.png", b"x")
    assert store.delete_prefix("u/aaa/") == 1
    assert [f.key for f in store.list()] == ["u/aaab/1.png"]


def test_listing_gives_keys_sizes_and_times_in_order(store):
    store.put("u/b/2.png", b"12345")
    store.put("u/a/1.png", b"1")
    listed = list(store.list())
    assert [(f.key, f.size) for f in listed] == [("u/a/1.png", 1), ("u/b/2.png", 5)]
    assert all(abs(datetime.now(UTC) - f.modified) < timedelta(minutes=1) for f in listed)
    assert all(f.modified.tzinfo is not None for f in listed)


def test_listing_can_be_limited_to_a_prefix(store):
    store.put("u/a/1.png", b"x")
    store.put("u/b/1.png", b"x")
    assert [f.key for f in store.list("u/a/")] == ["u/a/1.png"]
    assert list(store.list("u/zzz/")) == []


def test_listing_skips_the_temporary_files_of_a_write_in_progress(store):
    store.put("u/a/1.png", b"x")
    (store.root / "u" / "a" / ".part-abc").write_bytes(b"half")
    assert [f.key for f in store.list()] == ["u/a/1.png"]


def test_an_empty_store_lists_nothing(store):
    assert list(store.list()) == []
