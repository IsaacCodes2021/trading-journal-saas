"""Copy-trading groups, on Postgres (migration 0026; the decisions are in `docs/new-feature-copy-trading/decisions.csv`).

A group is a leader account and the accounts that copy it. Only the leader's trades are stored; a follower's copies are
worked out when trades are read (`pg/queries.py`), from the membership events kept here. Nothing in this module writes a
trade, a fill pair or an import: a group of 20 accounts is one group row and its events.

Membership is a log, never edited. Followers chosen when the group is made count from the "copying since" day, or from
the start when none is given; a later change is dated today or later (decisions 9, 21, 22), so a past trade keeps the
followers it had. Every function takes a tenant connection: row-level security limits it to one person's rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg

from ..finance import ZERO
from .accounts import get_account

NAME_MAX = 80
NOTE_MAX = 500


@dataclass(frozen=True)
class MembershipEvent:
    id: int
    account_id: int
    event_type: str  # "added" or "removed"
    effective_date: date | None  # None: an "added" that counts from the start
    note: str
    created_at: datetime


@dataclass(frozen=True)
class Window:
    """Days a follower counted the leader's trades: from `starts` (None: the start) up to, not including, `ends`."""

    starts: date | None
    ends: date | None

    def covers(self, day: date) -> bool:
        return (self.starts is None or day >= self.starts) and (self.ends is None or day < self.ends)


@dataclass(frozen=True)
class CopyGroup:
    id: int
    name: str
    leader_account_id: int
    fills_acknowledged_at: datetime
    created_at: datetime
    events: tuple[MembershipEvent, ...]

    def windows(self, account_id: int) -> list[Window]:
        return windows_of([e for e in self.events if e.account_id == account_id])

    def follower_ids(self) -> list[int]:
        """Every account that has ever followed, in the order it first joined."""
        seen: dict[int, None] = {}
        for event in self.events:
            seen.setdefault(event.account_id, None)
        return list(seen)

    def is_active(self, account_id: int) -> bool:
        """In the group now: its last event is an "added" (a removal dated in the future still counts as in)."""
        own = [e for e in self.events if e.account_id == account_id]
        return bool(own) and own[-1].event_type == "added"


def windows_of(events: Sequence[MembershipEvent]) -> list[Window]:
    """One account's events in one group (in log order) as the windows it counted trades in."""
    ordered = sorted(events, key=lambda e: (e.effective_date is not None, e.effective_date or date.min, e.id))
    windows: list[Window] = []
    for at, event in enumerate(ordered):
        if event.event_type != "added":
            continue
        following = ordered[at + 1] if at + 1 < len(ordered) else None
        windows.append(Window(event.effective_date, following.effective_date if following else None))
    return windows


# ---- reading -----------------------------------------------------------------------------------------------------


def list_groups(conn: psycopg.Connection) -> list[CopyGroup]:
    """Every group, by name, each with its whole membership log in the order it was written."""
    groups = conn.execute("SELECT * FROM copy_groups ORDER BY lower(name), id").fetchall()
    events = conn.execute(
        "SELECT * FROM copy_group_membership_events ORDER BY group_id, effective_date NULLS FIRST, id"
    ).fetchall()
    by_group: dict[int, list[MembershipEvent]] = {}
    for row in events:
        by_group.setdefault(row["group_id"], []).append(_event(row))
    return [_group(row, by_group.get(row["id"], [])) for row in groups]


def get_group(conn: psycopg.Connection, group_id: int) -> CopyGroup:
    for group in list_groups(conn):
        if group.id == group_id:
            return group
    raise LookupError(f"no copy-trading group with id {group_id}")


def group_led_by(conn: psycopg.Connection, account_id: int) -> CopyGroup | None:
    return next((g for g in list_groups(conn) if g.leader_account_id == account_id), None)


