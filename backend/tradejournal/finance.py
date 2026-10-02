"""All financial math for the journal.

Money and prices are Decimal everywhere; nothing in this module uses float. Parsing
and per-trade P&L come first, then the performance statistics the dashboard shows,
so every number in the app comes from one tested place.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Mapping, Protocol, Sequence

from .models import FillPair

ZERO = Decimal("0")
CENT = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.000001")  # weighted averages can have more decimals than a tick
TICK_QUANTUM = Decimal("0.0001")

# Fee schedule key that applies to any symbol root without its own rate.
DEFAULT_FEE_KEY = "*"

# Dollar value of a one-point move for one contract. Only used to sanity-check the
# CSV's own P&L column; the CSV value is always the one stored.
POINT_VALUES: dict[str, Decimal] = {
    "NQ": Decimal("20"),
    "MNQ": Decimal("2"),
    "ES": Decimal("50"),
    "MES": Decimal("5"),
    "YM": Decimal("5"),
    "MYM": Decimal("0.5"),
    "RTY": Decimal("50"),
    "M2K": Decimal("5"),
    "CL": Decimal("1000"),
    "MCL": Decimal("100"),
    "GC": Decimal("100"),
    "MGC": Decimal("10"),
}

# Optional minus, optional "$", optional minus, optional "(", optional "$", number, optional ")".
_MONEY_RE = re.compile(r"(-?)\$?(-?)(\()?\$?(\d+(?:\.\d+)?|\.\d+)(\))?")


def parse_money(text: str) -> Decimal:
    """Parse an accounting-format amount: "$625.00" -> 625.00, "$(475.00)" -> -475.00.

    Also accepts thousands separators ("$4,400.00"), a leading minus ("-$12.50" or
    "$-12.50"), and bare numbers. Raises ValueError for anything else.
    """
    cleaned = text.strip().replace(",", "").replace(" ", "")
    match = _MONEY_RE.fullmatch(cleaned)
    if not match or bool(match[3]) != bool(match[5]):
        raise ValueError(f"not a money amount: {text!r}")
    negative_markers = bool(match[1]) + bool(match[2]) + bool(match[3])
    if negative_markers > 1:
        raise ValueError(f"not a money amount: {text!r}")
    value = Decimal(match[4])
    return -value if negative_markers and value else value


def parse_decimal(text: str) -> Decimal:
    """Parse a plain decimal number ("16450.25"). Raises ValueError on junk."""
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError(f"not a number: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"not a number: {text!r}")
    return value


def to_money(value: Decimal) -> Decimal:
    """Round to cents, half up."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def plain(value: Decimal) -> str:
    """Canonical string for storage: no exponent, no trailing zeros, no negative zero."""
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def money_str(value: Decimal) -> str:
    """Canonical string for a money amount: always two decimals ("-475.00")."""
    rounded = to_money(value)
    return str(rounded if rounded != 0 else ZERO.quantize(CENT))


def weighted_average_price(legs: Iterable[tuple[int, Decimal]]) -> Decimal:
    """Quantity-weighted average of (qty, price) legs."""
    total_qty = 0
    total_value = ZERO
    for qty, price in legs:
        total_qty += qty
        total_value += price * qty
    if total_qty <= 0:
        raise ValueError("weighted average needs a positive total quantity")
    return (total_value / total_qty).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def fee_rate_for(schedule: Mapping[str, Decimal], symbol_root: str) -> Decimal:
    """Per-contract round-trip fee for a symbol root, falling back to the account default."""
    return schedule.get(symbol_root, schedule.get(DEFAULT_FEE_KEY, ZERO))


def round_trip_fees(total_qty: int, rate_per_contract: Decimal) -> Decimal:
    """Fees for a trade: every contract bought is also sold, so qty round trips."""
    return to_money(rate_per_contract * total_qty)


def expected_pnl(pair: FillPair, point_value: Decimal) -> Decimal:
    """What a fill pair's P&L should be given its prices, quantity and point value."""
    return to_money((pair.sell_price - pair.buy_price) * pair.qty * point_value)


@dataclass(frozen=True)
class TradePnL:
    total_qty: int
    avg_entry: Decimal
    avg_exit: Decimal
    points: Decimal  # per contract, positive = favorable
    ticks: Decimal  # per contract
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal


