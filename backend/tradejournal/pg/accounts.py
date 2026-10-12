"""Trading accounts and their fee schedules, on Postgres.

The port of ``tradejournal/accounts.py`` (spike S2). Validation, constants and the ``Account`` record are shared with
the SQLite module and only the code that talks to the database is rewritten. Every function takes a connection from
``Database.tenant_session``, so row-level security already limits it to one user's rows and no query filters on
``user_id``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg

from ..accounts import (
    ACCOUNT_TYPES,
    FIRMS,  # noqa: F401  (re-exported for callers and tests)
    OUTCOMES,
    OUTCOMES_BY_TYPE,
    TRAILING_TYPES,
    Account,
    _check_details,
    archive_summary,  # noqa: F401  (pure; re-exported)
    check_phase,
    check_purchase_date,
)
from ..finance import DEFAULT_FEE_KEY, ZERO, to_money
from . import usage


def create_account(
    conn: psycopg.Connection,
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
    purchase_date: date | None = None,
    phase: str | None = None,
) -> Account:
    name = _check_details(name, firm, account_type)
    if trailing_type is not None and trailing_type not in TRAILING_TYPES:
        raise ValueError(f"trailing type must be one of {', '.join(TRAILING_TYPES)}")
    stored_phase = check_phase(account_type, phase)
    row = conn.execute(
        """INSERT INTO accounts (name, firm, account_type, account_size, starting_balance,
               max_drawdown, trailing_type, trail_lock_enabled, trail_lock_level,
               daily_loss_limit, profit_target, purchase_date, phase)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (
            name,
            firm,
            account_type,
            _money_or_none(account_size),
            to_money(starting_balance),
            _money_or_none(max_drawdown),
            trailing_type,
            trail_lock_enabled,
            _money_or_none(trail_lock_level),
            _money_or_none(daily_loss_limit),
            _money_or_none(profit_target),
            check_purchase_date(purchase_date),
            stored_phase,
        ),
    ).fetchone()
    usage.refresh(conn)
    return _account_from_row(row)


def update_account(
    conn: psycopg.Connection,
    account_id: int,
    *,
    name: str,
    firm: str,
    account_type: str,
    account_size: Decimal | None,
    starting_balance: Decimal,
    purchase_date: date | None = None,
    phase: str | None = None,
) -> Account:
    """Change an account's basic details. (Prop firm rules are edited separately.)

    Changing the account type reclassifies all of the account's trades at once,
    since trades take their type from their account. The purchase date and phase are set as given (None clears the date,
    and a None phase means the same as the type).
    """
    name = _check_details(name, firm, account_type)
    stored_phase = check_phase(account_type, phase)
    purchased = check_purchase_date(purchase_date)
    current = get_account(conn, account_id)
    if current.archived and current.outcome not in OUTCOMES_BY_TYPE[account_type]:
        raise ValueError(
            f"a {ACCOUNT_TYPES[account_type].lower()} account can't have the outcome "
            f"'{OUTCOMES[current.outcome]}'; restore it first"
        )
    conn.execute(
        "UPDATE accounts SET name = %s, firm = %s, account_type = %s, account_size = %s, starting_balance = %s, "
        "purchase_date = %s, phase = %s WHERE id = %s",
        (
            name,
            firm,
            account_type,
            _money_or_none(account_size),
            to_money(starting_balance),
            purchased,
            stored_phase,
            account_id,
        ),
    )
    return get_account(conn, account_id)


def dismiss_pass_prompt(conn: psycopg.Connection, account_id: int) -> Account:
    """Stop offering "evaluation passed: set up your funded account" for this account (it can still be opened from the
    account's settings)."""
    get_account(conn, account_id)
    conn.execute(
        "UPDATE accounts SET pass_prompt_dismissed_at = now() WHERE id = %s AND pass_prompt_dismissed_at IS NULL",
        (account_id,),
    )
    return get_account(conn, account_id)


def update_rules(
    conn: psycopg.Connection,
    account_id: int,
    *,
    profit_target: Decimal | None,
    max_drawdown: Decimal | None,
    trailing_type: str | None,
    trail_lock_enabled: bool,
    trail_lock_level: Decimal | None,
    daily_loss_limit: Decimal | None,
) -> Account:
    """Set the firm's rules for an account. None means "not tracked". A trailing lock needs the level it locks at,
    and a level without the lock is dropped, so the stored rules never contradict each other."""
    get_account(conn, account_id)
    if trailing_type is not None and trailing_type not in TRAILING_TYPES:
        raise ValueError(f"trailing type must be one of {', '.join(TRAILING_TYPES)}")
    if trail_lock_enabled and trail_lock_level is None:
        raise ValueError("give the level at which the trailing stops, or switch the lock off")
    conn.execute(
        """UPDATE accounts SET profit_target = %s, max_drawdown = %s, trailing_type = %s, trail_lock_enabled = %s,
               trail_lock_level = %s, daily_loss_limit = %s WHERE id = %s""",
        (
            _money_or_none(profit_target),
            _money_or_none(max_drawdown),
            trailing_type,
            trail_lock_enabled,
            _money_or_none(trail_lock_level) if trail_lock_enabled else None,
            _money_or_none(daily_loss_limit),
            account_id,
        ),
    )
    return get_account(conn, account_id)


