"""Changing accounts, their fees and their ledger (doc 07 section 2). Mirrors `lib/data/model.ts`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, ConfigDict, Field

from ...accounts import FIRMS
from ...ledger import KINDS
from .base import ApiModel
from .journal import AccountOut, AccountType, Outcome, Phase, RowId, TrailingType
from .money import Money

# Amounts are cents in a numeric(20, 2) column; this is a sanity bound well inside it, so a typo is a refusal in words
# and a number the database cannot hold is never sent to it.
MAX_AMOUNT = Decimal("999999999999.99")
MAX_FEE_RATE = Decimal("10000")


def _between(low: Decimal, high: Decimal, *, low_open: bool = False):
    def check(value: Decimal) -> Decimal:
        if value > high or value < low or (low_open and value == low):
            raise ValueError(f"must be {'more than' if low_open else 'at least'} {low} and at most {high}")
        return value

    return check


Amount = Annotated[Money, AfterValidator(_between(Decimal("0"), MAX_AMOUNT))]  # 0 to the bound
PositiveAmount = Annotated[Money, AfterValidator(_between(Decimal("0"), MAX_AMOUNT, low_open=True))]
FeeRate = Annotated[Money, AfterValidator(_between(Decimal("0"), MAX_FEE_RATE))]
Percent = Annotated[Money, AfterValidator(_between(Decimal("0"), Decimal("100"), low_open=True))]  # (0, 100]

FirmName = Literal[*FIRMS]  # the firms the app knows (accounts.FIRMS); the front end offers the same list
LedgerKind = Literal[*KINDS]
SymbolRoot = Annotated[
    str, Field(min_length=1, max_length=12, pattern=r"^[A-Za-z0-9*]+$")
]  # "NQ", "6E", "*" (any other)


class _In(ApiModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)  # no silent typos


class AccountDetailsIn(_In):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    firm: FirmName
    account_type: AccountType
    account_size: Amount | None
    starting_balance: Amount
    purchase_date: date | None  # null clears it (an account from before purchase dates were kept)
    phase: Phase | None  # null: the same as the type; a funded account can be live


class NewAccountIn(AccountDetailsIn):
    """A new account needs the day it was bought: it decides which version of the firm's rules applies."""

    purchase_date: date


class AccountRulesIn(_In):
    """A rule left null is not tracked for the account."""

    profit_target: Amount | None
    max_drawdown: Amount | None
    trailing_type: TrailingType | None
    trail_lock_enabled: bool
    trail_lock_level: Amount | None
    daily_loss_limit: Amount | None


class ArchiveIn(_In):
    outcome: Outcome
    ended_on: date
    note: Annotated[str, Field(max_length=500)]


class FeeIn(_In):
    rate: FeeRate  # dollars per contract, round trip


class NewLedgerEntryIn(_In):
    kind: LedgerKind
    amount: PositiveAmount  # always positive: the kind says which way the money went
    entry_date: date
    note: Annotated[str, Field(max_length=500)]


class LedgerEntryOut(ApiModel):
    id: RowId
    account_id: RowId
    kind: LedgerKind
    amount: Money  # for a payout, the net: what the trader received before any fee
    entry_date: date
    note: str
    # A payout's own facts; null on costs, and on payouts recorded before they were kept.
    payout_number: int | None  # 1, 2, 3... by date
    gross_amount: Money | None  # what the firm paid out of the account
    profit_split_pct: Money | None  # the trader's share (amount = gross x split)
    payout_fee: Money | None  # kept by the firm or the bank; counted as a cost


class AccountProgressOut(ApiModel):
    """How an account is doing against its rules, worked out from its closed trades."""

    net_pnl: Money
    trade_count: int
    target_progress: float | None  # 0..1 of the profit target; null when there is no target
    drawdown_left: Money | None  # what is left of the allowed drawdown; null when there is no limit
    last_trading_day: date | None  # the day of its latest trade; null when it has none


class AccountOverviewOut(ApiModel):
    account: AccountOut
    progress: AccountProgressOut


class BusinessTotalsOut(ApiModel):
    """Across every account, archived ones included: is the prop business itself profitable?"""

    evaluations_passed: int
    evaluations_failed: int
    pass_rate: float | None  # passed / (passed + failed) over evaluations; closed ones do not count
    costs: Money
    payouts: Money
    net: Money  # payouts minus costs
    accounts_without_cost: list[str]  # names of accounts with no cost recorded
