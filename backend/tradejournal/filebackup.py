"""A second copy of the screenshot bucket, because R2 keeps no old versions (docs/runbooks/deploy.md section 2, step 4).

    python -m tradejournal.filebackup              # a dry run: counts only, nothing is touched
    python -m tradejournal.filebackup --apply      # copy what the second bucket lacks, and forget what has been gone long enough

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

It prints counts, never a key, a name or a credential.
"""

from __future__ import annotations

import argparse
import json
import os
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


def main(argv: list[str] | None = None) -> int:
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
    try:
        report = run(
            source,
            BucketFileStore(destination),
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