def archive_account(
    conn: psycopg.Connection, account_id: int, *, outcome: str, ended_on: date, note: str = ""
) -> Account:
    """Mark an account as finished. Its trades stay; views hide them unless asked."""
    account = get_account(conn, account_id)
    allowed = OUTCOMES_BY_TYPE[account.account_type]
    if outcome not in allowed:
        names = " or ".join(OUTCOMES[o] for o in allowed)
        raise ValueError(f"a {ACCOUNT_TYPES[account.account_type].lower()} account ends as {names}")
    conn.execute(
        "UPDATE accounts SET outcome = %s, ended_on = %s, archive_note = %s WHERE id = %s",
        (outcome, ended_on, note.strip(), account_id),
    )
    return get_account(conn, account_id)


def restore_account(conn: psycopg.Connection, account_id: int) -> Account:
    """Make an archived account active again."""
    get_account(conn, account_id)
    conn.execute("UPDATE accounts SET outcome = NULL, ended_on = NULL, archive_note = '' WHERE id = %s", (account_id,))
    return get_account(conn, account_id)


def last_trading_day(conn: psycopg.Connection, account_id: int) -> date | None:
    row = conn.execute("SELECT MAX(trade_date) AS last_day FROM trades WHERE account_id = %s", (account_id,)).fetchone()
    return row["last_day"]


def get_account(conn: psycopg.Connection, account_id: int) -> Account:
    row = conn.execute("SELECT * FROM accounts WHERE id = %s", (account_id,)).fetchone()
    if row is None:
        # Someone else's id looks exactly like a missing one: row-level security hides it.
        raise LookupError(f"no account with id {account_id}")
    return _account_from_row(row)


def find_account(conn: psycopg.Connection, name: str) -> Account | None:
    """The account with this name, ignoring capitalization (the SQLite column was COLLATE NOCASE)."""
    row = conn.execute("SELECT * FROM accounts WHERE lower(name) = lower(%s)", (name.strip(),)).fetchone()
    return _account_from_row(row) if row else None


def list_accounts(conn: psycopg.Connection, *, include_archived: bool = True) -> list[Account]:
    if include_archived:
        rows = conn.execute("SELECT * FROM accounts ORDER BY lower(name)")
    else:
        rows = conn.execute("SELECT * FROM accounts WHERE outcome IS NULL ORDER BY lower(name)")
    return [_account_from_row(r) for r in rows]


def fee_schedule(conn: psycopg.Connection, account_id: int) -> dict[str, Decimal]:
    """Round-trip fee per contract by symbol root; key '*' is the account default."""
    rows = conn.execute(
        "SELECT symbol_root, fee_per_contract_rt FROM fee_schedules WHERE account_id = %s", (account_id,)
    )
    return {r["symbol_root"]: r["fee_per_contract_rt"] for r in rows}


def set_fee(conn: psycopg.Connection, account_id: int, symbol_root: str, rate: Decimal) -> None:
    """Set the $ per contract round trip for a symbol root ('*' = any other root).

    Stored trades keep their old fees until store.recalculate_trades runs.
    """
    if rate < 0:
        raise ValueError("fee rate cannot be negative")
    root = symbol_root.strip().upper() or DEFAULT_FEE_KEY
    conn.execute(
        """INSERT INTO fee_schedules (account_id, symbol_root, fee_per_contract_rt)
           VALUES (%s, %s, %s)
           ON CONFLICT (user_id, account_id, symbol_root)
           DO UPDATE SET fee_per_contract_rt = EXCLUDED.fee_per_contract_rt""",
        (account_id, root, rate),
    )


def remove_fee(conn: psycopg.Connection, account_id: int, symbol_root: str) -> None:
    conn.execute(
        "DELETE FROM fee_schedules WHERE account_id = %s AND symbol_root = %s",
        (account_id, symbol_root.strip().upper() or DEFAULT_FEE_KEY),
    )


def _account_from_row(row: dict) -> Account:
    return Account(
        id=row["id"],
        name=row["name"],
        firm=row["firm"],
        account_type=row["account_type"],
        account_size=row["account_size"],
        starting_balance=row["starting_balance"],
        max_drawdown=row["max_drawdown"],
        trailing_type=row["trailing_type"],
        trail_lock_enabled=row["trail_lock_enabled"],
        trail_lock_level=row["trail_lock_level"],
        daily_loss_limit=row["daily_loss_limit"],
        profit_target=row["profit_target"],
        created_at=row["created_at"].isoformat(),
        outcome=row["outcome"],
        ended_on=row["ended_on"],
        archive_note=row["archive_note"],
        purchase_date=row["purchase_date"],
        phase_stored=row["phase"],
        pass_prompt_dismissed_at=row["pass_prompt_dismissed_at"].isoformat()
        if row["pass_prompt_dismissed_at"]
        else None,
    )


def _money_or_none(value: Decimal | None) -> Decimal | None:
    return None if value is None else to_money(value)