def trade_pnl(pairs: Sequence[FillPair], tick_size: Decimal, fee_rate: Decimal) -> TradePnL:
    """Prices, points, ticks and P&L for the fill pairs of one trade.

    Gross P&L is the sum of the CSV's P&L column. Points and ticks are the
    quantity-weighted move per contract, computed from exact sums so rounding the
    average prices never leaks into them.
    """
    if not pairs:
        raise ValueError("a trade needs at least one fill pair")
    directions = {p.direction for p in pairs}
    if len(directions) != 1:
        raise ValueError("fill pairs of one trade must share a direction")
    sign = 1 if directions.pop() == "long" else -1

    total_qty = sum(p.qty for p in pairs)
    favorable_move = sum(((p.exit_price - p.entry_price) * p.qty for p in pairs), ZERO) * sign
    points_exact = favorable_move / total_qty
    gross = sum((p.pnl for p in pairs), ZERO)
    fees = round_trip_fees(total_qty, fee_rate)
    return TradePnL(
        total_qty=total_qty,
        avg_entry=weighted_average_price((p.qty, p.entry_price) for p in pairs),
        avg_exit=weighted_average_price((p.qty, p.exit_price) for p in pairs),
        points=points_exact.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP),
        ticks=(points_exact / tick_size).quantize(TICK_QUANTUM, rounding=ROUND_HALF_UP),
        gross_pnl=to_money(gross),
        fees=fees,
        net_pnl=to_money(gross - fees),
    )


# ---------------------------------------------------------------------------
# Performance statistics. Every P&L figure is net of fees unless named gross.

HUNDRED = Decimal("100")
SCORE_QUANTUM = Decimal("0.1")


class TradeResult(Protocol):
    trade_date: date
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class DailyResult:
    day: date
    net_pnl: Decimal
    trade_count: int


@dataclass(frozen=True)
class EquityPoint:
    equity: Decimal  # cumulative net P&L after this trade
    drawdown: Decimal  # equity minus its running peak (the peak starts at 0): zero or negative


@dataclass(frozen=True)
class Summary:
    trade_count: int
    win_count: int  # net P&L > 0
    loss_count: int  # net P&L < 0
    breakeven_count: int  # net P&L == 0
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    total_won: Decimal  # sum of winning trades
    total_lost: Decimal  # sum of losing trades (negative)
    win_rate: Decimal | None  # wins / (wins + losses); breakeven trades don't count either way
    profit_factor: Decimal | None  # total won / |total lost|; None when there are no losses
    avg_win: Decimal | None
    avg_loss: Decimal | None  # negative
    win_loss_ratio: Decimal | None  # avg win / |avg loss|
    expectancy: Decimal | None  # net P&L per trade
    largest_win: Decimal | None
    largest_loss: Decimal | None  # negative
    trading_days: int
    winning_days: int
    losing_days: int
    day_win_rate: Decimal | None  # winning days / (winning + losing days)
    max_drawdown: Decimal  # deepest peak-to-trough drop of cumulative net P&L, as a positive amount


def daily_results(trades: Iterable[TradeResult]) -> list[DailyResult]:
    """Net P&L and trade count per trading day, oldest first."""
    totals: dict[date, Decimal] = defaultdict(lambda: ZERO)
    counts: dict[date, int] = defaultdict(int)
    for trade in trades:
        totals[trade.trade_date] += trade.net_pnl
        counts[trade.trade_date] += 1
    return [DailyResult(day, totals[day], counts[day]) for day in sorted(totals)]


def equity_curve(net_pnls: Iterable[Decimal]) -> list[EquityPoint]:
    """Cumulative net P&L and drawdown after each trade, in the order given."""
    points = []
    equity = peak = ZERO
    for pnl in net_pnls:
        equity += pnl
        peak = max(peak, equity)
        points.append(EquityPoint(equity=equity, drawdown=equity - peak))
    return points


def max_drawdown(net_pnls: Iterable[Decimal]) -> Decimal:
    return -min((point.drawdown for point in equity_curve(net_pnls)), default=ZERO)


