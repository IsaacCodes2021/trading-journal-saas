"""The background sweep that deletes accounts whose time has come (requirement F-A6; `pg/deletion.py` and migration 0008).

    python -m tradejournal.pg.sweep deletions              # a dry run: how many are due, nothing is touched
    python -m tradejournal.pg.sweep deletions --apply      # delete them
    python -m tradejournal.pg.sweep ended-notices          # a dry run: who is due the "your access has ended" email
    python -m tradejournal.pg.sweep ended-notices --apply  # send it, and record that it was sent
    python -m tradejournal.pg.sweep all --apply            # all three, in that order (what the scheduled job runs)
    python -m tradejournal.pg.sweep beta-signups                         # a dry run: sign-ups with no account past 24 hours
    python -m tradejournal.pg.sweep beta-signups --apply                 # remove them and their codes

Run it as a scheduled job on the host (hourly is plenty). It connects as the `sweep` role (`TJ_SWEEP_DATABASE_URL`), which
can find accounts that are due and delete them, and nothing else about a person.

For each due account, in this order and for good reasons:
1. In ONE transaction: write the audit row, delete the account (the statement itself re-checks that it is still pending and
   still due, so a person who signed in a moment ago, and so cancelled, is not deleted), and write the identity's id into
   `identity_removals`. All of it happens or none of it does.
2. Then, separately and repeatably: remove each recorded identity at the provider, and forget it once the provider confirms.
   A failure leaves the entry for the next run; it never leaves an identity nobody remembers.
3. And the same for the person's screenshot files (`file_removals`, migration 0013): the folder `u/<user id>/` is written
   down in the transaction of step 1 and removed from the file store afterwards (`TJ_FILES_DIR`). With no store configured
   on the job the entries wait, and the counts say so, until a run that has one.

It prints counts and never an email, a name, a provider id or a file name. `--apply` is required to change anything; without a provider
configured it refuses (an account deleted here and left at the provider would still be someone's personal data), unless
`--without-identity-provider` says so on purpose (local development).

`ended-notices` is the other job, run daily: people whose time-limited access (a beta code, a plan code) ran out, or was
ended by an operator and whose email did not go, are sent the "your access has ended" email once (`pg/ended_notices.py`,
migration 0016). A send that fails leaves the person due, so the next run tries again. It needs `TJ_EMAIL_API_KEY` and
`TJ_EMAIL_FROM` (the web service's settings) to apply, and prints counts and the short reason codes of failures only.

`billing` (with a payment provider configured: `TJ_BILLING_PROVIDER` and its settings) ends at the provider the subscriptions
of deleted accounts (queued in the deletion's own transaction, retried until the provider confirms) and runs the nightly
check: every subscription not ended is compared with the provider's own answer, and a difference (a missed webhook) is
corrected and counted. `billing-notices` (with `TJ_BILLING_ENABLED` on) sends the "trial ends in 2 days" and "subscription
ended" emails once each. With billing on, `ended-notices` words its email for a trial, and offers Subscribe.

`beta-signups` removes the sign-ups (an address, a first name, a favourite firm) and the codes of people who asked to join
and never made an account, once their last code is older than 24 hours (migration 0017; the owner's decision of
2026-10-10, and the lifetime of a code since migration 0018, so a code never outlives its sign-up). `--keep-hours` can
lengthen that and cannot shorten it below the lifetime of a code. It is the third step of `all`. A sign-up that has an
account is never touched here: it goes with the account.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from ..api.emails import access_ended_email, subscription_ended_email, trial_ended_email, trial_reminder_email
from ..api.mailer import DEFAULT_API_URL, Mailer, MailError, ResendMailer
from ..auth.idp import IdentityDirectory, IdentityError
from ..billing.provider import BillingProvider
from ..billing.types import BillingError
from ..bucketstore import BucketFileStore, BucketSettings
from ..filestore import FileStore, LocalFileStore
from . import audit, beta, deletion, ended_notices
from . import billing as pg_billing
from .system import SystemDatabase

BATCH = 50
BETA_KEEP_HOURS = 24  # how long a sign-up with no account is kept, and how long its code works (migration 0018)
MIN_KEEP_HOURS = BETA_KEEP_HOURS  # a shorter time would remove someone whose code still works
ACTOR_SYSTEM = audit.ACTOR_SYSTEM


@dataclass
class SweepReport:
    due: int = 0
    deleted: int = 0
    skipped: int = 0  # due when listed, no longer by the time it was acted on (the person signed in)
    identities_removed: int = 0
    identities_pending: int = 0  # still to remove at the provider (the next run tries again)
    files_removed: int = 0  # people's screenshot folders removed from the file store
    files_pending: int = 0  # still to remove (no store on this job, or the store failed: the next run tries again)
    dry_run: bool = True


def run(
    system: SystemDatabase,
    directory: IdentityDirectory | None,
    *,
    apply: bool,
    files: FileStore | None = None,
    now: datetime | None = None,
    limit: int = BATCH,
) -> SweepReport:
    moment = now or datetime.now(UTC)
    report = SweepReport(dry_run=not apply)
    with system.system_session("sweep") as conn:
        due = deletion.due(conn, now=moment, limit=limit)
        report.due = len(due)
    if not apply:
        return report
    for account in due:
        with system.system_session("sweep") as conn, conn.transaction():  # all of it, or none of it
            audit.record(conn, "account_deleted", user_id=account.user_id, actor=ACTOR_SYSTEM)
            # Before the row goes (the subscription goes with it): remember to end it at the provider. The entry holds no
            # person's id, so it outlives the account, and the `billing` step sends it until the provider confirms.
            if pg_billing.queue_cancellation(conn, account.user_id):
                audit.record(conn, "billing_cancel_queued", user_id=account.user_id, actor=ACTOR_SYSTEM)
            if deletion.delete(conn, account.user_id, now=moment):
                conn.execute(
                    "INSERT INTO identity_removals (idp_subject) VALUES (%s) ON CONFLICT DO NOTHING",
                    (account.idp_subject,),
                )
                conn.execute(
                    "INSERT INTO file_removals (prefix) VALUES (%s) ON CONFLICT DO NOTHING", (f"u/{account.user_id}/",)
                )
                report.deleted += 1
            else:
                raise psycopg.Rollback  # no longer due (the person signed in): the audit row for it goes too
    report.skipped = report.due - report.deleted
    _remove_identities(system, directory, report)
    _remove_files(system, files, report)
    return report


@dataclass
class NoticeReport:
    due: int = 0
    sent: int = 0
    failed: int = 0  # still due: the next run tries again
    reasons: dict[str, int] = field(default_factory=dict)  # the provider's short reason codes of the failures, counted
    dry_run: bool = True


def run_notices(
    system: SystemDatabase,
    mailer: Mailer | None,
    *,
    apply: bool,
    now: datetime | None = None,
    limit: int = ended_notices.BATCH,
    since: datetime | None = None,
    billing: bool = False,
) -> NoticeReport:
    """Send the "your access has ended" email to everyone who is due it, and record each one that left."""
    moment = now or datetime.now(UTC)
    report = NoticeReport(dry_run=not apply)
    with system.system_session("sweep") as conn:
        waiting = ended_notices.due(conn, now=moment, since=since, limit=limit)
    report.due = len(waiting)
    if not apply:
        return report
    if mailer is None:
        raise ValueError("a mailer is needed to send")
    for notice in waiting:
        if billing and notice.kind == "trial":
            message = trial_ended_email(to=notice.email, first_name=notice.first_name)
        else:
            message = access_ended_email(to=notice.email, first_name=notice.first_name, billing=billing)
        try:
            mailer.send(message)
        except MailError as problem:
            report.failed += 1
            report.reasons[problem.reason] = report.reasons.get(problem.reason, 0) + 1
            continue
        with system.system_session("sweep") as conn, conn.transaction():
            if ended_notices.mark_sent(conn, notice.redemption_id, now=moment):
                audit.record(conn, "access_ended_notice_sent", user_id=notice.user_id, actor=ACTOR_SYSTEM)
        report.sent += 1
    return report


@dataclass
class BillingReport:
    cancels_sent: int = 0  # deleted accounts' subscriptions ended at the provider
    cancels_pending: int = 0  # still to send (the provider failed or is not configured): the next run tries again
    checked: int = 0  # subscriptions compared with the provider
    corrected: int = 0  # ... that were wrong here (a missed webhook)
    check_failed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    dry_run: bool = True


def run_billing(
    system: SystemDatabase,
    provider: BillingProvider | None,
    *,
    apply: bool,
    now: datetime | None = None,
    limit: int = 500,
) -> BillingReport:
    """End the queued subscriptions of deleted accounts, then the nightly check. Without a provider everything waits."""
    moment = now or datetime.now(UTC)
    report = BillingReport(dry_run=not apply)
    if provider is None:
        with system.system_session("sweep") as conn:
            report.cancels_pending = conn.execute("SELECT count(*) AS n FROM billing_cancellations").fetchone()["n"]
        return report
    with system.system_session("sweep") as conn:
        queued = pg_billing.cancellations(conn, provider.name, limit=limit)
        to_check = pg_billing.to_check(conn, provider.name, limit=limit)
    if not apply:
        report.cancels_pending, report.checked = len(queued), len(to_check)
        return report
    for subscription_id in queued:
        try:
            provider.cancel_now(subscription_id)
            reason = None
        except BillingError as problem:
            reason = problem.reason
        with system.system_session("sweep") as conn:
            if reason is None:
                pg_billing.cancellation_done(conn, provider.name, subscription_id)
                audit.record(conn, "billing_cancelled_after_deletion", actor=ACTOR_SYSTEM)
                report.cancels_sent += 1
            else:
                pg_billing.cancellation_failed(conn, provider.name, subscription_id, reason)
                report.cancels_pending += 1
                report.reasons[reason] = report.reasons.get(reason, 0) + 1
    for subscription_id in to_check:
        try:
            answer = provider.fetch_subscription(subscription_id)
        except BillingError as problem:
            report.check_failed += 1
            report.reasons[problem.reason] = report.reasons.get(problem.reason, 0) + 1
            continue
        report.checked += 1
        with system.system_session("sweep") as conn:
            if pg_billing.reconcile(conn, provider.name, answer, now=moment):
                report.corrected += 1
    return report


@dataclass
class BillingNoticeReport:
    reminders_due: int = 0
    reminders_sent: int = 0
    ended_due: int = 0
    ended_sent: int = 0
    failed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    dry_run: bool = True


def run_billing_notices(
    system: SystemDatabase, mailer: Mailer | None, *, apply: bool, now: datetime | None = None, limit: int = 100
) -> BillingNoticeReport:
    """ "Your trial ends in 2 days" and "your subscription has ended", once each. A send that fails stays due."""
    moment = now or datetime.now(UTC)
    report = BillingNoticeReport(dry_run=not apply)
    with system.system_session("sweep") as conn:
        reminders = pg_billing.trial_reminders_due(conn, now=moment, limit=limit)
        endings = pg_billing.subscription_ended_due(conn, now=moment, limit=limit)
    report.reminders_due, report.ended_due = len(reminders), len(endings)
    if not apply:
        return report
    if mailer is None:
        raise ValueError("a mailer is needed to send")

    def send(message) -> bool:
        try:
            mailer.send(message)
            return True
        except MailError as problem:
            report.failed += 1
            report.reasons[problem.reason] = report.reasons.get(problem.reason, 0) + 1
            return False

    for reminder in reminders:
        if send(trial_reminder_email(to=reminder.email, first_name=reminder.first_name, ends_at=reminder.ends_at)):
            with system.system_session("sweep") as conn, conn.transaction():
                if pg_billing.mark_reminder_sent(conn, reminder.redemption_id, now=moment):
                    audit.record(conn, "trial_reminder_sent", user_id=reminder.user_id, actor=ACTOR_SYSTEM)
            report.reminders_sent += 1
    for ending in endings:
        if send(subscription_ended_email(to=ending.email, first_name=ending.first_name)):
            with system.system_session("sweep") as conn, conn.transaction():
                if pg_billing.mark_ended_notice_sent(conn, ending.user_id, now=moment):
                    audit.record(conn, "subscription_ended_notice_sent", user_id=ending.user_id, actor=ACTOR_SYSTEM)
            report.ended_sent += 1
    return report


@dataclass
class BetaSignupReport:
    due: int = 0  # unused sign-ups older than the age given
    erased: int = 0
    dry_run: bool = True


def run_beta_signups(
    system: SystemDatabase, *, keep: timedelta, apply: bool, now: datetime | None = None, limit: int = 100
) -> BetaSignupReport:
    """Remove the sign-ups and codes of addresses with no account whose last code is older than `keep`."""
    if keep < timedelta(hours=MIN_KEEP_HOURS):
        raise ValueError(f"beta sign-ups are kept at least {MIN_KEEP_HOURS} hours")
    before = (now or datetime.now(UTC)) - keep
    report = BetaSignupReport(dry_run=not apply)
    with system.system_session("sweep") as conn:
        report.due = beta.erase_unused(conn, before=before, limit=limit, apply=False)
    if not apply or report.due == 0:
        return report
    with system.system_session("sweep") as conn, conn.transaction():
        report.erased = beta.erase_unused(conn, before=before, limit=limit, apply=True)
        if report.erased:
            audit.record(conn, "beta_signups_erased", actor=ACTOR_SYSTEM, meta={"count": report.erased})
    return report


def _remove_identities(system: SystemDatabase, directory: IdentityDirectory | None, report: SweepReport) -> None:
    with system.system_session("sweep") as conn:
        waiting = [
            r["idp_subject"] for r in conn.execute("SELECT idp_subject FROM identity_removals ORDER BY created_at")
        ]
    if directory is None:
        report.identities_pending = len(waiting)
        return
    for subject in waiting:
        try:
            gone = directory.delete_user(subject)
            reason = None if gone else "refused"
        except IdentityError as problem:
            gone, reason = False, problem.reason
        with system.system_session("sweep") as conn:
            if gone:
                conn.execute("DELETE FROM identity_removals WHERE idp_subject = %s", (subject,))
                report.identities_removed += 1
            else:
                conn.execute(
                    "UPDATE identity_removals SET attempts = attempts + 1, last_error = %s WHERE idp_subject = %s",
                    (reason, subject),
                )
                report.identities_pending += 1


def _remove_files(system: SystemDatabase, files: FileStore | None, report: SweepReport) -> None:
    with system.system_session("sweep") as conn:
        waiting = [r["prefix"] for r in conn.execute("SELECT prefix FROM file_removals ORDER BY created_at, prefix")]
    if files is None:
        report.files_pending = len(waiting)
        return
    for prefix in waiting:
        try:
            files.delete_prefix(prefix)
            reason = None
        except Exception as problem:  # noqa: BLE001 - a store can fail in ways nobody can list; the entry stays for next time
            reason = type(problem).__name__  # the kind of error only: a message could hold a path
        with system.system_session("sweep") as conn:
            if reason is None:
                conn.execute("DELETE FROM file_removals WHERE prefix = %s", (prefix,))
                report.files_removed += 1
            else:
                conn.execute(
                    "UPDATE file_removals SET attempts = attempts + 1, last_error = %s WHERE prefix = %s",
                    (reason, prefix),
                )
                report.files_pending += 1


def _files_from_env() -> FileStore | None:
    """The same place the web server keeps files: the bucket (`TJ_S3_*`) or the folder (`TJ_FILES_DIR`). A bucket is the one
    a separate job can reach on a host where it cannot share the web server's disk."""
    bucket = BucketSettings.from_env(os.environ)
    folder = os.environ.get("TJ_FILES_DIR", "").strip()
    if bucket is not None and folder:
        raise ValueError("TJ_S3_* and TJ_FILES_DIR are two places to keep files: choose one")
    if bucket is not None:
        return BucketFileStore(bucket)
    return LocalFileStore(Path(folder)) if folder else None


