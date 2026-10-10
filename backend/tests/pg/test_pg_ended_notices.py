"""The "your access has ended" email for people whose time-limited access ran out, on a real Postgres (migration 0016)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from test_pg_plans import code_for, redeem

from tradejournal.api.mailer import Email, MailError
from tradejournal.pg import ended_notices, sweep

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


class Mailbox:
    """A pretend email provider: remembers what was sent, and can be made to refuse."""

    def __init__(self) -> None:
        self.sent: list[Email] = []
        self.refuse: dict[str, str] = {}  # address -> reason code

    def send(self, email: Email) -> None:
        if email.to in self.refuse:
            raise MailError(self.refuse[email.to])
        self.sent.append(email)


def give(owner, system, user, *, ended: timedelta, days: int = 30):
    """A grant that ended `ended` ago (as of NOW; a negative value ends in the future). It is redeemed far in the past, where
    it cannot overlap another, and then given the dates the test wants."""
    n = owner.execute("SELECT count(*) AS n FROM grant_redemptions WHERE user_id = %s", (user,)).fetchone()["n"]
    _, code = code_for(owner, days=days)
    redeem(system, user, code, now=NOW - (1000 + 40 * n) * DAY)
    ends = NOW - ended
    row = owner.execute(
        "UPDATE grant_redemptions SET starts_at = %s, ends_at = %s"
        " WHERE id = (SELECT max(id) FROM grant_redemptions WHERE user_id = %s) RETURNING id",
        (ends - days * DAY, ends, user),
    ).fetchone()
    return row["id"]


def due(system, **kw):
    with system.system_session("sweep") as conn:
        return ended_notices.due(conn, now=NOW, **kw)


def noticed(owner, redemption):
    return owner.execute("SELECT ended_notice_sent_at FROM grant_redemptions WHERE id = %s", (redemption,)).fetchone()[
        "ended_notice_sent_at"
    ]


def sign_up_as_beta(owner, email, first_name="Alice"):
    owner.execute(
        "INSERT INTO beta_signups (email, first_name, prop_firm) VALUES (%s, %s, 'Topstep')", (email, first_name)
    )


def events(owner):
    return [(r["action"], r["user_id"], r["actor"]) for r in owner.execute("SELECT * FROM audit_log ORDER BY id")]


# ---- who is due -----------------------------------------------------------------------------------------------------


def test_someone_whose_thirty_days_ran_out_is_due_and_is_called_by_the_name_they_gave(system, owner, alice):
    sign_up_as_beta(owner, "alice@example.test", "Alice")
    redemption = give(owner, system, alice, ended=2 * DAY)
    assert due(system) == [ended_notices.Notice(redemption, alice, "alice@example.test", "Alice")]


def test_someone_without_a_beta_sign_up_is_called_there(system, owner, alice):
    give(owner, system, alice, ended=DAY)
    assert [n.first_name for n in due(system)] == ["there"]


def test_the_name_is_found_whatever_the_case_of_the_address(system, owner, alice):
    sign_up_as_beta(owner, "alice@example.test", "Alice")
    owner.execute("UPDATE users SET email = 'Alice@Example.Test' WHERE id = %s", (alice,))
    give(owner, system, alice, ended=DAY)
    assert [n.first_name for n in due(system)] == ["Alice"]


def test_nothing_is_due_while_the_access_still_runs(system, owner, alice):
    give(owner, system, alice, ended=-5 * DAY)  # ends in five days
    assert due(system) == []


def test_it_is_due_from_the_moment_the_days_are_up_and_not_a_moment_before(system, owner, alice):
    give(owner, system, alice, ended=timedelta(0))  # ends exactly now
    assert len(due(system)) == 1
    owner.execute("UPDATE grant_redemptions SET ends_at = %s", (NOW + timedelta(seconds=1),))
    assert due(system) == []


def test_an_ending_older_than_the_window_is_left_alone(system, owner, alice, bob):
    give(owner, system, alice, ended=14 * DAY)  # on the edge: still in
    give(owner, system, bob, ended=14 * DAY + timedelta(seconds=1))  # just outside
    assert [n.user_id for n in due(system)] == [alice]


def test_the_window_can_be_chosen(system, owner, alice):
    give(owner, system, alice, ended=30 * DAY)
    assert due(system) == []
    assert len(due(system, since=NOW - 40 * DAY)) == 1


def test_someone_already_told_is_not_due_again(system, owner, alice):
    redemption = give(owner, system, alice, ended=DAY)
    owner.execute("UPDATE grant_redemptions SET ended_notice_sent_at = %s WHERE id = %s", (NOW, redemption))
    assert due(system) == []


@pytest.mark.parametrize("status", ["suspended", "pending_deletion"])
def test_an_account_that_is_not_active_is_not_written_to(system, owner, alice, status):
    give(owner, system, alice, ended=DAY)
    if status == "pending_deletion":
        owner.execute("UPDATE users SET status = %s, deletion_scheduled_at = %s WHERE id = %s", (status, NOW, alice))
    else:
        owner.execute("UPDATE users SET status = %s WHERE id = %s", (status, alice))
    assert due(system) == []


def test_someone_who_has_another_grant_running_has_not_lost_their_access(system, owner, alice):
    give(owner, system, alice, ended=DAY)
    give(owner, system, alice, ended=-10 * DAY)  # a second code, running
    assert due(system) == []


def test_someone_with_a_plan_override_in_force_has_not_lost_their_access(system, owner, alice):
    give(owner, system, alice, ended=DAY)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, expires_at, reason, created_by) VALUES (%s, 'pro', NULL, 'friend', 'x')",
        (alice,),
    )
    assert due(system) == []


def test_an_override_that_was_revoked_or_has_run_out_does_not_hide_the_notice(system, owner, alice, bob):
    give(owner, system, alice, ended=DAY)
    give(owner, system, bob, ended=DAY)
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, expires_at, reason, created_by, revoked_at)"
        " VALUES (%s, 'pro', NULL, 'friend', 'x', %s)",
        (alice, NOW - DAY),
    )
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, expires_at, reason, created_by) VALUES (%s, 'pro', %s, 'friend', 'x')",
        (bob, NOW - DAY),
    )
    assert {n.user_id for n in due(system)} == {alice, bob}


def test_a_grant_an_operator_revoked_is_due_when_its_email_did_not_go(system, owner, alice):
    redemption = give(owner, system, alice, ended=-20 * DAY)  # would have run another 20 days
    owner.execute("UPDATE grant_redemptions SET revoked_at = %s WHERE id = %s", (NOW - DAY, redemption))
    assert [n.redemption_id for n in due(system)] == [redemption]


def test_a_revocation_dated_in_the_future_is_not_an_ending_yet(system, owner, alice):
    redemption = give(owner, system, alice, ended=-20 * DAY)
    owner.execute("UPDATE grant_redemptions SET revoked_at = %s WHERE id = %s", (NOW + DAY, redemption))
    assert due(system) == []


def test_a_person_with_two_ended_grants_is_one_notice_for_the_latest(system, owner, alice):
    give(owner, system, alice, ended=10 * DAY)
    latest = give(owner, system, alice, ended=3 * DAY)
    assert [n.redemption_id for n in due(system)] == [latest]


def test_the_oldest_ending_comes_first_and_the_limit_is_kept(system, owner, alice, bob):
    give(owner, system, alice, ended=2 * DAY)
    give(owner, system, bob, ended=5 * DAY)
    assert [n.user_id for n in due(system)] == [bob, alice]
    assert [n.user_id for n in due(system, limit=1)] == [bob]


# ---- recording that it was sent --------------------------------------------------------------------------------------


def test_recording_covers_the_persons_earlier_ended_grants_so_they_are_told_once(system, owner, alice):
    earlier = give(owner, system, alice, ended=10 * DAY)
    latest = give(owner, system, alice, ended=3 * DAY)
    with system.system_session("sweep") as conn:
        assert ended_notices.mark_sent(conn, latest, now=NOW) is True
    assert noticed(owner, earlier) == NOW and noticed(owner, latest) == NOW
    assert due(system) == []


def test_recording_twice_says_so_and_changes_nothing(system, owner, alice):
    redemption = give(owner, system, alice, ended=DAY)
    with system.system_session("sweep") as conn:
        assert ended_notices.mark_sent(conn, redemption, now=NOW) is True
    with system.system_session("sweep") as conn:
        assert ended_notices.mark_sent(conn, redemption, now=NOW + DAY) is False
    assert noticed(owner, redemption) == NOW


def test_recording_does_not_touch_a_grant_that_is_still_running_or_another_person(system, owner, alice, bob):
    ended = give(owner, system, alice, ended=DAY)
    running = give(owner, system, alice, ended=-10 * DAY)
    other = give(owner, system, bob, ended=DAY)
    with system.system_session("sweep") as conn:
        ended_notices.mark_sent(conn, ended, now=NOW)
    assert noticed(owner, running) is None and noticed(owner, other) is None


# ---- the job ---------------------------------------------------------------------------------------------------------


def test_a_dry_run_counts_who_is_due_and_sends_and_records_nothing(system, owner, alice):
    redemption = give(owner, system, alice, ended=DAY)
    mailbox = Mailbox()
    report = sweep.run_notices(system, mailbox, apply=False, now=NOW)
    assert (report.due, report.sent, report.failed, report.dry_run) == (1, 0, 0, True)
    assert mailbox.sent == [] and noticed(owner, redemption) is None and events(owner) == []


def test_applying_sends_the_email_records_it_and_writes_one_audit_row(system, owner, alice):
    sign_up_as_beta(owner, "alice@example.test", "Alice")
    redemption = give(owner, system, alice, ended=DAY)
    mailbox = Mailbox()
    report = sweep.run_notices(system, mailbox, apply=True, now=NOW)
    assert (report.due, report.sent, report.failed, report.dry_run) == (1, 1, 0, False)
    [email] = mailbox.sent
    assert email.to == "alice@example.test" and email.subject == "Your Roundturn beta access has ended"
    assert email.text.startswith("Hi Alice,")
    assert noticed(owner, redemption) == NOW
    assert events(owner) == [("access_ended_notice_sent", alice, "system")]


def test_a_second_run_sends_nothing_more(system, owner, alice):
    give(owner, system, alice, ended=DAY)
    mailbox = Mailbox()
    sweep.run_notices(system, mailbox, apply=True, now=NOW)
    again = sweep.run_notices(system, mailbox, apply=True, now=NOW + DAY)
    assert (again.due, again.sent) == (0, 0)
    assert len(mailbox.sent) == 1


def test_a_refused_email_leaves_the_person_due_and_is_counted_by_its_reason(system, owner, alice, bob):
    sign_up_as_beta(owner, "alice@example.test", "Alice")
    give(owner, system, alice, ended=2 * DAY)
    redemption = give(owner, system, bob, ended=DAY)
    mailbox = Mailbox()
    mailbox.refuse["alice@example.test"] = "mail_unavailable_503"
    report = sweep.run_notices(system, mailbox, apply=True, now=NOW)
    assert (report.due, report.sent, report.failed) == (2, 1, 1)  # one failing does not stop the next
    assert report.reasons == {"mail_unavailable_503": 1}
    assert [e.to for e in mailbox.sent] == ["bob@example.test"] and noticed(owner, redemption) == NOW
    assert [n.user_id for n in due(system)] == [alice]
    del mailbox.refuse["alice@example.test"]
    retry = sweep.run_notices(system, mailbox, apply=True, now=NOW)
    assert (retry.due, retry.sent, retry.failed) == (1, 1, 0)


def test_applying_without_a_mailer_is_an_error_not_a_silent_skip(system, owner, alice):
    give(owner, system, alice, ended=DAY)
    with pytest.raises(ValueError):
        sweep.run_notices(system, None, apply=True, now=NOW)
    assert (
        owner.execute("SELECT count(*) AS n FROM grant_redemptions WHERE ended_notice_sent_at IS NOT NULL").fetchone()[
            "n"
        ]
        == 0
    )


def test_two_runs_that_overlap_record_it_once_and_write_one_audit_row(system, owner, alice):
    redemption = give(owner, system, alice, ended=DAY)

    class Racing(Mailbox):
        """While this run is sending, another run gets there first and records it."""

        def send(self, email: Email) -> None:
            super().send(email)
            with system.system_session("sweep") as conn:
                ended_notices.mark_sent(conn, redemption, now=NOW)

    mailbox = Racing()
    report = sweep.run_notices(system, mailbox, apply=True, now=NOW + timedelta(minutes=1))
    assert report.sent == 1 and noticed(owner, redemption) == NOW  # the first recording stands
    assert events(owner) == []  # this run found it already recorded and wrote nothing of its own


# ---- who may call the functions ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["login", "webhook", "admin"])
def test_only_the_sweep_role_can_ask_who_is_due_or_record_it(system, owner, alice, profile):
    give(owner, system, alice, ended=DAY)
    with system.system_session(profile) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM ended_notices_due(%s, %s, 10)", (NOW, NOW - 14 * DAY))
    with system.system_session(profile) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT mark_ended_notice_sent(1, %s)", (NOW,))


def test_the_sweep_role_still_cannot_read_addresses_or_grants_itself(system, owner, alice):
    give(owner, system, alice, ended=DAY)
    with system.system_session("sweep") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT email FROM users")
    with system.system_session("sweep") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM grant_redemptions")
    with system.system_session("sweep") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM beta_signups")


# ---- the command -----------------------------------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, sweep_url):
    for name in ("TJ_EMAIL_API_KEY", "TJ_EMAIL_FROM", "TJ_EMAIL_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TJ_SWEEP_DATABASE_URL", sweep_url)
    return monkeypatch


def test_the_command_is_a_dry_run_unless_told_and_prints_only_counts(env, capsys, system, owner, alice):
    sign_up_as_beta(owner, "alice@example.test", "Alice")
    give(owner, system, alice, ended=DAY)
    owner.execute(
        "UPDATE grant_redemptions SET ends_at = now() - interval '1 day', starts_at = now() - interval '31 days'"
    )
    assert sweep.main(["ended-notices"]) == 0
    out = capsys.readouterr()
    assert "dry run: nothing changed; due 1, sent 0, failed 0" in out.out
    assert "alice" not in out.out + out.err and "@" not in out.out + out.err


def test_applying_without_the_email_settings_is_refused_by_name_and_sends_nothing(env, capsys, system, owner, alice):
    give(owner, system, alice, ended=DAY)
    assert sweep.main(["ended-notices", "--apply"]) == 2
    err = capsys.readouterr().err
    assert "TJ_EMAIL_API_KEY" in err and "TJ_EMAIL_FROM" in err
    assert (
        owner.execute("SELECT count(*) AS n FROM grant_redemptions WHERE ended_notice_sent_at IS NOT NULL").fetchone()[
            "n"
        ]
        == 0
    )


def test_the_command_needs_the_sweep_connection(monkeypatch, capsys):
    monkeypatch.delenv("TJ_SWEEP_DATABASE_URL", raising=False)
    assert sweep.main(["ended-notices"]) == 2
    assert "TJ_SWEEP_DATABASE_URL is not set" in capsys.readouterr().err


def test_all_runs_both_jobs_and_a_missing_setting_for_one_does_not_stop_the_other(
    env, capsys, system, owner, alice, bob
):
    # Alice asked to delete her account eight days ago; Bob's access ended yesterday. This job has no email settings.
    from tradejournal.pg import deletion

    with system.system_session("login") as conn:
        deletion.request(conn, alice, now=NOW - 8 * DAY)
    give(owner, system, bob, ended=DAY)
    owner.execute(
        "UPDATE grant_redemptions SET ends_at = now() - interval '1 day', starts_at = now() - interval '31 days'"
    )
    assert sweep.main(["all", "--apply", "--without-identity-provider"]) == 2  # the notices could not be sent
    captured = capsys.readouterr()
    assert "applied; due 1, deleted 1" in captured.out  # the deletions ran
    assert "TJ_EMAIL_API_KEY" in captured.err  # and the notices said what they lacked
    assert owner.execute("SELECT count(*) AS n FROM users WHERE id = %s", (alice,)).fetchone()["n"] == 0


def test_all_without_applying_is_a_dry_run_of_both(env, capsys, system, owner, alice):
    give(owner, system, alice, ended=DAY)
    owner.execute(
        "UPDATE grant_redemptions SET ends_at = now() - interval '1 day', starts_at = now() - interval '31 days'"
    )
    assert sweep.main(["all"]) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing changed; due 0, deleted 0" in out
    assert "dry run: nothing changed; due 1, sent 0, failed 0" in out