def member_totals(conn: psycopg.Connection) -> dict[tuple[int, int], tuple[int, Decimal]]:
    """(group id, account id) to (trade count, net P&L) for every member that has trades, all time, archived accounts
    included: the leader's own stored trades, and each follower's copies from that group only."""
    rows = conn.execute(
        """WITH windows AS (
               SELECT group_id, account_id, event_type, effective_date AS starts,
                      lead(effective_date) OVER (
                          PARTITION BY group_id, account_id ORDER BY effective_date NULLS FIRST, id
                      ) AS ends
               FROM copy_group_membership_events
           )
           SELECT copy_groups.id AS group_id, copy_groups.leader_account_id AS account_id,
                  count(*) AS trades, sum(trades.net_pnl) AS net
           FROM copy_groups JOIN trades ON trades.account_id = copy_groups.leader_account_id
           GROUP BY copy_groups.id, copy_groups.leader_account_id
           UNION ALL
           SELECT copy_groups.id, windows.account_id, count(*), sum(trades.net_pnl)
           FROM copy_groups
           JOIN trades ON trades.account_id = copy_groups.leader_account_id
           JOIN windows ON windows.group_id = copy_groups.id AND windows.event_type = 'added'
                AND (windows.starts IS NULL OR trades.trade_date >= windows.starts)
                AND (windows.ends IS NULL OR trades.trade_date < windows.ends)
           GROUP BY copy_groups.id, windows.account_id"""
    ).fetchall()
    totals: dict[tuple[int, int], tuple[int, Decimal]] = {}
    for row in rows:
        key = (row["group_id"], row["account_id"])
        count, net = totals.get(key, (0, ZERO))
        totals[key] = (count + int(row["trades"]), net + row["net"])
    return totals


def copied_to(conn: psycopg.Connection, trade_id: int) -> list[int]:
    """The follower accounts a stored trade was copied to (in their group when it was traded), by account name."""
    trade = conn.execute("SELECT account_id, trade_date FROM trades WHERE id = %s", (trade_id,)).fetchone()
    if trade is None:
        return []
    group = group_led_by(conn, trade["account_id"])
    if group is None:
        return []
    ids = [a for a in group.follower_ids() if any(w.covers(trade["trade_date"]) for w in group.windows(a))]
    if not ids:
        return []
    rows = conn.execute("SELECT id FROM accounts WHERE id = ANY(%s::bigint[]) ORDER BY lower(name), id", (ids,))
    return [row["id"] for row in rows]


# ---- writing -----------------------------------------------------------------------------------------------------


def create_group(
    conn: psycopg.Connection,
    name: str,
    *,
    leader_account_id: int,
    follower_account_ids: Iterable[int],
    acknowledged_at: datetime,
    copying_since: date | None = None,
    note: str = "",
) -> CopyGroup:
    """A new group. `acknowledged_at` is when the person accepted that copies do not always fill the same (decision 6):
    there is no group without it. The followers count the leader's trades from `copying_since`, or all of them."""
    name = _check_name(name)
    note = _check_note(note)
    get_account(conn, leader_account_id)
    followers = list(dict.fromkeys(follower_account_ids))
    if not followers:
        raise ValueError("choose at least one follower account")
    if leader_account_id in followers:
        raise ValueError("the leader cannot also be one of its own followers")
    for follower in followers:
        get_account(conn, follower)
    if group_led_by(conn, leader_account_id) is not None:
        raise ValueError("that account already leads a group; add followers to that group instead")
    if conn.execute("SELECT 1 FROM copy_groups WHERE lower(btrim(name)) = lower(%s)", (name,)).fetchone():
        raise ValueError("you already have a group with that name")
    row = conn.execute(
        """INSERT INTO copy_groups (name, leader_account_id, fills_acknowledged_at) VALUES (%s, %s, %s)
           RETURNING id""",
        (name, leader_account_id, acknowledged_at),
    ).fetchone()
    for follower in followers:
        _insert_event(conn, row["id"], follower, "added", copying_since, note)
    return get_group(conn, row["id"])