def _directory_from_env() -> IdentityDirectory | None:
    domain, client, secret = (
        os.environ.get(k) for k in ("TJ_KINDE_DOMAIN", "TJ_KINDE_M2M_CLIENT_ID", "TJ_KINDE_M2M_CLIENT_SECRET")
    )
    if not (domain and client and secret):
        return None
    from ..auth.kinde_admin import KindeManagement

    return KindeManagement(domain=domain, client_id=client, client_secret=secret)


def _mailer_from_env() -> Mailer | None:
    """The web service's own email settings (`TJ_EMAIL_API_KEY`, `TJ_EMAIL_FROM`, optionally `TJ_EMAIL_API_URL`)."""
    key = os.environ.get("TJ_EMAIL_API_KEY", "").strip()
    sender = os.environ.get("TJ_EMAIL_FROM", "").strip()
    if not (key and sender):
        return None
    return ResendMailer(
        api_key=key, sender=sender, base_url=os.environ.get("TJ_EMAIL_API_URL", "").strip() or DEFAULT_API_URL
    )


def _billing_on() -> bool:
    return os.environ.get("TJ_BILLING_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _provider_from_env() -> BillingProvider | None:
    """The payment provider the job's settings name (`TJ_BILLING_PROVIDER=paddle` and its `TJ_PADDLE_*`), or None. The
    fake provider lives inside one process, so a job cannot use it."""
    from ..billing.factory import provider_from_env

    return provider_from_env(os.environ)


def _main_notices(args: argparse.Namespace, url: str) -> int:
    mailer = _mailer_from_env()
    if args.apply and mailer is None:
        print("refusing to send without TJ_EMAIL_API_KEY and TJ_EMAIL_FROM", file=sys.stderr)
        return 2
    system = SystemDatabase({"sweep": url})
    system.open()
    try:
        report = run_notices(
            system, mailer, apply=args.apply, limit=min(args.limit, ended_notices.BATCH * 5), billing=_billing_on()
        )
    finally:
        system.close()
    mode = "dry run: nothing changed" if report.dry_run else "applied"
    print(f"{mode}; due {report.due}, sent {report.sent}, failed {report.failed}")
    for reason, count in sorted(report.reasons.items()):
        print(f"  {reason}: {count}", file=sys.stderr)
    return 1 if report.failed else 0  # a refused email is a failed run on the host, where someone will see it


def _main_billing(args: argparse.Namespace, url: str) -> int:
    try:
        provider = _provider_from_env()
    except ValueError as problem:  # names the settings, never their values
        print(problem, file=sys.stderr)
        return 2
    system = SystemDatabase({"sweep": url})
    system.open()
    try:
        report = run_billing(system, provider, apply=args.apply, limit=min(args.limit * 10, 1000))
    finally:
        system.close()
    mode = "dry run: nothing changed" if report.dry_run else "applied"
    print(
        f"{mode}; cancels sent {report.cancels_sent}, cancels still to send {report.cancels_pending}, "
        f"subscriptions checked {report.checked}, corrected {report.corrected}, check failures {report.check_failed}"
    )
    for reason, count in sorted(report.reasons.items()):
        print(f"  {reason}: {count}", file=sys.stderr)
    if provider is None and report.cancels_pending:
        print("cancels are waiting because no payment provider is configured on this job", file=sys.stderr)
    return 1 if report.check_failed or (report.cancels_pending and provider is not None) else 0


def _main_billing_notices(args: argparse.Namespace, url: str) -> int:
    if not _billing_on():
        print("billing is switched off (TJ_BILLING_ENABLED): no billing emails")
        return 0
    mailer = _mailer_from_env()
    if args.apply and mailer is None:
        print("refusing to send without TJ_EMAIL_API_KEY and TJ_EMAIL_FROM", file=sys.stderr)
        return 2
    system = SystemDatabase({"sweep": url})
    system.open()
    try:
        report = run_billing_notices(system, mailer, apply=args.apply, limit=min(args.limit, 500))
    finally:
        system.close()
    mode = "dry run: nothing changed" if report.dry_run else "applied"
    print(
        f"{mode}; trial reminders due {report.reminders_due}, sent {report.reminders_sent}; "
        f"subscription endings due {report.ended_due}, sent {report.ended_sent}; failed {report.failed}"
    )
    for reason, count in sorted(report.reasons.items()):
        print(f"  {reason}: {count}", file=sys.stderr)
    return 1 if report.failed else 0


def _main_beta_signups(args: argparse.Namespace, url: str) -> int:
    hours = BETA_KEEP_HOURS if args.keep_hours is None else args.keep_hours
    if hours < MIN_KEEP_HOURS:
        print(f"--keep-hours must be at least {MIN_KEEP_HOURS}: a code works that long", file=sys.stderr)
        return 2
    system = SystemDatabase({"sweep": url})
    system.open()
    try:
        report = run_beta_signups(system, keep=timedelta(hours=hours), apply=args.apply, limit=min(args.limit, 500))
    finally:
        system.close()
    mode = "dry run: nothing changed" if report.dry_run else "applied"
    print(f"{mode}; sign-ups with no account past {hours} hours: {report.due}, removed {report.erased}")
    return 0


def _main_deletions(args: argparse.Namespace, url: str) -> int:
    directory = None if args.without_identity_provider else _directory_from_env()
    if args.apply and directory is None and not args.without_identity_provider:
        print(
            "refusing to delete without the identity provider's management settings "
            "(TJ_KINDE_DOMAIN, TJ_KINDE_M2M_CLIENT_ID, TJ_KINDE_M2M_CLIENT_SECRET); "
            "pass --without-identity-provider to delete here only",
            file=sys.stderr,
        )
        return 2
    try:
        files = _files_from_env()
    except ValueError as problem:  # names the settings, never their values
        print(problem, file=sys.stderr)
        return 2
    system = SystemDatabase({"sweep": url})
    system.open()
    try:
        report = run(system, directory, apply=args.apply, files=files, limit=args.limit)
    finally:
        system.close()
    mode = "dry run: nothing changed" if report.dry_run else "applied"
    print(
        f"{mode}; due {report.due}, deleted {report.deleted}, skipped {report.skipped}, "
        f"identities removed {report.identities_removed}, identities still to remove {report.identities_pending}, "
        f"file folders removed {report.files_removed}, file folders still to remove {report.files_pending}"
    )
    if report.files_pending and files is None:
        print("file folders are waiting because neither TJ_S3_* nor TJ_FILES_DIR is set on this job", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scheduled jobs: delete accounts whose time has come, and send the ending notices."
    )
    parser.add_argument(
        "command",
        choices=["deletions", "ended-notices", "all", "beta-signups", "billing", "billing-notices"],
        help="`all` runs every one, in that order, and fails if any did (so one scheduled job covers them)",
    )
    parser.add_argument("--apply", action="store_true", help="actually do it (the default only counts)")
    parser.add_argument("--limit", type=int, default=BATCH, help="at most this many per run")
    parser.add_argument(
        "--keep-hours",
        type=int,
        default=None,
        help="beta-signups only: remove sign-ups with no account whose last code is older than this (default and minimum 24)",
    )
    parser.add_argument(
        "--without-identity-provider",
        action="store_true",
        help="do not remove identities at the provider (local development only)",
    )
    args = parser.parse_args(argv)
    url = os.environ.get("TJ_SWEEP_DATABASE_URL")
    if not url:
        print("TJ_SWEEP_DATABASE_URL is not set", file=sys.stderr)
        return 2
    if args.command == "deletions":
        return _main_deletions(args, url)
    if args.command == "ended-notices":
        return _main_notices(args, url)
    if args.command == "beta-signups":
        return _main_beta_signups(args, url)
    if args.command == "billing":
        return _main_billing(args, url)
    if args.command == "billing-notices":
        return _main_billing_notices(args, url)
    # `all`: every step, even when one cannot run, so a missing setting for one never stops the others.
    return max(
        _main_deletions(args, url),
        _main_notices(args, url),
        _main_beta_signups(args, url),
        _main_billing(args, url),
        _main_billing_notices(args, url),
    )


if __name__ == "__main__":
    sys.exit(main())
