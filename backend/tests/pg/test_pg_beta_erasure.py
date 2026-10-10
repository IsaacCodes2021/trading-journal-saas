"""What the beta keeps about a person (migration 0017): their sign-up and codes go with their account, and sign-ups that never
became an account are removed on a date the owner chooses, never by default."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from test_pg_beta import count, join, row

from tradejournal.pg import beta, deletion, invites, sweep

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def account(owner, email):
    return owner.execute(
        "INSERT INTO users (idp_subject, email) VALUES (%s, %s) RETURNING id", (f"idp|{email}", email)
    ).fetchone()["id"]


def delete_account(system, user):
    with system.system_session("login") as conn:
        assert deletion.request(conn, user, now=NOW - 8 * DAY)
    with system.system_session("sweep") as conn:
        assert deletion.delete(conn, user, now=NOW)


def aged(owner, signup, *, days):
    """Make the sign-up's last code `days` old (as of NOW)."""
    owner.execute(
        "UPDATE beta_signups SET last_code_at = %s, created_at = %s WHERE id = %s",
        (NOW - days * DAY, NOW - days * DAY, signup),
    )


def emails_of(owner, table):
    return sorted(r["email"] for r in owner.execute(f"SELECT email FROM {table}").fetchall())  # noqa: S608


# ---- an account being deleted ---------------------------------------------------------------------------------------------


def test_deleting_an_account_removes_their_signup_and_every_code_tied_to_their_address(system, owner):
    cara_signup, _ = join(system, email="cara@example.test")
    dan_signup, _ = join(system, email="dan@example.test")
    cara = account(owner, "Cara@Example.test")  # the account may be spelled in capitals
    account(owner, "dan@example.test")
    # an operator's own invite for the same address, written in capitals, and a code that names nobody
    owner.execute(
        "INSERT INTO invites (code_hash, label, email) VALUES (%s, 'for Cara', 'Cara@Example.test')",
        (invites.hash_code(invites.generate_code()),),
    )
    shared = owner.execute(
        "INSERT INTO invites (code_hash, label, max_uses) VALUES (%s, 'for anyone', 5) RETURNING id",
        (invites.hash_code(invites.generate_code()),),
    ).fetchone()["id"]
    owner.execute("INSERT INTO invite_redemptions (invite_id, user_id) VALUES (%s, %s)", (shared, cara))

    delete_account(system, cara)

    assert row(owner, "beta_signups", id=cara_signup) is None
    assert emails_of(owner, "beta_signups") == ["dan@example.test"]
    assert row(owner, "beta_signups", id=dan_signup)["invite_id"] is not None
    assert [r["email"] for r in owner.execute("SELECT email FROM invites WHERE email IS NOT NULL")] == [
        "dan@example.test"
    ]
    assert row(owner, "invites", id=shared) is not None  # a code that names nobody is not theirs to take


def test_it_holds_for_any_way_a_user_row_goes_not_only_the_sweep(system, owner):
    signup, _ = join(system, email="cara@example.test")
    cara = account(owner, "cara@example.test")
    owner.execute("DELETE FROM users WHERE id = %s", (cara,))
    assert row(owner, "beta_signups", id=signup) is None and count(owner, "invites") == 0


def test_someone_who_never_signed_up_loses_nothing_of_anyone_elses(system, owner):
    join(system, email="dan@example.test")
    eve = account(owner, "eve@example.test")
    owner.execute("DELETE FROM users WHERE id = %s", (eve,))
    assert emails_of(owner, "beta_signups") == ["dan@example.test"] and count(owner, "invites") == 1


def test_the_audit_trail_of_the_sign_up_stays_and_names_no_address(system, owner):
    signup, _ = join(system, email="cara@example.test")
    cara = account(owner, "cara@example.test")
    delete_account(system, cara)
    events = owner.execute("SELECT action, entity_id, meta FROM audit_log WHERE action = 'beta_code_made'").fetchall()
    assert [(e["entity_id"], e["meta"]) for e in events] == [(str(signup), {"codes_made": 1})]
    assert "cara" not in str(owner.execute("SELECT * FROM audit_log").fetchall())


# ---- sign-ups that never became an account -----------------------------------------------------------------------------


def make_signups(system, owner):
    """Old (100 days), middling (45), recent (10), and old with an account; and one that has no date of last code at all."""
    ids = {}
    for name, days in (("old", 100), ("middling", 45), ("recent", 10), ("member", 100)):
        signup, _ = join(system, email=f"{name}@example.test")
        aged(owner, signup, days=days)
        ids[name] = signup
    account(owner, "member@example.test")
    undated, _ = join(system, email="undated@example.test")
    owner.execute(
        "UPDATE beta_signups SET last_code_at = NULL, created_at = %s WHERE id = %s", (NOW - 200 * DAY, undated)
    )
    ids["undated"] = undated
    return ids


def erase(system, *, days, apply, limit=100):
    with system.system_session("sweep") as conn:
        return beta.erase_unused(conn, before=NOW - days * DAY, limit=limit, apply=apply)


def test_a_dry_run_counts_who_would_go_and_removes_nothing(system, owner):
    make_signups(system, owner)
    assert erase(system, days=30, apply=False) == 3  # old, middling and undated
    assert count(owner, "beta_signups") == 5 and count(owner, "invites") == 5


def test_applying_removes_old_unused_signups_and_their_codes_and_nothing_else(system, owner):
    make_signups(system, owner)
    assert erase(system, days=30, apply=True) == 3
    assert emails_of(owner, "beta_signups") == ["member@example.test", "recent@example.test"]
    assert sorted(r["email"] for r in owner.execute("SELECT email FROM invites")) == [
        "member@example.test",
        "recent@example.test",
    ]


