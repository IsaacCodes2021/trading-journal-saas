"""A second copy of the screenshot bucket, because R2 keeps no old versions (docs/runbooks/deploy.md section 2, step 4).

    python -m tradejournal.filebackup              # a dry run: counts only, nothing is touched
    python -m tradejournal.filebackup --apply      # copy what the second bucket lacks, and forget what has been gone long enough
    python -m tradejournal.filebackup restore --prefix u/<user id>/ [--apply]   # put one person's pictures back
    python -m tradejournal.filebackup restore --all [--apply]                   # put back all the original lacks

Run it as a scheduled job (daily is plenty). The source is the bucket the web service uses (``TJ_S3_*``, or ``TJ_FILES_DIR``
in development); the copy is any other S3-compatible bucket, preferably at another provider, set by the same four
settings with ``TJ_BACKUP_S3_`` in front (``TJ_BACKUP_S3_ENDPOINT``, ``_BUCKET``, ``_ACCESS_KEY_ID``, ``_SECRET_ACCESS_KEY``,
optionally ``_REGION``).

Pictures never change once stored (the server writes each under a name it makes and never overwrites it), so a copy is a
matter of "which keys does the second bucket not have yet". What leaves the copy is decided like this, because a copy that
never forgets would keep a deleted account's pictures forever:

- a picture that is gone from the source stays in the copy for ``--keep-days`` (30 by default), so a bad deletion, a bug or a
  leaked token can be undone within that time, and is then deleted from the copy too (an account deletion included: the
  privacy wording has to say that backups hold deleted pictures for up to that long);
- the first time a key is seen missing is written in a small file in the copy (``state/missing.json``), so the clock keeps
  running between runs; a key that comes back in the source is forgotten;
- it refuses to forget anything when the source lists **nothing** while the copy holds files, which is what a wrong bucket
  or a failed listing looks like, and it never forgets more than ``--max-purge`` keys in one run;
- it refuses to run when the source and the copy are the same bucket.

Restoring is the other direction. ``restore`` copies, from the copy back into the original, the pictures the original
lacks. It is a dry run unless ``--apply``; it never overwrites a picture the original has; it needs a scope, ``--prefix``
for one person or ``--all``, because ``--all`` also brings back pictures people deleted within the keep time (the copy
holds them for that long on purpose, not to be shown again); and it needs a token that can WRITE to the original, which
the daily job's read-only token cannot (docs/runbooks/restore.md).

It prints counts, never a key, a name or a credential.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .bucketstore import BucketFileStore, BucketSettings, StoreUnavailable
from .filestore import FileMissing, FileStore
from .pg.sweep import _files_from_env

PREFIX = "u"  # every picture lives under u/<user id>/
STATE_KEY = "state/missing.json"  # in the copy only; not under u/, so it is never listed as a picture
DEFAULT_KEEP_DAYS = 30
DEFAULT_LIMIT = 500
DEFAULT_MAX_PURGE = 200


@dataclass
class BackupReport:
    in_source: int = 0
    to_copy: int = 0
    copied: int = 0
    vanished: int = 0  # listed in the source, gone by the time it was read (a person deleted it): nothing to copy
    gone_from_source: int = 0  # in the copy, not in the source: held until the keep time passes
    purged: int = 0
    purge_waiting: int = 0  # past the keep time but over this run's cap: the next run takes them
    refused: str | None = None  # why it did not forget anything, if it did not
    dry_run: bool = True


def _load_state(copy: FileStore) -> dict[str, str]:
    try:
        raw = json.loads(copy.get(STATE_KEY))
    except FileMissing:
        return {}
    except ValueError:  # unreadable: start the clocks again rather than guess (it only delays a deletion)
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(raw, dict) else {}


def _seen(text: str, fallback: datetime) -> datetime:
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return fallback
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def run(
    source: FileStore,
    copy: FileStore,
    *,
    apply: bool = False,
    keep_days: int = DEFAULT_KEEP_DAYS,
    limit: int = DEFAULT_LIMIT,
    max_purge: int = DEFAULT_MAX_PURGE,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> BackupReport:
    report = BackupReport(dry_run=not apply)
    moment = now()
    in_source = {f.key: f for f in source.list(PREFIX)}
    in_copy = {f.key: f for f in copy.list(PREFIX)}
    report.in_source = len(in_source)

    # 1. copy what the second bucket lacks (a key it has with a different size is copied again: it should not happen)
    wanted = sorted(k for k, f in in_source.items() if k not in in_copy or in_copy[k].size != f.size)
    report.to_copy = len(wanted)
    if apply:
        for key in wanted[:limit]:
            try:
                data = source.get(key)
            except FileMissing:
                report.vanished += 1
                continue
            copy.put(key, data)
            report.copied += 1

    # 2. forget, after the keep time, what the source no longer has
    gone = sorted(set(in_copy) - set(in_source))
    report.gone_from_source = len(gone)
    state = _load_state(copy)
    clocks = {key: state.get(key, moment.isoformat()) for key in gone}
    if not in_source and in_copy:
        report.refused = "the source lists no files but the copy holds some; nothing was forgotten"
    else:
        expired = [k for k in gone if moment - _seen(clocks[k], moment) >= timedelta(days=keep_days)]
        report.purge_waiting = max(0, len(expired) - max_purge)
        if apply:
            for key in expired[:max_purge]:
                copy.delete(key)
                clocks.pop(key, None)
                report.purged += 1
    if apply and clocks != state:
        copy.put(STATE_KEY, json.dumps(clocks, sort_keys=True).encode("utf-8"))
    return report


@dataclass
class RestoreReport:
    in_copy: int = 0  # pictures the copy holds in the chosen scope
    present: int = 0  # already in the original, same size: left alone
    differing: int = 0  # in the original with another size: left alone, never overwritten
    to_restore: int = 0
    restored: int = 0
    vanished: int = 0  # listed in the copy, gone when read: nothing to put back
    dry_run: bool = True


def restore(
    source: FileStore,
    copy: FileStore,
    *,
    scope: str | None = None,
    apply: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> RestoreReport:
    """Put back into the original (`source`) the pictures the copy holds and the original lacks.

    It never overwrites a picture the original has, whatever its size. `scope` is `u/<user id>/` for one person, or None
    for every picture. Only keys under `u/` count, so the copy's own state file is never put anywhere."""
    report = RestoreReport(dry_run=not apply)
    where = scope or PREFIX
    in_copy = {f.key: f for f in copy.list(where) if f.key.startswith(PREFIX + "/")}
    in_source = {f.key: f for f in source.list(where) if f.key.startswith(PREFIX + "/")}
    report.in_copy = len(in_copy)
    report.present = sum(1 for k, f in in_copy.items() if k in in_source and in_source[k].size == f.size)
    report.differing = sum(1 for k, f in in_copy.items() if k in in_source and in_source[k].size != f.size)
    wanted = sorted(k for k in in_copy if k not in in_source)
    report.to_restore = len(wanted)
    if apply:
        for key in wanted[:limit]:
            try:
                data = copy.get(key)
            except FileMissing:
                report.vanished += 1
                continue
            source.put(key, data)
            report.restored += 1
    return report


