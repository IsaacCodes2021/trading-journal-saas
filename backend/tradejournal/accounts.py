"""Trading accounts and their fee schedules."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from .finance import DEFAULT_FEE_KEY, ZERO, money_str, parse_decimal
from .timeutil import to_utc_iso

# Alphabetical, with Other last. Add firms here; the database doesn't restrict them.
FIRMS = ("Apex", "Bulenox", "Lucid", "My Funded Futures", "Take Profit", "Tradeify", "Other")
ACCOUNT_TYPES = {"evaluation": "Evaluation", "funded": "Funded"}  # stored value -> display name
TRAILING_TYPES = ("intraday", "eod")
# How an archived account ended. Only evaluations can pass.
OUTCOMES = {"passed": "Passed", "failed": "Failed", "closed": "Closed"}
OUTCOMES_BY_TYPE = {"evaluation": ("passed", "failed", "closed"), "funded": ("failed", "closed")}


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    firm: str
    account_type: str  # "evaluation" or "funded"
    account_size: Decimal | None
    starting_balance: Decimal
    max_drawdown: Decimal | None
    trailing_type: str | None
    trail_lock_enabled: bool
    trail_lock_level: Decimal | None
    daily_loss_limit: Decimal | None
    profit_target: Decimal | None
    created_at: str
    outcome: str | None = None  # set when archived: "passed", "failed" or "closed"
    ended_on: date | None = None
    archive_note: str = ""

    @property
    def archived(self) -> bool:
        return self.outcome is not None


def create_account(
    conn: sqlite3.Connection,
    name: str,
    *,
    firm: str = "Other",
    account_type: str = "evaluation",
    account_size: Decimal | None = None,
    starting_balance: Decimal = ZERO,
    max_drawdown: Decimal | None = None,
    trailing_type: str | None = None,
    trail_lock_enabled: bool = False,
    trail_lock_level: Decimal | None = None,
    daily_loss_limit: Decimal | None = None,
    profit_target: Decimal | None = None,
) -> Account:
    name = _check_details(name, firm, account_type)
    if trailing_type is not None and trailing_type not in TRAILING_TYPES:
        raise ValueError(f"trailing type must be one of {', '.join(TRAILING_TYPES)}")
    cursor = conn.execute(
        """INSERT INTO accounts (name, firm, account_type, account_size, starting_balance,
               max_drawdown, trailing_type, trail_lock_enabled, trail_lock_level,
               daily_loss_limit, profit_target, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name,
            firm,
            account_type,
            _money_or_none(account_size),
            money_str(starting_balance),
            _money_or_none(max_drawdown),
            trailing_type,
            int(trail_lock_enabled),
            _money_or_none(trail_lock_level),
            _money_or_none(daily_loss_limit),
            _money_or_none(profit_target),
            to_utc_iso(datetime.now(timezone.utc)),
        ),
    )
    return get_account(conn, cursor.lastrowid)


def update_account(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    name: str,
    firm: str,
    account_type: str,
    account_size: Decimal | None,
    starting_balance: Decimal,
) -> Account:
    """Change an account's basic details. (Prop firm rules are edited separately.)

    Changing the account type reclassifies all of the account's trades at once,
    since trades take their type from their account.
    """
    name = _check_details(name, firm, account_type)
    current = get_account(conn, account_id)
    if current.archived and current.outcome not in OUTCOMES_BY_TYPE[account_type]:
        raise ValueError(
            f"a {ACCOUNT_TYPES[account_type].lower()} account can't have the outcome "
            f"'{OUTCOMES[current.outcome]}'; restore it first"
        )
    conn.execute(
        "UPDATE accounts SET name = ?, firm = ?, account_type = ?, account_size = ?, starting_balance = ? "
        "WHERE id = ?",
        (name, firm, account_type, _money_or_none(account_size), money_str(starting_balance), account_id),
    )
    return get_account(conn, account_id)


def archive_account(
    conn: sqlite3.Connection, account_id: int, *, outcome: str, ended_on: date, note: str = ""
) -> Account:
    """Mark an account as finished. Its trades stay; views hide them unless asked."""
    account = get_account(conn, account_id)
    allowed = OUTCOMES_BY_TYPE[account.account_type]
    if outcome not in allowed:
        names = " or ".join(OUTCOMES[o] for o in allowed)
        raise ValueError(f"a {ACCOUNT_TYPES[account.account_type].lower()} account ends as {names}")
    conn.execute(
        "UPDATE accounts SET outcome = ?, ended_on = ?, archive_note = ? WHERE id = ?",
        (outcome, ended_on.isoformat(), note.strip(), account_id),
    )
    return get_account(conn, account_id)


def restore_account(conn: sqlite3.Connection, account_id: int) -> Account:
    """Make an archived account active again."""
    get_account(conn, account_id)
    conn.execute(
        "UPDATE accounts SET outcome = NULL, ended_on = NULL, archive_note = '' WHERE id = ?", (account_id,)
    )
    return get_account(conn, account_id)