def rename_group(conn: psycopg.Connection, group_id: int, name: str) -> CopyGroup:
    name = _check_name(name)
    get_group(conn, group_id)
    clash = conn.execute(
        "SELECT 1 FROM copy_groups WHERE lower(btrim(name)) = lower(%s) AND id <> %s", (name, group_id)
    ).fetchone()
    if clash:
        raise ValueError("you already have a group with that name")
    conn.execute("UPDATE copy_groups SET name = %s WHERE id = %s", (name, group_id))
    return get_group(conn, group_id)


def delete_group(conn: psycopg.Connection, group_id: int) -> None:
    """End a group: the group and its membership log go; every trade stays on the leader (decision 30). The followers'
    copies stop existing, since they were never stored."""
    get_group(conn, group_id)
    conn.execute("DELETE FROM copy_groups WHERE id = %s", (group_id,))


def add_follower(
    conn: psycopg.Connection, group_id: int, account_id: int, *, effective_date: date, today: date, note: str = ""
) -> CopyGroup:
    """`account_id` copies the leader from `effective_date` on, today (the person's trading day) or later."""
    group = get_group(conn, group_id)
    get_account(conn, account_id)
    note = _check_note(note)
    if account_id == group.leader_account_id:
        raise ValueError("the leader cannot also be one of its own followers")
    if group.is_active(account_id):
        raise ValueError("that account is already in this group")
    _check_forward(group, account_id, effective_date, today)
    _insert_event(conn, group_id, account_id, "added", effective_date, note)
    return get_group(conn, group_id)


def remove_follower(
    conn: psycopg.Connection, group_id: int, account_id: int, *, effective_date: date, today: date, note: str = ""
) -> CopyGroup:
    """`account_id` stops copying from `effective_date` (that day's trades no longer count). Its earlier copies stay."""
    group = get_group(conn, group_id)
    note = _check_note(note)
    if not group.is_active(account_id):
        raise LookupError(f"account {account_id} is not in group {group_id}")
    _check_forward(group, account_id, effective_date, today)
    _insert_event(conn, group_id, account_id, "removed", effective_date, note)
    return get_group(conn, group_id)


# ---- helpers -----------------------------------------------------------------------------------------------------


def _check_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise ValueError("give the group a name")
    if len(name) > NAME_MAX:
        raise ValueError(f"a group's name is at most {NAME_MAX} characters")
    return name


def _check_note(note: str) -> str:
    note = note.strip()
    if len(note) > NOTE_MAX:
        raise ValueError(f"a note is at most {NOTE_MAX} characters")
    return note


def _check_forward(group: CopyGroup, account_id: int, effective_date: date, today: date) -> None:
    """Forward only (decision 22): a change takes effect today or later, and never before the account's last change."""
    if effective_date < today:
        raise ValueError("a change takes effect today or later; past trades keep the followers they had")
    last = [e.effective_date for e in group.events if e.account_id == account_id and e.effective_date is not None]
    if last and effective_date < max(last):
        raise ValueError(
            f"this account's last change in the group is dated {max(last).isoformat()}; pick that day or later"
        )


def _insert_event(
    conn: psycopg.Connection, group_id: int, account_id: int, event_type: str, effective_date: date | None, note: str
) -> None:
    conn.execute(
        """INSERT INTO copy_group_membership_events (group_id, account_id, event_type, effective_date, note)
           VALUES (%s, %s, %s, %s, %s)""",
        (group_id, account_id, event_type, effective_date, note),
    )


def _event(row: dict) -> MembershipEvent:
    return MembershipEvent(
        id=row["id"],
        account_id=row["account_id"],
        event_type=row["event_type"],
        effective_date=row["effective_date"],
        note=row["note"],
        created_at=row["created_at"],
    )


def _group(row: dict, events: list[MembershipEvent]) -> CopyGroup:
    return CopyGroup(
        id=row["id"],
        name=row["name"],
        leader_account_id=row["leader_account_id"],
        fills_acknowledged_at=row["fills_acknowledged_at"],
        created_at=row["created_at"],
        events=tuple(events),
    )