def test_the_age_is_the_one_asked_for_and_the_edge_is_kept(system, owner):
    ids = make_signups(system, owner)
    assert erase(system, days=60, apply=True) == 2  # old and undated; middling (45 days) is younger than 60
    assert row(owner, "beta_signups", id=ids["middling"]) is not None
    aged(owner, ids["middling"], days=60)  # exactly on the line: not past it
    assert erase(system, days=60, apply=True) == 0
    owner.execute(
        "UPDATE beta_signups SET last_code_at = last_code_at - interval '1 second' WHERE id = %s", (ids["middling"],)
    )
    assert erase(system, days=60, apply=True) == 1


def test_someone_with_an_account_is_never_removed_here_however_old(system, owner):
    ids = make_signups(system, owner)
    erase(system, days=1, apply=True)
    assert row(owner, "beta_signups", id=ids["member"]) is not None


def test_earlier_codes_for_the_same_address_go_with_it(system, owner):
    signup, _ = join(system, email="old@example.test")
    # a second request after the first email failed: the earlier code is revoked and kept as a row
    with system.system_session("login") as conn:
        beta.email_result(conn, signup, ok=False)
    again, _ = join(system, email="old@example.test")
    assert again == signup and count(owner, "invites") == 2
    aged(owner, signup, days=100)
    assert erase(system, days=30, apply=True) == 1
    assert count(owner, "invites") == 0 and count(owner, "beta_signups") == 0


def test_one_run_takes_at_most_the_limit_and_the_next_takes_the_rest(system, owner):
    for n in range(3):
        signup, _ = join(system, email=f"old{n}@example.test")
        aged(owner, signup, days=100)
    assert erase(system, days=30, apply=True, limit=2) == 2
    assert erase(system, days=30, apply=False) == 1
    assert erase(system, days=30, apply=True, limit=2) == 1
    assert count(owner, "beta_signups") == 0


def test_a_missing_date_is_an_error_not_everyone(system, owner):
    join(system)
    with pytest.raises(psycopg.errors.RaiseException), system.system_session("sweep") as conn:
        conn.execute("SELECT erase_unused_beta_signups(NULL, 10, true)")
    assert count(owner, "beta_signups") == 1


# ---- who may call it ---------------------------------------------------------------------------------------------------


def test_only_the_sweep_role_may_call_the_eraser_and_nobody_may_call_the_trigger(owner):
    sql = "SELECT has_function_privilege(%s, %s, 'EXECUTE') AS ok"
    eraser = "erase_unused_beta_signups(timestamptz, integer, boolean)"
    assert owner.execute(sql, ("app_sweep", eraser)).fetchone()["ok"]
    for role in ("app_rw", "app_login", "app_webhook", "app_admin"):
        assert not owner.execute(sql, (role, eraser)).fetchone()["ok"], role
    for role in ("app_rw", "app_login", "app_webhook", "app_admin", "app_sweep"):
        assert not owner.execute(sql, (role, "erase_beta_traces()")).fetchone()["ok"], role


# ---- the command -------------------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, sweep_url):
    monkeypatch.setenv("TJ_SWEEP_DATABASE_URL", sweep_url)
    return monkeypatch


def test_the_command_will_not_run_without_an_age_or_with_a_short_one(env, capsys, system, owner):
    signup, _ = join(system)
    aged(owner, signup, days=500)
    assert sweep.main(["beta-signups", "--apply"]) == 2
    assert "--keep-days" in capsys.readouterr().err
    assert sweep.main(["beta-signups", "--keep-days", "29", "--apply"]) == 2
    assert "at least 30" in capsys.readouterr().err
    assert count(owner, "beta_signups") == 1
    with pytest.raises(ValueError):
        sweep.run_beta_signups(system, keep=timedelta(days=29), apply=True)


def test_the_command_is_a_dry_run_unless_told_prints_counts_and_writes_one_audit_row(env, capsys, system, owner):
    join(system, email="old@example.test")
    owner.execute(
        "UPDATE beta_signups SET last_code_at = now() - interval '100 days', created_at = now() - interval '100 days'"
    )
    assert sweep.main(["beta-signups", "--keep-days", "90"]) == 0
    out = capsys.readouterr()
    assert "dry run: nothing changed; unused sign-ups past 90 days: 1, removed 0" in out.out
    assert "old@example" not in out.out + out.err and count(owner, "beta_signups") == 1
    assert sweep.main(["beta-signups", "--keep-days", "90", "--apply"]) == 0
    assert "applied; unused sign-ups past 90 days: 1, removed 1" in capsys.readouterr().out
    assert count(owner, "beta_signups") == 0 and count(owner, "invites") == 0
    events = owner.execute("SELECT actor, meta FROM audit_log WHERE action = 'beta_signups_erased'").fetchall()
    assert [(e["actor"], e["meta"]) for e in events] == [("system", {"count": 1})]
    assert "old@example" not in str(owner.execute("SELECT * FROM audit_log").fetchall())
    assert sweep.main(["beta-signups", "--keep-days", "90", "--apply"]) == 0  # nothing left: no second audit row
    assert (
        owner.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'beta_signups_erased'").fetchone()["n"] == 1
    )


def test_all_does_not_include_it(env, capsys, system, owner):
    signup, _ = join(system)
    aged(owner, signup, days=500)
    assert sweep.main(["all"]) == 0
    assert count(owner, "beta_signups") == 1
    assert "unused sign-ups" not in capsys.readouterr().out
