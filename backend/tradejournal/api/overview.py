"""The Accounts tab: every active account against its verified rules, and the totals row (`GET /api/v1/accounts/overview`).
Pure.

The engine (`compliance.evaluate`) does the work. This module hands it each account's closed trades, payouts and rule set,
turns its answer into the API's shapes, and adds up the totals. An account without verified rules still shows its money:
balance and net P&L are the same arithmetic the engine uses (start + closed trades - what payouts took out).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from .. import compliance
from ..accounts import Account
from ..finance import ZERO
from ..ledger import LedgerEntry
from ..prop_rules import RuleValues
from ..queries import TradeRecord
from .accounts_view import account_out
from .cents import cents
from .schemas.rules import (
    AccountRowOut,
    AccountsOverviewOut,
    AccountsTotalsOut,
    InactivityOut,
    PayoutReadinessOut,
    RuleResultOut,
    SpectrumOut,
)

_PCT = Decimal("0.01")


def payout_records(entries: Iterable[LedgerEntry]) -> list[compliance.PayoutRecord]:
    """The ledger's payouts as the engine reads them (gross is None for one recorded before the gross was kept)."""
    return [
        compliance.PayoutRecord(day=e.entry_date, gross=e.gross_amount, net=e.amount) for e in entries if e.is_payout
    ]


def _money(value: Decimal | None) -> Decimal | None:
    return None if value is None else cents(value)


def _spectrum(s: compliance.Spectrum) -> SpectrumOut:
    return SpectrumOut(
        breach_level=_money(s.breach_level),
        breach_kind=s.breach_kind,
        target_level=_money(s.target_level),
        target_kind=s.target_kind,
        balance=cents(s.balance),
        to_breach=_money(s.to_breach),
        to_target=_money(s.to_target),
        position=s.position,
    )


def _payout(p: compliance.PayoutStatus) -> PayoutReadinessOut:
    return PayoutReadinessOut(
        ready=p.ready,
        gross=_money(p.gross),
        net=_money(p.net),
        split_pct=None if p.split_pct is None else p.split_pct.quantize(_PCT),
        next_number=p.next_number,
        progress=p.progress,
        blockers=list(p.blockers),
    )


def _rule(r: compliance.RuleResult) -> RuleResultOut:
    return RuleResultOut(
        key=r.key, group=r.group, label=r.label, status=r.status, current=r.current, limit=r.limit, message=r.message
    )


def account_row(
    account: Account,
    rules: RuleValues | None,
    trades: Sequence[TradeRecord],
    payouts: Sequence[LedgerEntry],
    now: datetime,
) -> AccountRowOut:
    """One account's row. `trades` and `payouts` are this account's own."""
    records = payout_records(payouts)
    withdrawn_gross = sum((p.withdrawn for p in records), ZERO)
    withdrawn_net = sum((p.net for p in records), ZERO)
    common = {
        "account": account_out(account),
        "phase": account.phase,
        "withdrawn_gross": cents(withdrawn_gross),
        "withdrawn_net": cents(withdrawn_net),
    }
    if rules is None:
        net = sum((t.net_pnl for t in trades), ZERO)
        return AccountRowOut(
            **common,
            program=None,
            rules_set=False,
            balance=cents(account.starting_balance + net - withdrawn_gross),
            net_pnl=cents(net),
            status=None,
            rules=[],
            spectrum=None,
            payout=None,
            consistency_headroom=None,
            inactivity=None,
            floor=None,
            progress="",
            pass_prompt=False,
        )
    facts = compliance.AccountFacts(
        start=account.starting_balance, phase=account.phase, purchase_date=account.purchase_date
    )
    result = compliance.evaluate(rules.values, facts, trades, records, now)
    inactivity = result.inactivity
    return AccountRowOut(
        **common,
        program=rules.values.get("program"),
        rules_set=True,
        balance=cents(result.balance),
        net_pnl=cents(result.net_pnl),
        status=result.status,
        rules=[_rule(r) for r in result.rules],
        spectrum=_spectrum(result.spectrum),
        payout=None if result.payout is None else _payout(result.payout),
        consistency_headroom=_money(result.consistency_headroom),
        inactivity=None
        if inactivity is None
        else InactivityOut(days_left=inactivity.days_left, deadline=inactivity.deadline, message=inactivity.message),
        floor=_money(result.floor),
        progress=result.progress,
        pass_prompt=result.status == "passed"
        and account.account_type == "evaluation"
        and not account.archived
        and account.pass_prompt_dismissed_at is None,
    )


def overview(
    accounts: Sequence[Account],
    rules: Mapping[int, RuleValues],
    trades: Iterable[TradeRecord],
    ledger: Iterable[LedgerEntry],
    now: datetime,
) -> AccountsOverviewOut:
    """A row for every active (not archived) account, in the order given, and the totals over those rows. `rules` maps
    an account id to its verified rule set; `trades` and `ledger` may hold every account's."""
    active = [a for a in accounts if not a.archived]
    trades_by: dict[int, list[TradeRecord]] = {a.id: [] for a in active}
    payouts_by: dict[int, list[LedgerEntry]] = {a.id: [] for a in active}
    for trade in trades:
        if trade.account_id in trades_by:
            trades_by[trade.account_id].append(trade)
    for entry in ledger:
        if entry.is_payout and entry.account_id in payouts_by:
            payouts_by[entry.account_id].append(entry)
    rows = [account_row(a, rules.get(a.id), trades_by[a.id], payouts_by[a.id], now) for a in active]
    ready = [r.payout for r in rows if r.payout is not None and r.payout.ready]
    totals = AccountsTotalsOut(
        balance=cents(sum((r.balance for r in rows), ZERO)),
        net_pnl=cents(sum((r.net_pnl for r in rows), ZERO)),
        withdrawable_gross=cents(sum((p.gross or ZERO for p in ready), ZERO)),
        withdrawable_net=cents(sum((p.net or ZERO for p in ready), ZERO)),
        withdrawn_gross=cents(sum((r.withdrawn_gross for r in rows), ZERO)),
        withdrawn_net=cents(sum((r.withdrawn_net for r in rows), ZERO)),
    )
    return AccountsOverviewOut(accounts=rows, totals=totals)
