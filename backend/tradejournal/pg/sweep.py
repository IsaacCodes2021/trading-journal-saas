"""The background sweep that deletes accounts whose time has come (requirement F-A6; `pg/deletion.py` and migration 0008).

    python -m tradejournal.pg.sweep deletions              # a dry run: how many are due, nothing is touched
    python -m tradejournal.pg.sweep deletions --apply      # delete them

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
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from ..auth.idp import IdentityDirectory, IdentityError
from ..filestore import FileStore, LocalFileStore
from . import audit, deletion
from .system import SystemDatabase

BATCH = 50
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
    folder = os.environ.get("TJ_FILES_DIR", "").strip()
    return LocalFileStore(Path(folder)) if folder else None


def _directory_from_env() -> IdentityDirectory | None:
    domain, client, secret = (
        os.environ.get(k) for k in ("TJ_KINDE_DOMAIN", "TJ_KINDE_M2M_CLIENT_ID", "TJ_KINDE_M2M_CLIENT_SECRET")
    )
    if not (domain and client and secret):
        return None
    from ..auth.kinde_admin import KindeManagement

    return KindeManagement(domain=domain, client_id=client, client_secret=secret)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete the accounts whose scheduled time has come.")
    parser.add_argument("command", choices=["deletions"])
    parser.add_argument("--apply", action="store_true", help="actually delete (the default only counts)")
    parser.add_argument("--limit", type=int, default=BATCH, help="at most this many accounts per run")
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
    directory = None if args.without_identity_provider else _directory_from_env()
    if args.apply and directory is None and not args.without_identity_provider:
        print(
            "refusing to delete without the identity provider's management settings "
            "(TJ_KINDE_DOMAIN, TJ_KINDE_M2M_CLIENT_ID, TJ_KINDE_M2M_CLIENT_SECRET); "
            "pass --without-identity-provider to delete here only",
            file=sys.stderr,
        )
        return 2
    files = _files_from_env()
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
        print("file folders are waiting because TJ_FILES_DIR is not set on this job", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
