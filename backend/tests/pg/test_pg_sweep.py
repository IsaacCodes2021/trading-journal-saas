"""The deletion sweep, end to end on a real Postgres with a pretend identity provider (requirement F-A6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pg_helpers import make_account, make_trades
from test_pg_tenancy import TENANT_TABLES

from tradejournal.auth.idp import IdentityError
from tradejournal.pg import deletion, sweep

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


# ---- the command line -------------------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, sweep_url):
    for name in ("TJ_KINDE_DOMAIN", "TJ_KINDE_M2M_CLIENT_ID", "TJ_KINDE_M2M_CLIENT_SECRET"):
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


def test_it_needs_the_sweep_connection_and_says_which_setting_is_missing(monkeypatch, capsys):
    monkeypatch.delenv("TJ_SWEEP_DATABASE_URL", raising=False)
    assert sweep.main(["deletions"]) == 2
    assert "TJ_SWEEP_DATABASE_URL is not set" in capsys.readouterr().err
