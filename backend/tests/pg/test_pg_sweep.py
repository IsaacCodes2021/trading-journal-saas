"""The deletion sweep, end to end on a real Postgres with a pretend identity provider (requirement F-A6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pg_helpers import PNG_1X1, make_account, make_trades
from test_pg_tenancy import TENANT_TABLES

from tradejournal.auth.idp import IdentityError
from tradejournal.filestore import FileMissing, LocalFileStore
from tradejournal.pg import deletion, sweep
from tradejournal.pg import screenshots as shots

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=8)  # asked eight days ago, so due since yesterday


class Directory:
    """A pretend provider: remembers what it was asked to remove, and can be made to fail."""

    def __init__(self) -> None:
        self.removed: list[str] = []
        self.fail_with: str | None = None
        self.refuse = False

    def delete_user(self, subject: str) -> bool:
        if self.fail_with:
            raise IdentityError(self.fail_with)
        if self.refuse:
            return False
        self.removed.append(subject)
        return True


def ask(system, user, at=LONG_AGO):
    with system.system_session("login") as conn:
        return deletion.request(conn, user, now=at)


def fill(db, user, name):
    with db.tenant_session(user) as conn:
        make_trades(conn, make_account(conn, name).id, 2)


def trades_of(db, user) -> int:
    with db.tenant_session(user) as conn:
        return conn.execute("SELECT count(*) AS n FROM trades").fetchone()["n"]


def users(owner) -> set:
    return {r["id"] for r in owner.execute("SELECT id FROM users")}


def removals(owner):
    return owner.execute(
        "SELECT idp_subject, attempts, last_error FROM identity_removals ORDER BY idp_subject"
    ).fetchall()


def events(owner):
    return [
        (r["action"], r["user_id"], r["actor"])
        for r in owner.execute("SELECT action, user_id, actor FROM audit_log ORDER BY id")
    ]


# ---- a dry run ------------------------------------------------------------------------------------------------------------


def test_a_dry_run_counts_what_is_due_and_changes_nothing(db, system, owner, alice, bob):
    fill(db, alice, "A")
    ask(system, alice)
    directory = Directory()
    report = sweep.run(system, directory, apply=False, now=NOW)
    assert (report.due, report.deleted, report.dry_run) == (1, 0, True)
    assert users(owner) == {alice, bob} and trades_of(db, alice) == 2
    assert directory.removed == [] and removals(owner) == [] and events(owner) == []


# ---- applying -------------------------------------------------------------------------------------------------------------


def test_it_deletes_what_is_due_records_it_and_removes_the_identity_at_the_provider(db, system, owner, alice, bob):
    fill(db, alice, "A")
    fill(db, bob, "B")
    ask(system, alice)
    directory = Directory()
    report = sweep.run(system, directory, apply=True, now=NOW)

    assert (report.due, report.deleted, report.skipped, report.identities_removed, report.identities_pending) == (
        1,
        1,
        0,
        1,
        0,
    )
    assert users(owner) == {bob} and trades_of(db, bob) == 2  # alice is gone, bob is untouched
    assert directory.removed == ["idp|alice@example.test"]
    assert removals(owner) == []  # written down in the same transaction, and forgotten once the provider confirmed
    assert events(owner) == [("account_deleted", None, "system")]  # the history stays, with the person detached


def test_nobody_who_is_not_due_is_touched(db, system, owner, alice, bob):
    ask(system, alice, at=NOW - timedelta(days=6))  # a day to go
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (bob,))
    report = sweep.run(system, Directory(), apply=True, now=NOW)
    assert (report.due, report.deleted) == (0, 0) and users(owner) == {alice, bob}


def test_a_person_who_signs_in_between_the_list_and_the_delete_is_skipped_and_leaves_no_trace(
    db, system, owner, alice, bob, monkeypatch
):
    fill(db, alice, "A")
    ask(system, alice)
    ask(system, bob)
    real = deletion.due

    def due_then_alice_signs_in(conn, **kwargs):
        listed = real(conn, **kwargs)
        owner.execute("UPDATE users SET status = 'active', deletion_scheduled_at = NULL WHERE id = %s", (alice,))
        return listed

    monkeypatch.setattr(sweep.deletion, "due", due_then_alice_signs_in)
    directory = Directory()
    report = sweep.run(system, directory, apply=True, now=NOW)

    assert (report.due, report.deleted, report.skipped) == (2, 1, 1)
    assert users(owner) == {alice} and trades_of(db, alice) == 2  # alice stays, with everything
    assert directory.removed == ["idp|bob@example.test"]  # only bob's identity is removed
    assert [e[0] for e in events(owner)] == [
        "account_deleted"
    ]  # one row: alice's attempt was rolled back with her skip
    assert [r["idp_subject"] for r in removals(owner)] == []


def test_at_most_the_limit_is_deleted_per_run_and_the_next_run_takes_the_rest(system, owner, alice, bob):
    ask(system, alice, LONG_AGO)
    ask(system, bob, LONG_AGO + timedelta(hours=1))
    directory = Directory()
    first = sweep.run(system, directory, apply=True, now=NOW, limit=1)
    assert (first.due, first.deleted) == (1, 1) and len(users(owner)) == 1
    second = sweep.run(system, directory, apply=True, now=NOW, limit=1)
    assert (second.due, second.deleted) == (1, 1) and users(owner) == set()
    assert sweep.run(system, directory, apply=True, now=NOW).due == 0


# ---- the provider fails: nothing is forgotten ------------------------------------------------------------------------------


def test_a_provider_failure_leaves_the_account_deleted_and_the_identity_written_down_for_next_time(
    db, system, owner, alice
):
    fill(db, alice, "A")
    ask(system, alice)
    directory = Directory()
    directory.fail_with = "management_network"
    report = sweep.run(system, directory, apply=True, now=NOW)

    assert (report.deleted, report.identities_removed, report.identities_pending) == (1, 0, 1)
    assert users(owner) == set()  # their data is gone whatever the provider did
    assert [(r["idp_subject"], r["attempts"], r["last_error"]) for r in removals(owner)] == [
        ("idp|alice@example.test", 1, "management_network")
    ]

    again = sweep.run(system, directory, apply=True, now=NOW)  # still down
    assert again.identities_pending == 1 and removals(owner)[0]["attempts"] == 2

    directory.fail_with = None  # back up
    done = sweep.run(system, directory, apply=True, now=NOW)
    assert (done.due, done.identities_removed, done.identities_pending) == (0, 1, 0)
    assert directory.removed == ["idp|alice@example.test"] and removals(owner) == []


def test_a_provider_that_answers_no_is_retried_like_a_failure(system, owner, alice):
    ask(system, alice)
    directory = Directory()
    directory.refuse = True
    report = sweep.run(system, directory, apply=True, now=NOW)
    assert report.identities_pending == 1 and removals(owner)[0]["last_error"] == "refused"


def test_with_no_provider_configured_the_data_is_deleted_and_every_identity_is_left_in_the_outbox(
    system, owner, alice, bob
):
    ask(system, alice)
    ask(system, bob)
    report = sweep.run(system, None, apply=True, now=NOW)
    assert (report.deleted, report.identities_removed, report.identities_pending) == (2, 0, 2)
    assert {r["idp_subject"] for r in removals(owner)} == {"idp|alice@example.test", "idp|bob@example.test"}


def test_one_failing_identity_does_not_stop_the_others(system, owner, alice, bob):
    ask(system, alice)
    ask(system, bob)

    class Picky(Directory):
        def delete_user(self, subject: str) -> bool:
            if "alice" in subject:
                raise IdentityError("management_lookup_500")
            return super().delete_user(subject)

    directory = Picky()
    report = sweep.run(system, directory, apply=True, now=NOW)
    assert (report.deleted, report.identities_removed, report.identities_pending) == (2, 1, 1)
    assert directory.removed == ["idp|bob@example.test"]
    assert [r["idp_subject"] for r in removals(owner)] == ["idp|alice@example.test"]


def test_running_it_twice_for_the_same_account_deletes_and_removes_it_once(system, owner, alice):
    ask(system, alice)
    directory = Directory()
    sweep.run(system, directory, apply=True, now=NOW)
    sweep.run(system, directory, apply=True, now=NOW)
    assert directory.removed == ["idp|alice@example.test"] and events(owner) == [("account_deleted", None, "system")]


def test_every_tenant_table_is_emptied_for_the_deleted_person(db, system, alice, bob):
    fill(db, alice, "A")
    fill(db, bob, "B")
    ask(system, alice)
    sweep.run(system, Directory(), apply=True, now=NOW)
    with db.tenant_session(alice) as conn:
        assert {t: conn.execute(f'SELECT count(*) AS n FROM "{t}"').fetchone()["n"] for t in TENANT_TABLES} == {
            t: 0 for t in TENANT_TABLES
        }  # noqa: S608


# ---- the person's screenshot files ------------------------------------------------------------------------------------------


class FlakyStore(LocalFileStore):
    """A file store that can be made to fail when asked to remove a folder, with a path in its message (which must never
    be kept)."""

    def __init__(self, root, failing: bool | set[str] = False, error: type[Exception] = OSError) -> None:
        super().__init__(root)
        self.failing = failing
        self.error = error
        self.asked: list[str] = []

    def delete_prefix(self, prefix: str) -> int:
        self.asked.append(prefix)
        if self.failing is True or (isinstance(self.failing, set) and prefix in self.failing):
            raise self.error(f"{self.root}/{prefix} is not available")
        return super().delete_prefix(prefix)


class BucketDown(Exception):
    """What a store other than a folder might raise: a kind this code has never heard of."""


@pytest.fixture
def files(tmp_path):
    return FlakyStore(tmp_path / "files")


def keep_image(db, files, user, name) -> str:
    with db.tenant_session(user) as conn:
        trade = make_trades(conn, make_account(conn, name).id, 1)[0]
        return shots.add(conn, files, user, trade, "a.png", PNG_1X1).key


def present(files, key) -> bool:
    try:
        files.get(key)
    except FileMissing:
        return False
    return True


def folders_waiting(owner):
    return [
        (r["prefix"], r["attempts"], r["last_error"])
        for r in owner.execute("SELECT prefix, attempts, last_error FROM file_removals ORDER BY prefix")
    ]


def test_the_files_of_a_deleted_person_go_with_them_and_nobody_elses_do(db, system, owner, files, alice, bob):
    mine, theirs = keep_image(db, files, alice, "A"), keep_image(db, files, bob, "B")
    ask(system, alice)
    report = sweep.run(system, Directory(), apply=True, files=files, now=NOW)
    assert (report.deleted, report.files_removed, report.files_pending) == (1, 1, 0)
    assert not present(files, mine) and present(files, theirs)
    assert not (files.root / "u" / str(alice)).exists()  # the folder itself, not only the file
    assert folders_waiting(owner) == []  # written down in the transaction, forgotten once the store confirmed


def test_a_dry_run_leaves_the_files_and_writes_nothing_down(db, system, owner, files, alice):
    key = keep_image(db, files, alice, "A")
    ask(system, alice)
    report = sweep.run(system, Directory(), apply=False, files=files, now=NOW)
    assert (report.deleted, report.files_removed, report.files_pending) == (0, 0, 0)
    assert present(files, key) and folders_waiting(owner) == [] and files.asked == []


def test_a_store_that_fails_leaves_the_account_deleted_and_the_folder_written_down_with_only_the_kind_of_error(
    db, system, owner, files, alice
):
    key = keep_image(db, files, alice, "A")
    ask(system, alice)
    files.failing = True
    report = sweep.run(system, Directory(), apply=True, files=files, now=NOW)
    assert (report.deleted, report.files_removed, report.files_pending) == (1, 0, 1)
    assert users(owner) == set() and present(files, key)  # the account is gone whatever the store did
    assert folders_waiting(owner) == [(f"u/{alice}/", 1, "OSError")]  # the kind, never the message (it holds a path)

    sweep.run(system, Directory(), apply=True, files=files, now=NOW)  # still down
    assert folders_waiting(owner) == [(f"u/{alice}/", 2, "OSError")]

    files.failing = False  # back up
    done = sweep.run(system, Directory(), apply=True, files=files, now=NOW)
    assert (done.due, done.files_removed, done.files_pending) == (0, 1, 0)
    assert not present(files, key) and folders_waiting(owner) == []


@pytest.mark.parametrize("error", [RuntimeError, BucketDown, PermissionError])
def test_any_kind_of_store_error_is_kept_as_its_kind_only(db, system, owner, files, alice, error):
    keep_image(db, files, alice, "A")
    ask(system, alice)
    files.failing, files.error = True, error
    report = sweep.run(system, Directory(), apply=True, files=files, now=NOW)
    assert (report.deleted, report.files_pending) == (1, 1)
    assert folders_waiting(owner) == [(f"u/{alice}/", 1, error.__name__)]


def test_one_failing_folder_does_not_stop_the_others(db, system, owner, files, alice, bob):
    mine, theirs = keep_image(db, files, alice, "A"), keep_image(db, files, bob, "B")
    ask(system, alice)
    ask(system, bob)
    files.failing = {f"u/{alice}/"}
    report = sweep.run(system, Directory(), apply=True, files=files, now=NOW)
    assert (report.deleted, report.files_removed, report.files_pending) == (2, 1, 1)
    assert present(files, mine) and not present(files, theirs)
    assert [w[0] for w in folders_waiting(owner)] == [f"u/{alice}/"]


def test_with_no_store_on_the_job_the_folders_wait_until_a_run_that_has_one(db, system, owner, files, alice, bob):
    mine, theirs = keep_image(db, files, alice, "A"), keep_image(db, files, bob, "B")
    ask(system, alice)
    ask(system, bob)
    report = sweep.run(system, Directory(), apply=True, now=NOW)  # no `files`
    assert (report.deleted, report.files_removed, report.files_pending) == (2, 0, 2)
    assert present(files, mine) and present(files, theirs)
    later = sweep.run(system, Directory(), apply=True, files=files, now=NOW)
    assert (later.files_removed, later.files_pending) == (2, 0)
    assert not present(files, mine) and not present(files, theirs) and folders_waiting(owner) == []


def test_a_person_who_signs_in_between_the_list_and_the_delete_keeps_their_files_and_leaves_no_entry(
    db, system, owner, files, alice, bob, monkeypatch
):
    mine, theirs = keep_image(db, files, alice, "A"), keep_image(db, files, bob, "B")
    ask(system, alice)
    ask(system, bob)
    real = deletion.due

    def due_then_alice_signs_in(conn, **kwargs):
        listed = real(conn, **kwargs)
        owner.execute("UPDATE users SET status = 'active', deletion_scheduled_at = NULL WHERE id = %s", (alice,))
        return listed

    monkeypatch.setattr(sweep.deletion, "due", due_then_alice_signs_in)
    report = sweep.run(system, Directory(), apply=True, files=files, now=NOW)
    assert (report.deleted, report.skipped, report.files_removed) == (1, 1, 1)
    assert present(files, mine) and not present(files, theirs)  # her rows are all still there, so her files are
    assert files.asked == [f"u/{bob}/"] and folders_waiting(owner) == []


def test_a_folder_that_is_already_gone_is_not_an_error(db, system, owner, files, alice):
    ask(system, alice)  # no screenshots at all: there is no folder to remove
    report = sweep.run(system, Directory(), apply=True, files=files, now=NOW)
    assert (report.deleted, report.files_removed, report.files_pending) == (1, 1, 0) and folders_waiting(owner) == []


# ---- the command line -------------------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, sweep_url):
    for name in (
        "TJ_KINDE_DOMAIN",
        "TJ_KINDE_M2M_CLIENT_ID",
        "TJ_KINDE_M2M_CLIENT_SECRET",
        "TJ_FILES_DIR",
        "TJ_S3_ENDPOINT",
        "TJ_S3_BUCKET",
        "TJ_S3_ACCESS_KEY_ID",
        "TJ_S3_SECRET_ACCESS_KEY",
        "TJ_S3_REGION",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TJ_SWEEP_DATABASE_URL", sweep_url)
    return monkeypatch


def test_the_command_is_a_dry_run_unless_told_to_apply_and_prints_only_counts(env, capsys, system, owner, alice):
    ask(system, alice)
    assert sweep.main(["deletions"]) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing changed; due 1, deleted 0" in out and users(owner) == {alice}
    assert "alice" not in out and "@" not in out and "idp|" not in out


def test_applying_without_the_providers_management_settings_is_refused_by_name_without_values(
    env, capsys, system, owner, alice
):
    ask(system, alice)
    assert sweep.main(["deletions", "--apply"]) == 2
    err = capsys.readouterr().err
    assert "TJ_KINDE_M2M_CLIENT_SECRET" in err and "--without-identity-provider" in err
    assert users(owner) == {alice}  # nothing was deleted


def test_it_can_be_told_on_purpose_to_leave_the_provider_alone_for_local_development(env, capsys, system, owner, alice):
    ask(system, alice)
    assert sweep.main(["deletions", "--apply", "--without-identity-provider"]) == 0
    assert (
        "applied; due 1, deleted 1, skipped 0, identities removed 0, identities still to remove 1"
        in capsys.readouterr().out
    )
    assert users(owner) == set()


def test_the_command_removes_the_files_from_the_folder_in_the_environment_and_prints_only_counts(
    env, capsys, db, system, owner, files, alice
):
    key = keep_image(db, files, alice, "A")
    ask(system, alice)
    env.setenv("TJ_FILES_DIR", str(files.root))
    assert sweep.main(["deletions", "--apply", "--without-identity-provider"]) == 0
    captured = capsys.readouterr()
    assert "file folders removed 1, file folders still to remove 0" in captured.out
    assert str(files.root) not in captured.out + captured.err and str(alice) not in captured.out + captured.err
    assert not present(files, key) and "TJ_FILES_DIR" not in captured.err


def test_the_command_says_why_folders_are_waiting_when_the_job_has_no_folder_set(
    env, capsys, db, system, owner, files, alice
):
    key = keep_image(db, files, alice, "A")
    ask(system, alice)
    assert sweep.main(["deletions", "--apply", "--without-identity-provider"]) == 0
    captured = capsys.readouterr()
    assert "file folders removed 0, file folders still to remove 1" in captured.out
    assert "neither TJ_S3_* nor TJ_FILES_DIR is set on this job" in captured.err
    assert present(files, key) and len(folders_waiting(owner)) == 1


def test_it_needs_the_sweep_connection_and_says_which_setting_is_missing(monkeypatch, capsys):
    monkeypatch.delenv("TJ_SWEEP_DATABASE_URL", raising=False)
    assert sweep.main(["deletions"]) == 2
    assert "TJ_SWEEP_DATABASE_URL is not set" in capsys.readouterr().err
