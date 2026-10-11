"""Empty one person's journal, so that something else (the local-journal migration) can be put there.

    python -m tradejournal.pg.clear_journal --user-id <uuid>                       # a dry run: counts, changes nothing
    python -m tradejournal.pg.clear_journal --user-id <uuid> --apply --expect-accounts 1 --expect-trades 8

For the owner, by hand. Nothing in the app does this: a prop account can be archived, never deleted. The command removes the
person's accounts (and with them, by cascade, their fees, imports, fill pairs, trades, tag links, screenshot rows and ledger),
their tags and their day notes. It keeps everything else about the person: the sign-in, the settings, the tag groups.

It is built to be hard to point at the wrong journal:

- ``TJ_DATABASE_URL`` (the request role, ``app_rw``) and ``--user-id``: one person at a time, through the same tenant session
  the app uses, so it can only ever see that person's rows.
- A dry run is the default. ``--apply`` also needs ``--expect-accounts`` and ``--expect-trades``, and refuses when the real
  counts differ: the journal you looked at in the dry run is the one that is emptied, not one that has since filled up.
- It **refuses** while any screenshot exists. Their files would be left behind in the bucket; delete the pictures in the app
  first (or ask for the files to be handled as part of this).
- It prints counts only: no account names, notes or amounts.

Everything is one transaction. A failure leaves the journal as it was.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from uuid import UUID

import psycopg

from . import usage
from .session import Database


class ClearRefused(Exception):
    """The journal was not touched, and why (a sentence that names no data)."""


@dataclass(frozen=True)
class Counts:
    accounts: int
    trades: int
    tags: int
    day_notes: int
    screenshots: int

    def empty(self) -> bool:
        return not any((self.accounts, self.trades, self.tags, self.day_notes, self.screenshots))

    def sentence(self) -> str:
        return (
            f"{self.accounts} accounts, {self.trades} trades, {self.tags} tags, "
            f"{self.day_notes} day notes, {self.screenshots} screenshots"
        )


def count(conn: psycopg.Connection) -> Counts:
    row = conn.execute(
        """SELECT (SELECT count(*) FROM accounts) AS accounts, (SELECT count(*) FROM trades) AS trades,
                  (SELECT count(*) FROM tags) AS tags, (SELECT count(*) FROM day_notes) AS day_notes,
                  (SELECT count(*) FROM screenshots) AS screenshots"""
    ).fetchone()
    return Counts(row["accounts"], row["trades"], row["tags"], row["day_notes"], row["screenshots"])


def _refuse_screenshots(found: Counts) -> None:
    if found.screenshots:
        raise ClearRefused(
            f"the journal holds {found.screenshots} screenshots; their files would be left behind. "
            "Delete the pictures in the app first"
        )


def clear(conn: psycopg.Connection, *, expect_accounts: int, expect_trades: int) -> tuple[Counts, Counts]:
    """Empty the journal of the person `conn` belongs to. Returns the counts before and after. Raises `ClearRefused`."""
    before = count(conn)
    _refuse_screenshots(before)
    if (before.accounts, before.trades) != (expect_accounts, expect_trades):
        raise ClearRefused(
            f"the journal holds {before.accounts} accounts and {before.trades} trades, "
            f"not the {expect_accounts} and {expect_trades} that were expected"
        )
    conn.execute("DELETE FROM accounts")  # cascades: fees, imports, fill pairs, trades (and their tag links), ledger
    conn.execute("DELETE FROM tags")
    conn.execute("DELETE FROM day_notes")
    usage.refresh(conn)  # the counters are kept by the domain functions; a direct delete has to recount them
    after = count(conn)
    if not after.empty():  # a table this command does not know about would keep a trade or an account alive
        raise ClearRefused(f"something was left behind ({after.sentence()}); nothing was changed")
    return before, after


def run(
    database: Database, user_id: UUID, *, apply: bool, expect_accounts: int | None, expect_trades: int | None
) -> Counts:
    """The counts that were (or, in a dry run, would be) removed."""
    with database.tenant_session(user_id) as conn:
        with conn.transaction():
            if not apply:
                found = count(conn)
                _refuse_screenshots(found)
                return found
            if expect_accounts is None or expect_trades is None:
                raise ValueError("applying needs the expected counts")
            before, _ = clear(conn, expect_accounts=expect_accounts, expect_trades=expect_trades)
            return before


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Empty one person's journal. A dry run unless --apply.")
    parser.add_argument("--user-id", type=UUID, required=True, help="the id of the person whose journal is emptied")
    parser.add_argument(
        "--database-url", default=os.environ.get("TJ_DATABASE_URL"), help="default: $TJ_DATABASE_URL (app_rw)"
    )
    parser.add_argument("--apply", action="store_true", help="actually remove (the default only counts)")
    parser.add_argument("--expect-accounts", type=int, help="with --apply: the number of accounts the dry run showed")
    parser.add_argument("--expect-trades", type=int, help="with --apply: the number of trades the dry run showed")
    args = parser.parse_args(argv)
    if not args.database_url:
        print("set TJ_DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2
    if args.apply and (args.expect_accounts is None or args.expect_trades is None):
        print("--apply needs --expect-accounts and --expect-trades (the numbers a dry run showed)", file=sys.stderr)
        return 2
    database = Database(args.database_url, max_size=2)
    database.open()
    try:
        removed = run(
            database,
            args.user_id,
            apply=args.apply,
            expect_accounts=args.expect_accounts,
            expect_trades=args.expect_trades,
        )
    except ClearRefused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 3
    finally:
        database.close()
    print(("applied; removed " if args.apply else "dry run: nothing changed; would remove ") + removed.sentence())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