def archive_summary(accounts: list[Account]) -> str:
    """How archived accounts ended, e.g.
    "Evaluations: 2 passed, 3 failed (40% pass rate) · Funded: 1 failed".
    The pass rate counts passed and failed evaluations; closed ones don't count."""
    parts = []
    for account_type, label in (("evaluation", "Evaluations"), ("funded", "Funded")):
        counts = {o: sum(1 for a in accounts if a.account_type == account_type and a.outcome == o) for o in OUTCOMES}
        listed = ", ".join(f"{n} {OUTCOMES[o].lower()}" for o, n in counts.items() if n)
        if not listed:
            continue
        decided = counts["passed"] + counts["failed"]
        if account_type == "evaluation" and decided:
            listed += f" ({counts['passed'] / decided:.0%} pass rate)"
        parts.append(f"{label}: {listed}")
    return " · ".join(parts)


def last_trading_day(conn: sqlite3.Connection, account_id: int) -> date | None:
    row = conn.execute("SELECT MAX(trade_date) FROM trades WHERE account_id = ?", (account_id,)).fetchone()
    return date.fromisoformat(row[0]) if row[0] else None


def _check_details(name: str, firm: str, account_type: str) -> str:
    """Validate the basic details and return the cleaned-up name."""
    name = name.strip()
    if not name:
        raise ValueError("account name is required")
    if firm not in FIRMS:
        raise ValueError(f"firm must be one of {', '.join(FIRMS)}")
    if account_type not in ACCOUNT_TYPES:
        raise ValueError(f"account type must be one of {', '.join(ACCOUNT_TYPES)}")
    return name


def get_account(conn: sqlite3.Connection, account_id: int) -> Account:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise LookupError(f"no account with id {account_id}")
    return _account_from_row(row)


def find_account(conn: sqlite3.Connection, name: str) -> Account | None:
    row = conn.execute("SELECT * FROM accounts WHERE name = ?", (name.strip(),)).fetchone()
    return _account_from_row(row) if row else None


def list_accounts(conn: sqlite3.Connection, *, include_archived: bool = True) -> list[Account]:
    where = "" if include_archived else " WHERE outcome IS NULL"
    return [_account_from_row(r) for r in conn.execute(f"SELECT * FROM accounts{where} ORDER BY name")]


def fee_schedule(conn: sqlite3.Connection, account_id: int) -> dict[str, Decimal]:
    """Round-trip fee per contract by symbol root; key '*' is the account default."""
    rows = conn.execute(
        "SELECT symbol_root, fee_per_contract_rt FROM fee_schedules WHERE account_id = ?",
        (account_id,),
    )
    return {r["symbol_root"]: Decimal(r["fee_per_contract_rt"]) for r in rows}


def set_fee(conn: sqlite3.Connection, account_id: int, symbol_root: str, rate: Decimal) -> None:
    """Set the $ per contract round trip for a symbol root ('*' = any other root).

    Stored trades keep their old fees until store.recalculate_trades runs.
    """
    if rate < 0:
        raise ValueError("fee rate cannot be negative")
    root = symbol_root.strip().upper() or DEFAULT_FEE_KEY
    conn.execute(
        """INSERT INTO fee_schedules (account_id, symbol_root, fee_per_contract_rt)
           VALUES (?, ?, ?)
           ON CONFLICT (account_id, symbol_root)
           DO UPDATE SET fee_per_contract_rt = excluded.fee_per_contract_rt""",
        (account_id, root, format(rate, "f")),
    )


def remove_fee(conn: sqlite3.Connection, account_id: int, symbol_root: str) -> None:
    conn.execute(
        "DELETE FROM fee_schedules WHERE account_id = ? AND symbol_root = ?",
        (account_id, symbol_root.strip().upper() or DEFAULT_FEE_KEY),
    )


def _account_from_row(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        name=row["name"],
        firm=row["firm"],
        account_type=row["account_type"],
        account_size=_decimal_or_none(row["account_size"]),
        starting_balance=Decimal(row["starting_balance"]),
        max_drawdown=_decimal_or_none(row["max_drawdown"]),
        trailing_type=row["trailing_type"],
        trail_lock_enabled=bool(row["trail_lock_enabled"]),
        trail_lock_level=_decimal_or_none(row["trail_lock_level"]),
        daily_loss_limit=_decimal_or_none(row["daily_loss_limit"]),
        profit_target=_decimal_or_none(row["profit_target"]),
        created_at=row["created_at"],
        outcome=row["outcome"],
        ended_on=date.fromisoformat(row["ended_on"]) if row["ended_on"] else None,
        archive_note=row["archive_note"],
    )


def _money_or_none(value: Decimal | None) -> str | None:
    return None if value is None else money_str(value)


def _decimal_or_none(text: str | None) -> Decimal | None:
    return None if text is None else parse_decimal(text)