def _copy_settings(env: Mapping[str, str]) -> BucketSettings | None:
    renamed = {
        name.replace("TJ_BACKUP_S3_", "TJ_S3_", 1): value
        for name, value in env.items()
        if name.startswith("TJ_BACKUP_S3_")
    }
    try:
        return BucketSettings.from_env(renamed)
    except ValueError as problem:  # names the settings, never their values
        raise ValueError(str(problem).replace("TJ_S3_", "TJ_BACKUP_S3_")) from None


def _open_stores() -> tuple[FileStore, BucketFileStore] | int:
    """The original and the copy from the environment, or the exit code to give (and the reason, already printed)."""
    try:
        source = _files_from_env()
        destination = _copy_settings(os.environ)
    except ValueError as problem:
        print(problem, file=sys.stderr)
        return 2
    if source is None:
        print("neither TJ_S3_* nor TJ_FILES_DIR is set: there is no source to copy", file=sys.stderr)
        return 2
    if destination is None:
        print("TJ_BACKUP_S3_* is not set: there is nowhere to copy to", file=sys.stderr)
        return 2
    if isinstance(source, BucketFileStore):
        origin = BucketSettings.from_env(os.environ)
        if origin is not None and (origin.endpoint, origin.bucket) == (destination.endpoint, destination.bucket):
            print("the copy is the same bucket as the source: refusing to run", file=sys.stderr)
            return 2
    return source, BucketFileStore(destination)