def summarize(trades: Sequence[TradeResult]) -> Summary:
    """Headline statistics for trades given in chronological order (drawdown depends on it)."""
    nets = [t.net_pnl for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    total_won = sum(wins, ZERO)
    total_lost = sum(losses, ZERO)
    net = sum(nets, ZERO)
    avg_win = total_won / len(wins) if wins else None
    avg_loss = total_lost / len(losses) if losses else None
    days = daily_results(trades)
    winning_days = sum(1 for d in days if d.net_pnl > 0)
    losing_days = sum(1 for d in days if d.net_pnl < 0)
    return Summary(
        trade_count=len(trades),
        win_count=len(wins),
        loss_count=len(losses),
        breakeven_count=len(nets) - len(wins) - len(losses),
        gross_pnl=sum((t.gross_pnl for t in trades), ZERO),
        fees=sum((t.fees for t in trades), ZERO),
        net_pnl=net,
        total_won=total_won,
        total_lost=total_lost,
        win_rate=_ratio(Decimal(len(wins)), Decimal(len(wins) + len(losses))),
        profit_factor=_ratio(total_won, -total_lost),
        avg_win=avg_win,
        avg_loss=avg_loss,
        win_loss_ratio=_ratio(avg_win, -avg_loss) if avg_win is not None and avg_loss is not None else None,
        expectancy=net / len(trades) if trades else None,
        largest_win=max(wins) if wins else None,
        largest_loss=min(losses) if losses else None,
        trading_days=len(days),
        winning_days=winning_days,
        losing_days=losing_days,
        day_win_rate=_ratio(Decimal(winning_days), Decimal(winning_days + losing_days)),
        max_drawdown=max_drawdown(nets),
    )


@dataclass(frozen=True)
class ScoreAxis:
    name: str
    value: Decimal | None  # the measured metric (a ratio or a fraction), None when undefined
    score: Decimal  # 0-100
    rule: str  # how the value maps to the score


@dataclass(frozen=True)
class PerformanceScore:
    axes: list[ScoreAxis]
    overall: Decimal  # plain average of the axis scores


SCORE_RULES = {
    "Win rate": "Winning trades ÷ (winning + losing trades). 60% or higher scores 100, scaling down to 0 at 0%.",
    "Profit factor": "Total won ÷ total lost. 1.0 or lower scores 0; 2.5 or higher scores 100. No losing trades scores 100.",
    "Avg win/loss": "Average win ÷ average loss. 2.5 or higher scores 100, scaling down to 0 at 0.",
    "Consistency": (
        "Share of winning-day profit that came from your single best day. 20% or less scores 100; "
        "100% (one day made it all) scores 0."
    ),
    "Max drawdown": (
        "Deepest drop in cumulative net P&L as a share of total won. 0% scores 100; "
        "giving back 100% or more scores 0."
    ),
    "Recovery factor": "Net P&L ÷ max drawdown. 3 or higher scores 100; 0 or lower scores 0.",
}


def performance_score(summary: Summary, days: Sequence[DailyResult]) -> PerformanceScore | None:
    """Six 0-100 scores for the radar chart. None when there are no trades."""
    if summary.trade_count == 0:
        return None
    has_wins = summary.win_count > 0
    has_losses = summary.loss_count > 0

    best_day = max((d.net_pnl for d in days if d.net_pnl > 0), default=None)
    winning_day_total = sum((d.net_pnl for d in days if d.net_pnl > 0), ZERO)
    best_day_share = best_day / winning_day_total if best_day is not None else None
    drawdown_share = _ratio(summary.max_drawdown, summary.total_won)
    recovery = _ratio(summary.net_pnl, summary.max_drawdown)

    def when_undefined(fallback: bool) -> Decimal:
        return HUNDRED if fallback else ZERO

    scores = {
        "Win rate": (
            summary.win_rate,
            _scale(summary.win_rate, ZERO, Decimal("0.6")) if summary.win_rate is not None else ZERO,
        ),
        "Profit factor": (
            summary.profit_factor,
            _scale(summary.profit_factor, Decimal("1"), Decimal("2.5"))
            if summary.profit_factor is not None
            else when_undefined(has_wins and not has_losses),
        ),
        "Avg win/loss": (
            summary.win_loss_ratio,
            _scale(summary.win_loss_ratio, ZERO, Decimal("2.5"))
            if summary.win_loss_ratio is not None
            else when_undefined(has_wins and not has_losses),
        ),
        "Consistency": (
            best_day_share,
            _scale(1 - best_day_share, ZERO, Decimal("0.8")) if best_day_share is not None else ZERO,
        ),
        "Max drawdown": (
            drawdown_share,
            _scale(1 - drawdown_share, ZERO, Decimal("1")) if drawdown_share is not None else ZERO,
        ),
        "Recovery factor": (
            recovery,
            _scale(recovery, ZERO, Decimal("3"))
            if recovery is not None
            else when_undefined(summary.net_pnl > 0),
        ),
    }
    axes = [ScoreAxis(name, value, score, SCORE_RULES[name]) for name, (value, score) in scores.items()]
    overall = (sum((a.score for a in axes), ZERO) / len(axes)).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
    return PerformanceScore(axes=axes, overall=overall)


# ---------------------------------------------------------------------------
# Prop business: what accounts cost versus what they paid out.


class LedgerItem(Protocol):
    amount: Decimal  # positive

    @property
    def is_payout(self) -> bool: ...


@dataclass(frozen=True)
class BusinessTotals:
    costs: Decimal  # fees, resets, subscriptions: money spent
    payouts: Decimal  # money received

    @property
    def net(self) -> Decimal:
        return self.payouts - self.costs


def business_totals(items: Iterable[LedgerItem]) -> BusinessTotals:
    costs = payouts = ZERO
    for item in items:
        if item.is_payout:
            payouts += item.amount
        else:
            costs += item.amount
    return BusinessTotals(costs=costs, payouts=payouts)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator else None


def _scale(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    """Map value linearly so low -> 0 and high -> 100, clamped to 0..100."""
    scaled = (value - low) / (high - low) * HUNDRED
    return min(HUNDRED, max(ZERO, scaled)).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