_PERSON_FOLDER = re.compile(r"u/[a-z0-9][a-z0-9-]*/")


def _main_restore(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tradejournal.filebackup restore",
        description="Put pictures back into the original from the copy. A dry run unless --apply.",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--prefix", help="one person's folder, u/<user id>/")
    scope.add_argument(
        "--all",
        action="store_true",
        help="everything the copy holds that the original lacks, including pictures deleted within the keep time",
    )
    parser.add_argument("--apply", action="store_true", help="actually put them back (the default only counts)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="at most this many put back per run")
    args = parser.parse_args(argv)
    if args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 2
    if args.prefix is not None and not _PERSON_FOLDER.fullmatch(args.prefix):
        print("--prefix must be one person's folder: u/<user id>/ (with the slash at the end)", file=sys.stderr)
        return 2
    opened = _open_stores()
    if isinstance(opened, int):
        return opened
    original, copy = opened
    try:
        report = restore(original, copy, scope=args.prefix, apply=args.apply, limit=args.limit)
    except StoreUnavailable:
        print(
            "a bucket did not answer or refused the request. A token that can only read the original refuses a "
            "restore (docs/runbooks/restore.md). Pictures already put back stay; run it again.",
            file=sys.stderr,
        )
        return 1
    mode = "dry run: nothing changed" if report.dry_run else "applied"
    print(
        f"{mode}; in the copy {report.in_copy}, already in the original {report.present}, "
        f"in the original with another size {report.differing}, to put back {report.to_restore}, "
        f"put back {report.restored}, gone before it was read {report.vanished}"
    )
    if args.all and report.in_copy == 0:
        print("the copy lists no pictures: this looks like the wrong bucket, nothing was put back", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if given and given[0] == "restore":
        return _main_restore(given[1:])
    parser = argparse.ArgumentParser(description="Keep a second copy of the screenshot bucket.")
    parser.add_argument("--apply", action="store_true", help="actually copy and forget (the default only counts)")
    parser.add_argument(
        "--keep-days", type=int, default=DEFAULT_KEEP_DAYS, help="days a deleted picture stays in the copy"
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="at most this many pictures copied per run")
    parser.add_argument("--max-purge", type=int, default=DEFAULT_MAX_PURGE, help="at most this many forgotten per run")
    args = parser.parse_args(argv)
    if args.keep_days < 1 or args.limit < 1 or args.max_purge < 0:
        print("--keep-days and --limit must be at least 1, and --max-purge cannot be negative", file=sys.stderr)
        return 2
    opened = _open_stores()
    if isinstance(opened, int):
        return opened
    source, copy = opened
    try:
        report = run(
            source,
            copy,
            apply=args.apply,
            keep_days=args.keep_days,
            limit=args.limit,
            max_purge=args.max_purge,
        )
    except StoreUnavailable:
        print("a bucket did not answer or refused the request; nothing was lost, run it again", file=sys.stderr)
        return 1
    mode = "dry run: nothing changed" if report.dry_run else "applied"
    print(
        f"{mode}; in the source {report.in_source}, to copy {report.to_copy}, copied {report.copied}, "
        f"gone before it was read {report.vanished}; gone from the source and held {report.gone_from_source}, "
        f"forgotten {report.purged}, forgotten next run {report.purge_waiting}"
    )
    if report.refused:
        print(report.refused, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
