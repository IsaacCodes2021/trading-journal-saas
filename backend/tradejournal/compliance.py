"""Where a prop account stands against its verified rules, and whether it can ask for a payout. Pure.

Input: the account's starting balance, phase and purchase date, its rule set (`prop_rules.RuleValues`), its closed trades
(Tradovate Performance CSVs) and its payouts. Output (`Evaluation`): a status (`ok`, `at_risk`, `breached`, `passed`,
`payout_ready`), one result per rule (`pass`, `fail`, `n/a` or `not_verifiable`, with the current value, the limit and a
sentence), the breach-to-payout spectrum, payout readiness and the amount that can be withdrawn, the consistency headroom
and the inactivity countdown.

**Closed trades only.** A closed-trade export has the realized balance after every trade, never the open profit or loss
inside one. Realized equity is always at or above the true low, so a breach seen in closed trades is certain, and the
absence of one is not proof. A rule that needs open equity (an intraday trailing drawdown, a drawdown or daily loss limit
enforced on live equity, open-P&L rules) is therefore never a `pass`: it is `fail` when the closed trades already prove a
breach and `not_verifiable` otherwise.

**When a rule is unclear, the conservative reading is used** (the one that warns earlier or pays out less), and every
such choice is listed in `docs/prop-rules/PLAN.md`. The descriptive text columns (cap schedule, cadence, inactivity, ...)
are read by the small interpreters below; text they do not recognise makes that rule `not_verifiable`, never a pass.

Days are the firm's trading days (`timeutil.firm_session`), and a trade belongs to the day it closed in.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal, Protocol

from .finance import CENT, ZERO, to_money
from .prop_rules import NO_CONSISTENCY
from .timeutil import TradingSession, firm_session, trading_date, zone

RuleStatus = Literal["pass", "fail", "n/a", "not_verifiable"]
AccountStatus = Literal["ok", "at_risk", "breached", "passed", "payout_ready"]
NOT_VERIFIABLE_LABEL = "Not verifiable from imports"

HUNDRED = Decimal("100")
AT_RISK_SHARE = Decimal("0.25")  # within a quarter of the max loss (or of today's daily limit) of the breach level
MICROS = frozenset(
    {"MNQ", "MES", "MYM", "M2K", "MCL", "MGC", "M6E", "M6B", "M6A", "MBT", "MET", "SIL", "MHG", "QI", "QO"}
)
NEW_YORK = "America/New_York"
FLAT_GRACE = timedelta(minutes=1)


class TradeLike(Protocol):
    entry_at: datetime
    exit_at: datetime
    net_pnl: Decimal
    symbol_root: str
    max_position: int


@dataclass(frozen=True)
class PayoutRecord:
    day: date
    gross: Decimal | None  # what left the account; None for a payout recorded before gross was kept
    net: Decimal  # what the trader received before any fee (the ledger amount)

    @property
    def withdrawn(self) -> Decimal:
        """What the payout took out of the balance. Without a gross, the net is all that is known."""
        return self.gross if self.gross is not None else self.net


@dataclass(frozen=True)
class AccountFacts:
    start: Decimal  # the starting balance
    phase: str  # evaluation, funded or live
    purchase_date: date | None = None


@dataclass(frozen=True)
class RuleResult:
    key: str
    group: str
    label: str
    status: RuleStatus
    current: str | None = None
    limit: str | None = None
    message: str = ""


@dataclass(frozen=True)
class Spectrum:
    """One bar from breach to goal: the left end is the drawdown floor or today's daily loss limit, whichever is closer;
    the right end is the profit target (evaluation) or the balance at which a payout can be asked for (funded)."""

    breach_level: Decimal | None
    breach_kind: Literal["drawdown", "daily_loss"] | None
    target_level: Decimal | None
    target_kind: Literal["profit_target", "payout"] | None
    balance: Decimal
    to_breach: Decimal | None  # dollars above the breach level
    to_target: Decimal | None  # dollars still to go (0 once reached)
    position: float | None  # 0 at the breach level, 1 at the target; None without both ends


@dataclass(frozen=True)
class PayoutStatus:
    ready: bool
    gross: Decimal | None  # what can be withdrawn now, when ready
    net: Decimal | None  # gross x split
    split_pct: Decimal | None
    next_number: int
    lifetime_gross: Decimal
    lifetime_net: Decimal
    progress: str  # "3/5 qualifying days · best day 38% of 50% max · $640 to buffer"
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class InactivityStatus:
    days_left: int | None
    deadline: date | None
    message: str


@dataclass(frozen=True)
class Evaluation:
    status: AccountStatus
    balance: Decimal
    net_pnl: Decimal
    rules: tuple[RuleResult, ...]
    spectrum: Spectrum
    payout: PayoutStatus | None  # funded and live accounts
    consistency_headroom: Decimal | None
    inactivity: InactivityStatus | None
    floor: Decimal | None
    effective_target: Decimal | None
    progress: str  # funded: the payout progress; evaluation: how far from passing


# ---- formatting -----------------------------------------------------------------------------------------------------


def money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(to_money(value)):,.2f}"


def pct(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.1"))
    return f"{rounded.normalize():f}%" if rounded != rounded.to_integral() else f"{int(rounded)}%"


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# ---- reading the descriptive columns ------------------------------------------------------------------------------

LockMode = Literal["unchanged", "lock_start", "lock_start_plus_100", "reset_zero", "freeze"]


def lock_level(text: str | None, start: Decimal, profit_target: Decimal | None) -> Decimal | None:
    """The balance at which a trailing drawdown stops trailing, or None when it never stops (or the text is not one this
    reads: a floor that keeps trailing is the conservative reading, since it can only be higher)."""
    if not text:
        return None
    value = text.strip().lower()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return Decimal(value)
    if value.startswith("none"):
        return None
    if value.startswith("start_balance_plus_100") or "drawdown + $100" in value or value.startswith("locks at $100"):
        return start + 100
    if value.startswith("start_balance") or value.startswith("stops at $0"):
        return start
    if value.startswith("profit_target_balance"):
        return start + profit_target if profit_target is not None else None
    return None


def after_payout_mode(text: str | None) -> LockMode:
    """What the first payout does to the drawdown floor. Empty or unreadable text is "unchanged": the floor never moves
    down and keeps trailing the balance plus what was paid out, the reading that leaves the least room."""
    value = (text or "").strip().lower()
    if value.startswith("resets_to_zero"):
        return "reset_zero"
    if value.startswith("locks_at_start_plus_100") or "start+100" in value or value.startswith("locks at $100"):
        return "lock_start_plus_100"
    if value.startswith("locks_at_start"):
        return "lock_start"
    if "static" in value:
        return "freeze"
    return "unchanged"


@dataclass(frozen=True)
class Cap:
    kind: Literal["amount", "uncapped", "unknown", "absent"]
    amount: Decimal | None = None
    pct: tuple[Decimal, str] | None = (
        None  # a percentage cap written into the schedule ("50% of profit capped at 1500")
    )


def cap_for(schedule: str | None, payout_number: int) -> Cap:
    """The most a payout with this number may be, from `payout_cap_schedule` ("1:1500|2:2000|4+:3000", "all:2000",
    "1-3:1500|4+:uncapped", "uncapped")."""
    if not schedule or not schedule.strip():
        return Cap("absent")
    text = schedule.strip().lower()
    if text.startswith(("uncapped", "no max", "no maximum", "no dollar cap")):
        return Cap("uncapped")
    capped = re.match(r"(\d+(?:\.\d+)?)% of (profit|balance) capped at \$?(\d+(?:\.\d+)?)", text)
    if capped:
        return Cap("amount", Decimal(capped[3]), (Decimal(capped[1]), capped[2]))
    standard = re.match(r"standard \$?(\d+(?:\.\d+)?)", text)
    if standard:
        return Cap("amount", Decimal(standard[1]))
    head = re.split(r"\s*[;(]|\s+(?=up to)", text, maxsplit=1)[0].strip()
    for entry in head.split("|"):
        if ":" not in entry:
            return Cap("unknown")
        spec, _, raw = (part.strip() for part in entry.partition(":"))
        if _covers(spec, payout_number):
            amount = raw.split()[0] if raw else ""
            if amount == "uncapped":
                return Cap("uncapped")
            if re.fullmatch(r"\d+(?:\.\d+)?", amount):
                return Cap("amount", Decimal(amount))
            return Cap("unknown")
    return Cap("unknown")


def _covers(spec: str, number: int) -> bool:
    if spec in ("all", "per cycle"):
        return True
    if re.fullmatch(r"\d+", spec):
        return int(spec) == number
    if re.fullmatch(r"\d+\+", spec):
        return number >= int(spec[:-1])
    span = re.fullmatch(r"(\d+)-(\d+)", spec)
    return bool(span) and int(span[1]) <= number <= int(span[2])


def percentage_cap(text: str | None) -> tuple[Decimal, str] | None:
    """`payout_pct_of_profit_cap` as (percent, of what): "50% of profit" is (50, "profit"); "of what" is profit (balance
    above the start), cycle profit (made since the last payout) or balance."""
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)% of (cycle profit|profit|balance)", text.lower())
    return (Decimal(match[1]), match[2]) if match else None


def split_share(text: str | None) -> Decimal | None:
    """The trader's share of a payout, in percent, from `profit_split` ("90/10" is 90). A tiered or optional split gives its
    lowest share (the payout it promises is the smallest); "up to" says nothing certain, so it gives none."""
    if not text:
        return None
    value = text.lower()
    if value.strip().startswith("up to"):
        return None
    value = re.sub(r"\d+(?:\.\d+)?% processing fee", "", value)
    shares = [
        Decimal(a) for a, b in re.findall(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", value) if Decimal(a) + Decimal(b) == 100
    ]
    shares += [Decimal(n) for n in re.findall(r"(\d+(?:\.\d+)?)%", value)]
    shares = [s for s in shares if 0 < s <= 100]
    return min(shares) if shares else None


def payout_limit_count(text: str | None) -> int | None:
    """How many payouts the account allows before it closes or moves to live, when the text starts with the number
    ("6 (PA closes after 6th)")."""
    match = re.match(r"\s*(\d+)\b", text or "")
    return int(match[1]) if match else None


@dataclass(frozen=True)
class Cadence:
    recognised: bool
    calendar_gap: int | None = None  # days since the last payout
    trading_gap: int | None = None  # trading days since the last payout
    per_month: int | None = None
    once_a_day: bool = False
    reading: str = ""


def cadence(text: str | None) -> Cadence | None:
    """`payout_frequency` as the limits it sets. None when it is empty; `recognised` is False when nothing in it is a
    limit this can read."""
    if not text or not text.strip():
        return None
    value = text.lower()
    calendar_gap = trading_gap = per_month = None
    once_a_day = False
    readings: list[str] = []
    gap = re.search(r"every (\d+) (?:calendar )?days\b", value) or re.search(r"(\d+) days between payouts", value)
    if gap:
        calendar_gap = int(gap[1])
        readings.append(f"{gap[1]} days between payouts")
    trading = (
        re.search(r"every (\d+) trading days", value)
        or re.search(r"(\d+) trading days between", value)
        or re.search(r">=\s*(\d+) trading days", value)
    )
    if trading:
        trading_gap = int(trading[1])
        readings.append(f"{trading[1]} trading days since the last payout")
    if calendar_gap is None and re.search(r"up to weekly|\bweekly\b", value):
        calendar_gap = 7
        readings.append("at most one payout a week")
    month = re.search(r"up to (\d+) requests per month", value)
    if month:
        per_month = int(month[1])
        readings.append(f"at most {month[1]} a month")
    if re.search(
        r"\bdaily\b|on-demand|anytime|whenever eligible|any day|any trading day|1 request per day|same-day|requests before",
        value,
    ):
        once_a_day = True
        readings.append("one request a day")
    cycle = re.search(
        r"every \d+ (?:new )?(?:winning|qualifying|profitable|benchmark) days|\d+[- ]winning[- ]day cycle"
        r"|per (?:\w+ )?cycle|each completed cycle|qualifying days met|winning days|goal met|benchmark days",
        value,
    )
    if cycle:
        readings.append("each completed cycle (the qualifying-day and goal rules check it)")
    return Cadence(
        recognised=bool(readings),
        calendar_gap=calendar_gap,
        trading_gap=trading_gap,
        per_month=per_month,
        once_a_day=once_a_day,
        reading="; ".join(readings),
    )


@dataclass(frozen=True)
class Inactivity:
    kind: Literal["rolling", "every", "window", "none", "unreadable"]
    window: int = 0  # days
    count: int = 1  # qualifying days needed in the window
    min_profit: Decimal | None = None  # what a qualifying day (or trade) must make
    per_trade: bool = False  # the minimum applies to one trade, not to a day
    trading_days: bool = False  # the window counts weekdays, not calendar days
    reading: str = ""


def inactivity(text: str | None) -> Inactivity | None:
    """`inactivity_rule` as a rule this can count. None when it is empty."""
    if not text or not text.strip():
        return None
    value = text.lower()
    rolling = re.search(
        r"(\d+) (?:trading )?days? (?:with )?\$(\d+(?:\.\d+)?)\+ (?:net profit )?(?:in every|per) rolling (\d+)[- ]days?",
        value,
    )
    if rolling:
        count, profit, window = int(rolling[1]), Decimal(rolling[2]), int(rolling[3])
        return Inactivity(
            "rolling",
            window,
            count,
            profit,
            reading=f"{plural(count, 'day')} of {money(profit)}+ in every rolling {window} days",
        )
    per_trade = re.search(r"no trade with net p&l of at least \$(\d+(?:\.\d+)?) in (\d+) calendar days", value)
    if per_trade:
        profit, window = Decimal(per_trade[1]), int(per_trade[2])
        return Inactivity(
            "every",
            window,
            min_profit=profit,
            per_trade=True,
            reading=f"a trade making {money(profit)}+ at least every {window} days",
        )
    if "no time limit" in value:
        return Inactivity("none", reading="no time limit")
    window = re.search(r"(\d+)[- ](?:calendar )?days? (?:evaluation )?(?:access )?window", value) or re.search(
        r"(\d+)[- ]day (?:evaluation|access) window", value
    )
    if window:
        days = int(window[1])
        return Inactivity("window", days, reading=f"{days} days from the purchase date")
    trading = re.search(r"every (\d+) trading days", value)
    if trading:
        days = int(trading[1])
        return Inactivity("every", days, trading_days=True, reading=f"a trade at least every {days} trading days")
    every = (
        re.search(r"(?:one|1) (?:trade|round trip) every (\d+) (?:calendar )?days", value)
        or re.search(r"after (\d+) days of inactivity", value)
        or re.fullmatch(r"\s*(\d+) (?:calendar )?days\s*", value)
    )
    if every:
        days = int(every[1])
        return Inactivity("every", days, reading=f"a trade at least every {days} days")
    if re.search(r"(?:one|1) (?:trade|round trip) per (?:calendar |mon-fri )?week", value):
        return Inactivity("every", 7, reading="a trade at least every 7 days (a calendar week, counted as any 7 days)")
    return Inactivity("unreadable")


@dataclass(frozen=True)
class RequestDays:
    kind: Literal["any", "weekdays", "no_trade_today", "unreadable"]


def request_days(text: str | None) -> RequestDays | None:
    if not text or not text.strip():
        return None
    value = text.lower()
    if "without a new trade" in value or "not on a day a new trade" in value:
        return RequestDays("no_trade_today")
    if value.startswith("any"):
        return RequestDays("any")
    if "business days" in value or "mon-fri" in value or re.search(r"sun .*-\s*fri", value):
        return RequestDays("weekdays")
    return RequestDays("unreadable")


def flat_time(text: str | None) -> tuple[time, str] | None:
    """The first clock time in `flat_by_time` and its zone: "4:45 PM EST ..." is (16:45, New York)."""
    if not text:
        return None
    match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)\s*(et|est|edt|ct|cst|cdt)\b", text.lower())
    if not match:
        return None
    hour = int(match[1]) % 12 + (12 if match[3] == "pm" else 0)
    where = NEW_YORK if match[4].startswith("e") else "America/Chicago"
    return time(hour, int(match[2])), where


# ---- the engine ---------------------------------------------------------------------------------------------------


@dataclass
class _Day:
    day: date
    net: Decimal = ZERO
    trades: list[Any] = field(default_factory=list)


def _days(trades: Iterable[TradeLike], session: TradingSession) -> dict[date, _Day]:
    days: dict[date, _Day] = {}
    for trade in sorted(trades, key=lambda t: (t.exit_at, t.entry_at)):
        day = trading_date(trade.exit_at, session)
        entry = days.setdefault(day, _Day(day))
        entry.net += trade.net_pnl
        entry.trades.append(trade)
    return days


def _value(rules: Mapping[str, Any], name: str) -> Any:
    return rules.get(name)


def evaluate(
    rules: Mapping[str, Any],
    account: AccountFacts,
    trades: Sequence[TradeLike],
    payouts: Sequence[PayoutRecord],
    now: datetime,
) -> Evaluation:
    """Everything the Accounts tab shows for one account with verified rules. `rules` maps every field of
    `prop_rules.FIELDS` to its typed value (None when empty); `now` is an aware datetime."""
    return _Engine(rules, account, trades, payouts, now).run()


class _Engine:
    def __init__(
        self,
        rules: Mapping[str, Any],
        account: AccountFacts,
        trades: Sequence[TradeLike],
        payouts: Sequence[PayoutRecord],
        now: datetime,
    ) -> None:
        self.r = rules
        self.account = account
        self.start = account.start
        self.phase = account.phase
        self.funded = account.phase in ("funded", "live")
        self.session = firm_session(rules.get("firm"))
        self.now = now.astimezone(UTC)
        self.today = trading_date(self.now, self.session)
        self.calendar_today = self.now.astimezone(zone(NEW_YORK)).date()
        self.days = _days(trades, self.session)
        self.payouts = sorted(payouts, key=lambda p: p.day)
        self.last_payout = self.payouts[-1].day if self.payouts else None
        self.net_pnl = sum((d.net for d in self.days.values()), ZERO)
        self.withdrawn = sum((p.withdrawn for p in self.payouts), ZERO)
        self.balance = self.start + self.net_pnl - self.withdrawn
        # The cycle: the days after the last payout (a payout's own day stays in the cycle before it: the fewer days
        # count, the safer). An evaluation has one cycle, its whole life.
        self.cycle = [d for d in self._ordered() if self.last_payout is None or d.day > self.last_payout]
        self.cycle_profit = sum((d.net for d in self.cycle), ZERO)
        self.results: dict[str, RuleResult] = {}
        self.breached = False
        self.at_risk: list[str] = []
        self.floor: Decimal | None = None
        self.effective_target: Decimal | None = None
        self.headroom: Decimal | None = None

    def _ordered(self) -> list[_Day]:
        return [self.days[d] for d in sorted(self.days)]

    def add(self, key: str, group: str, label: str, status: RuleStatus, **details: Any) -> RuleResult:
        result = RuleResult(key, group, label, status, **details)
        self.results[key] = result
        return result

    # -- the rules, one method each ------------------------------------------------------------------------------

    def run(self) -> Evaluation:
        self.drawdown()
        dll_floor = self.daily_loss()
        self.contracts()
        self.min_trading_days()
        self.consistency()
        self.qualifying_days()
        self.profit_target()
        inactivity_status = self.inactivity()
        payout = self.payout() if self.funded else None
        self.restrictions()
        spectrum = self.spectrum(dll_floor, payout)
        status = self.status(payout)
        return Evaluation(
            status=status,
            balance=self.balance,
            net_pnl=self.net_pnl,
            rules=tuple(self.results.values()),
            spectrum=spectrum,
            payout=payout,
            consistency_headroom=self.headroom,
            inactivity=inactivity_status,
            floor=self.floor,
            effective_target=self.effective_target,
            progress=payout.progress if payout is not None else self._evaluation_progress(),
        )

    def _evaluation_progress(self) -> str:
        parts = []
        target = self.results.get("profit_target")
        if target and target.status != "n/a" and self.effective_target is not None:
            short = self.start + self.effective_target - self.balance
            parts.append(f"{money(short)} to target" if short > 0 else "target reached")
        days = self.results.get("min_trading_days")
        if days and days.status != "n/a":
            parts.append(f"{days.current}/{days.limit} trading days")
        parts.extend(self._consistency_progress())
        return " · ".join(parts)

    def _consistency_progress(self) -> list[str]:
        numbers = self._consistency_numbers()
        if numbers is None or numbers[1] <= 0 or not numbers[2]:
            return []
        best, against, _ = numbers
        return [f"best day {pct(best / against * HUNDRED)} of {pct(_value(self.r, 'consistency_pct'))} max"]

    def drawdown(self) -> None:
        kind = _value(self.r, "drawdown_type")
        max_loss = _value(self.r, "max_loss_usd")
        label = "Max loss (drawdown)"
        if not kind or max_loss is None:
            self.add("drawdown", "Drawdown", label, "n/a", message="No drawdown rule is set.")
            return
        initial = self.start - max_loss
        lock = None if kind == "static" else lock_level(_value(self.r, "drawdown_lock_level"), self.start, self.target)
        mode = after_payout_mode(_value(self.r, "drawdown_after_payout"))
        floor = initial
        static_after: Decimal | None = None
        mark = self.start  # the high-water mark: of the balance plus what was paid out, so payouts never lower it
        balance = self.start
        paid = ZERO
        breach: tuple[date, Decimal, Decimal] | None = None
        payouts_by_day: dict[date, list[PayoutRecord]] = defaultdict(list)
        for p in self.payouts:
            payouts_by_day[p.day].append(p)

        def trailed(mark: Decimal) -> Decimal:
            level = mark - max_loss
            if lock is not None and level > lock:
                level = max(lock, initial)
            return max(level, initial)

        for day in sorted(set(self.days) | set(payouts_by_day)):
            for p in payouts_by_day.get(day, []):
                balance -= p.withdrawn
                paid += p.withdrawn
                if static_after is None and mode != "unchanged":
                    static_after = {
                        "lock_start": self.start,
                        "lock_start_plus_100": self.start + 100,
                        "reset_zero": ZERO,
                        "freeze": floor,
                    }[mode]
                    floor = static_after
            for trade in self.days[day].trades if day in self.days else []:
                balance += trade.net_pnl
                if kind == "intraday_trailing" and static_after is None:
                    mark = max(mark, balance + paid)
                    floor = trailed(mark)
                if breach is None and balance <= floor:
                    breach = (day, balance, floor)
            # The end of the day moves an end-of-day floor, once the day is over (today's is still open).
            if kind in ("eod_trailing", "eod_trailing_realtime_enforced") and static_after is None and day < self.today:
                mark = max(mark, balance + paid)
                floor = trailed(mark)
        self.floor = floor
        room = self.balance - floor
        details = {"current": money(self.balance), "limit": money(floor)}
        if breach is not None:
            self.breached = True
            day, low, level = breach
            self.add(
                "drawdown",
                "Drawdown",
                label,
                "fail",
                **details,
                message=f"Breached on {day.isoformat()}: the balance closed a trade at {money(low)}, at or below the floor of {money(level)}.",
            )
            return
        if room <= max_loss * AT_RISK_SHARE:
            self.at_risk.append(f"{money(room)} above the drawdown floor")
        names = {
            "eod_trailing": "end-of-day trailing",
            "eod_trailing_realtime_enforced": "end-of-day trailing, enforced on live equity",
            "intraday_trailing": "intraday trailing",
            "static": "static",
        }
        if kind in ("eod_trailing", "static"):
            self.add(
                "drawdown",
                "Drawdown",
                label,
                "pass",
                **details,
                message=f"{money(room)} above the {names[kind]} floor.",
            )
            return
        why = (
            "The floor follows open-trade highs the export does not have, so it is approximated from closed trades."
            if kind == "intraday_trailing"
            else "The floor is exact, but the firm checks it against open equity during the day."
        )
        self.add(
            "drawdown",
            "Drawdown",
            label,
            "not_verifiable",
            **details,
            message=f"No breach in closed trades; {money(room)} above the {names[kind]} floor. {why}",
        )

    @property
    def target(self) -> Decimal | None:
        return _value(self.r, "profit_target_usd")

    def daily_loss(self) -> Decimal | None:
        kind = _value(self.r, "daily_loss_type")
        limit = _value(self.r, "daily_loss_limit_usd")
        label = "Daily loss limit"
        if not kind or kind == "none" or limit is None:
            self.add("daily_loss", "Daily loss", label, "n/a", message="No daily loss limit is set.")
            return None
        basis = _value(self.r, "daily_loss_basis")
        today = self.days.get(self.today)
        today_net = today.net if today else ZERO
        opening = self.balance - today_net  # the balance when today's session opened
        floor = opening - limit
        details = {"current": money(today_net), "limit": money(-limit)}
        hit = [d for d in self._ordered() if d.net <= -limit]
        optional = " (optional: clear it in the rules if you did not buy it)" if kind == "optional" else ""
        if kind == "hard" and hit:
            self.breached = True
            self.add(
                "daily_loss",
                "Daily loss",
                label,
                "fail",
                **details,
                message=f"A hard limit: {hit[0].day.isoformat()} closed at {money(hit[0].net)}.",
            )
            return floor
        if today and today.net <= -limit:
            self.at_risk.append("daily loss limit reached today")
            self.add(
                "daily_loss",
                "Daily loss",
                label,
                "fail",
                **details,
                message=f"Reached today ({money(today.net)}): trading is paused until the session resets{optional}.",
            )
            return floor
        if limit > 0 and today_net < 0 and -today_net >= limit * (1 - AT_RISK_SHARE):
            self.at_risk.append("close to today's daily loss limit")
        if basis == "realized":
            self.add(
                "daily_loss",
                "Daily loss",
                label,
                "pass",
                **details,
                message=f"{money(limit + today_net)} left today{optional}.",
            )
        else:
            counts = (
                "counts open losses" if basis == "realized_plus_unrealized" else "may count open losses (not stated)"
            )
            self.add(
                "daily_loss",
                "Daily loss",
                label,
                "not_verifiable",
                **details,
                message=f"No breach in closed trades; {money(limit + today_net)} left today. The limit {counts}{optional}.",
            )
        return floor

    def contracts(self) -> None:
        minis_limit = _value(self.r, "max_contracts_minis")
        micros_limit = _value(self.r, "max_contracts_micros")
        label = "Max contracts"
        if minis_limit is None and micros_limit is None:
            self.add("contracts", "Contracts & scaling", label, "n/a", message="No contract limit is set.")
            return
        events: list[tuple[datetime, int, int]] = []
        for day in self.days.values():
            for trade in day.trades:
                micro = trade.symbol_root.upper() in MICROS
                size = trade.max_position
                events.append((trade.entry_at, 1, size if micro else size * 10))
                events.append((trade.exit_at, 0, -(size if micro else size * 10)))
        peak = running = 0
        for _, _, change in sorted(events, key=lambda e: (e[0], e[1])):  # exits before entries at the same second
            running += change
            peak = max(peak, running)
        peak_minis = Decimal(peak) / 10
        over = []
        if minis_limit is not None and peak_minis > minis_limit:
            over.append(f"{peak_minis.normalize():f} minis against {minis_limit}")
        if micros_limit is not None and peak > micros_limit:
            over.append(f"{peak} micros against {micros_limit}")
        limit_text = " / ".join(
            part
            for part in (
                f"{minis_limit} minis" if minis_limit is not None else "",
                f"{micros_limit} micros" if micros_limit is not None else "",
            )
            if part
        )
        details = {"current": f"{peak_minis.normalize():f} minis ({peak} micros)", "limit": limit_text}
        if over:
            self.at_risk.append("traded more contracts than allowed")
            self.add(
                "contracts",
                "Contracts & scaling",
                label,
                "fail",
                **details,
                message="At most: " + "; ".join(over) + ".",
            )
            return
        scaling = (_value(self.r, "scaling_plan") or "").strip().lower()
        if scaling.startswith(("yes", "half-size")):
            self.add(
                "contracts",
                "Contracts & scaling",
                label,
                "not_verifiable",
                **details,
                message="Within the maximum, but a scaling plan lowers the limit at some balances.",
            )
            return
        self.add(
            "contracts", "Contracts & scaling", label, "pass", **details, message="Most open at once, from trade times."
        )

    def _scope(self) -> list[_Day]:
        """The days a payout rule counts: since the last payout on a funded account, every day of an evaluation."""
        return self.cycle if self.funded else self._ordered()

    def min_trading_days(self) -> None:
        needed = _value(self.r, "min_trading_days")
        label = "Minimum trading days"
        if needed is None or needed == 0:
            self.add("min_trading_days", "Qualifying days", label, "n/a", message="No minimum is set.")
            return
        traded = len(self._scope())
        since = "since the last payout" if self.funded and self.last_payout else "so far"
        self.add(
            "min_trading_days",
            "Qualifying days",
            label,
            "pass" if traded >= needed else "fail",
            current=str(traded),
            limit=str(needed),
            message=f"{plural(traded, 'trading day')} {since}.",
        )

    def _consistency_numbers(self) -> tuple[Decimal, Decimal, list[_Day]] | None:
        """(best day, what it is measured against, the days) for the rule's basis, or None without a rule."""
        basis = _value(self.r, "consistency_basis")
        share = _value(self.r, "consistency_pct")
        if share is None or not basis or basis in NO_CONSISTENCY:
            return None
        every = self._ordered()
        cycle = self.cycle
        best = lambda days: max((d.net for d in days), default=ZERO)  # noqa: E731
        total = sum((d.net for d in every), ZERO)
        if basis == "best_day_vs_total_profit":
            return best(every), total, every
        if basis == "best_day_vs_cycle_profit":
            return best(cycle), self.cycle_profit, cycle
        if basis == "best_day_vs_profit_target":
            return best(every), max(self.target or ZERO, total), every
        if basis == "best_day_vs_profit_goal":
            return best(cycle), self.target or ZERO, cycle
        if basis == "best_day_vs_positive_days_sum":
            return best(every), sum((d.net for d in every if d.net > 0), ZERO), every
        if basis == "best_day_vs_gross_profit_incl_payouts":
            return best(every), total, every
        if basis == "all_time_best_day_vs_cycle_profit":
            return best(every), self.cycle_profit, cycle
        return None

    def _denominator_after(self, extra: Decimal) -> Decimal:
        """What the best day is measured against if tomorrow makes `extra` more."""
        basis = _value(self.r, "consistency_basis")
        numbers = self._consistency_numbers()
        against = numbers[1] if numbers is not None else ZERO
        if basis == "best_day_vs_profit_target":
            total = sum((d.net for d in self._ordered()), ZERO)
            return max(self.target or ZERO, total + extra)
        if basis == "best_day_vs_profit_goal":
            return against
        return against + extra

    def consistency(self) -> None:
        label = "Consistency"
        numbers = self._consistency_numbers()
        if numbers is None:
            self.add("consistency", "Consistency", label, "n/a", message="No consistency rule is set.")
            return
        best, against, days = numbers
        share: Decimal = _value(self.r, "consistency_pct")
        limit = share / HUNDRED
        if not days:
            self.add(
                "consistency", "Consistency", label, "n/a", limit=pct(share), message="No trading days to measure yet."
            )
            return
        if against <= 0:
            self.add(
                "consistency",
                "Consistency",
                label,
                "fail",
                current=money(best),
                limit=pct(share),
                message=f"Needs a net profit first (it is {money(against)}).",
            )
            return
        ratio = best / against * HUNDRED
        ok = best < limit * against  # strict: "less than" (see PLAN.md)
        if _value(self.r, "consistency_basis") == "best_day_vs_profit_target" and self.target:
            self.effective_target = max(self.target, (best / limit).quantize(CENT))
        if ok:
            self.headroom = self._headroom(best, limit)
            self.add(
                "consistency",
                "Consistency",
                label,
                "pass",
                current=pct(ratio),
                limit=f"under {pct(share)}",
                message=f"Best day {money(best)} is {pct(ratio)} of {money(against)}.",
            )
            return
        need = (best / limit - against).quantize(CENT, rounding=ROUND_DOWN) + CENT
        self.add(
            "consistency",
            "Consistency",
            label,
            "fail",
            current=pct(ratio),
            limit=f"under {pct(share)}",
            message=f"Best day {money(best)} is {pct(ratio)} of {money(against)}: about {money(need)} more profit, on smaller days, brings it under.",
        )

    def _headroom(self, best: Decimal, limit: Decimal) -> Decimal | None:
        """The largest profit tomorrow can make with the rule still passing. While the rule passes, a day up to the best
        day keeps it passing; above that, max(best, x) < limit x denominator(x) holds below one point (the denominator
        grows more slowly than x), found here to the cent. None for a rule of 100% or more, which never binds."""
        if limit >= 1:
            return None

        def holds(extra: Decimal) -> bool:
            return max(best, extra) < limit * self._denominator_after(extra)

        low = max(best, ZERO)
        high = max(low * 2, Decimal(1))
        while holds(high):
            low, high = high, high * 2
        while high - low > CENT:
            middle = ((low + high) / 2).quantize(CENT, rounding=ROUND_DOWN)
            if middle <= low:
                break
            if holds(middle):
                low = middle
            else:
                high = middle
        return low.quantize(CENT, rounding=ROUND_DOWN)

    def qualifying_days(self) -> None:
        needed = _value(self.r, "qualifying_days_required")
        minimum = _value(self.r, "qualifying_day_min_profit_usd")
        label = "Qualifying days"
        if needed is None or needed == 0:
            self.add("qualifying_days", "Qualifying days", label, "n/a", message="No qualifying days are required.")
            return
        days = self._scope()
        counted = [d for d in days if (d.net >= minimum if minimum is not None else d.net > 0)]
        what = f"days of {money(minimum)}+" if minimum is not None else "profitable days"
        since = " since the last payout" if self.funded and self.last_payout else ""
        self.add(
            "qualifying_days",
            "Qualifying days",
            label,
            "pass" if len(counted) >= needed else "fail",
            current=str(len(counted)),
            limit=str(needed),
            message=f"{len(counted)} of {needed} {what}{since}.",
        )

    def profit_target(self) -> None:
        target = self.target
        label = "Profit target" if not self.funded else "Profit goal for this payout"
        if target is None:
            self.add("profit_target", "Pass / target", label, "n/a", message="No target is set.")
            return
        if self.funded:
            made = self.cycle_profit
            self.add(
                "profit_target",
                "Pass / target",
                label,
                "pass" if made >= target else "fail",
                current=money(made),
                limit=money(target),
                message=f"{money(made)} made since the last payout." if self.last_payout else f"{money(made)} made.",
            )
            return
        goal = self.effective_target or target
        self.effective_target = goal
        made = self.balance - self.start
        raised = f" (raised from {money(target)} by the consistency rule)" if goal > target else ""
        self.add(
            "profit_target",
            "Pass / target",
            label,
            "pass" if made >= goal else "fail",
            current=money(made),
            limit=money(goal),
            message=("Target reached." if made >= goal else f"{money(goal - made)} to go{raised}."),
        )

    def inactivity(self) -> InactivityStatus | None:
        rule = inactivity(_value(self.r, "inactivity_rule"))
        label = "Inactivity"
        if rule is None:
            self.add("inactivity", "Inactivity", label, "n/a", message="No inactivity rule is set.")
            return None
        if rule.kind == "none":
            self.add("inactivity", "Inactivity", label, "n/a", message="No time limit.")
            return None
        if rule.kind == "unreadable":
            self.add(
                "inactivity",
                "Inactivity",
                label,
                "not_verifiable",
                message=f"Cannot be counted from trades: “{_value(self.r, 'inactivity_rule')}”.",
            )
            return InactivityStatus(None, None, "Check this rule yourself.")
        start = self.account.purchase_date or (min(self.days) if self.days else None)
        if rule.kind == "window":
            if self.phase != "evaluation" or self.account.purchase_date is None:
                self.add(
                    "inactivity",
                    "Inactivity",
                    label,
                    "n/a" if self.phase != "evaluation" else "not_verifiable",
                    message=f"{rule.reading}."
                    + ("" if self.phase != "evaluation" else " Add the purchase date to count it."),
                )
                return None
            deadline = self.account.purchase_date + timedelta(days=rule.window - 1)
            return self._countdown(label, rule, deadline, closes="the evaluation's time runs out")
        if start is None:
            self.add(
                "inactivity",
                "Inactivity",
                label,
                "not_verifiable",
                message=f"{rule.reading}: add the purchase date to count it.",
            )
            return InactivityStatus(None, None, "Add the purchase date to count it.")
        if rule.kind == "rolling":
            floor = rule.min_profit if rule.min_profit is not None else CENT
            good = sorted(d.day for d in self.days.values() if d.net >= floor)
            last_needed = good[-rule.count] if len(good) >= rule.count else None
            anchor = max(start, last_needed) if last_needed else start
            deadline = anchor + timedelta(days=rule.window - 1)
            return self._countdown(label, rule, deadline, closes="the account closes")
        # "every": a trade (that makes the minimum, if there is one) at least every N days.
        if rule.per_trade and rule.min_profit is not None:
            active = [d.day for d in self.days.values() if any(t.net_pnl >= rule.min_profit for t in d.trades)]
        else:
            active = list(self.days)
        last = max([start, *active])
        if rule.trading_days:
            deadline = _add_weekdays(last, rule.window - 1)
        else:
            deadline = last + timedelta(days=rule.window - 1)
        return self._countdown(label, rule, deadline, closes="the firm acts on inactivity")

    def _countdown(self, label: str, rule: Inactivity, deadline: date, *, closes: str) -> InactivityStatus:
        left = (deadline - self.today).days
        details = {"current": plural(max(left, 0), "day") + " left", "limit": rule.reading}
        if left < 0:
            if (
                rule.kind == "window"
                and self.results.get("profit_target", None)
                and self.results["profit_target"].status == "pass"
            ):
                self.add("inactivity", "Inactivity", label, "pass", **details, message="Passed in time.")
                return InactivityStatus(None, deadline, "Passed in time.")
            self.breached = True
            message = f"The limit ran out on {deadline.isoformat()}: {closes}."
            self.add("inactivity", "Inactivity", label, "fail", **details, message=message)
            return InactivityStatus(left, deadline, message)
        if left <= max(2, rule.window // 4):
            self.at_risk.append(f"{plural(left, 'day')} left before the inactivity rule")
        message = f"{plural(left, 'day')} left (by {deadline.isoformat()}): {rule.reading}."
        self.add("inactivity", "Inactivity", label, "pass", **details, message=message)
        return InactivityStatus(left, deadline, message)

    # -- payouts ---------------------------------------------------------------------------------------------------

    def payout(self) -> PayoutStatus:
        group = "Payout"
        next_number = len(self.payouts) + 1
        buffer = _value(self.r, "payout_buffer_usd") or ZERO
        minimum = _value(self.r, "min_payout_usd")
        min_balance = _value(self.r, "min_balance_to_request_usd")
        above_buffer = self.balance - (self.start + buffer)

        if min_balance is None:
            self.add("min_balance", group, "Minimum balance to request", "n/a", message="No minimum balance is set.")
        else:
            self.add(
                "min_balance",
                group,
                "Minimum balance to request",
                "pass" if self.balance >= min_balance else "fail",
                current=money(self.balance),
                limit=money(min_balance),
                message="Reached." if self.balance >= min_balance else f"{money(min_balance - self.balance)} to go.",
            )
        self.add(
            "buffer",
            group,
            "Above start + buffer",
            "pass" if above_buffer > 0 else "fail",
            current=money(self.balance),
            limit=money(self.start + buffer),
            message=f"{money(above_buffer)} above it." if above_buffer > 0 else f"{money(-above_buffer)} to go.",
        )

        cap = cap_for(_value(self.r, "payout_cap_schedule"), next_number)
        max_payout = _value(self.r, "max_payout_usd")
        if cap.kind in ("absent", "unknown") and max_payout is not None:
            cap = Cap("amount", max_payout)
        if cap.kind == "amount" and cap.amount is not None:
            self.add("payout_cap", group, f"Cap for payout #{next_number}", "pass", limit=money(cap.amount))
        elif cap.kind == "uncapped":
            self.add("payout_cap", group, f"Cap for payout #{next_number}", "n/a", message="Uncapped.")
        else:
            schedule = _value(self.r, "payout_cap_schedule")
            self.add(
                "payout_cap",
                group,
                f"Cap for payout #{next_number}",
                "not_verifiable",
                message=(
                    f"The cap schedule does not say what payout #{next_number} may be (“{schedule}”)."
                    if schedule
                    else "No cap is set: give the maximum payout, or write 'uncapped' in the cap schedule."
                ),
            )

        self._cadence(group)
        self._request_days(group)
        limit = payout_limit_count(_value(self.r, "max_payouts_before_live_or_close"))
        if limit is None:
            text = _value(self.r, "max_payouts_before_live_or_close")
            self.add(
                "max_payouts",
                group,
                "Payouts before live or close",
                "n/a",
                current=str(len(self.payouts)),
                message=text or "No limit is set.",
            )
        else:
            self.add(
                "max_payouts",
                group,
                "Payouts before live or close",
                "pass" if len(self.payouts) < limit else "fail",
                current=str(len(self.payouts)),
                limit=str(limit),
                message=_value(self.r, "max_payouts_before_live_or_close") or "",
            )

        # What could be withdrawn: the cap, the percentage cap, and what sits above start + buffer.
        terms: list[Decimal] = [above_buffer]
        if cap.kind == "amount" and cap.amount is not None:
            terms.append(cap.amount)
        for share in (percentage_cap(_value(self.r, "payout_pct_of_profit_cap")), cap.pct):
            if share is None:
                continue
            percent, basis = share
            eligible = {
                "profit": self.balance - self.start,
                "cycle profit": self.cycle_profit,
                "balance": self.balance,
            }[basis]
            terms.append(eligible * percent / HUNDRED)
        gross = to_money(max(min(terms), ZERO)).quantize(CENT, rounding=ROUND_DOWN)
        enough = gross >= minimum if minimum is not None else gross > 0
        self.add(
            "withdrawable",
            group,
            "Withdrawable now",
            "pass" if enough else "fail",
            current=money(gross),
            limit=f"at least {money(minimum)}" if minimum is not None else "more than $0.00",
            message="The least of the cap, the percentage cap and the balance above start + buffer.",
        )

        gates = (
            "qualifying_days",
            "min_trading_days",
            "consistency",
            "profit_target",
            "min_balance",
            "buffer",
            "payout_cap",
            "payout_cadence",
            "payout_days",
            "inactivity",
            "max_payouts",
            "withdrawable",
        )
        blockers = [
            self.results[key].label
            for key in gates
            if key in self.results and self.results[key].status not in ("pass", "n/a")
        ]
        if self.breached:
            blockers.insert(0, "Breached")
        ready = not blockers
        share = split_share(_value(self.r, "profit_split"))
        return PayoutStatus(
            ready=ready,
            gross=gross if ready else None,
            net=to_money(gross * share / HUNDRED) if ready and share is not None else None,
            split_pct=share,
            next_number=next_number,
            lifetime_gross=sum((p.withdrawn for p in self.payouts), ZERO),
            lifetime_net=sum((p.net for p in self.payouts), ZERO),
            progress=self._progress(min_balance, buffer, minimum),
            blockers=tuple(blockers),
        )

    def _ready_balance(self, min_balance: Decimal | None, buffer: Decimal, minimum: Decimal | None) -> Decimal:
        level = self.start + buffer + (minimum or ZERO)
        if min_balance is not None:
            level = max(level, min_balance)
        if self.target is not None:  # a funded account with a profit goal for each payout
            level = max(level, self.balance - self.cycle_profit + self.target)
        return level

    def _progress(self, min_balance: Decimal | None, buffer: Decimal, minimum: Decimal | None) -> str:
        parts = []
        qualifying = self.results.get("qualifying_days")
        if qualifying and qualifying.status != "n/a":
            parts.append(f"{qualifying.current}/{qualifying.limit} qualifying days")
        parts.extend(self._consistency_progress())
        short = self._ready_balance(min_balance, buffer, minimum) - self.balance
        if short > 0:
            parts.append(f"{money(short)} to buffer")
        return " · ".join(parts)

    def _cadence(self, group: str) -> None:
        label = "Payout cadence"
        rule = cadence(_value(self.r, "payout_frequency"))
        if rule is None:
            self.add("payout_cadence", group, label, "n/a", message="No cadence is set.")
            return
        if not rule.recognised:
            self.add(
                "payout_cadence",
                group,
                label,
                "not_verifiable",
                message=f"Cannot be read: “{_value(self.r, 'payout_frequency')}”.",
            )
            return
        problems = []
        last = self.last_payout
        if last is not None and rule.calendar_gap is not None and (self.today - last).days < rule.calendar_gap:
            problems.append(f"next request from {(last + timedelta(days=rule.calendar_gap)).isoformat()}")
        if last is not None and rule.trading_gap is not None and len(self.cycle) < rule.trading_gap:
            problems.append(f"{len(self.cycle)} of {rule.trading_gap} trading days since the last payout")
        if rule.per_month is not None:
            this_month = sum(
                1 for p in self.payouts if (p.day.year, p.day.month) == (self.today.year, self.today.month)
            )
            if this_month >= rule.per_month:
                problems.append(f"already {this_month} this month")
        if rule.once_a_day and last == self.today:
            problems.append("already one today")
        self.add(
            "payout_cadence",
            group,
            label,
            "fail" if problems else "pass",
            limit=rule.reading,
            message="; ".join(problems).capitalize() + "." if problems else f"Read as: {rule.reading}.",
        )

    def _request_days(self, group: str) -> None:
        label = "Request day"
        rule = request_days(_value(self.r, "payout_days_allowed"))
        if rule is None or rule.kind == "any":
            self.add("payout_days", group, label, "n/a", message="Any day.")
            return
        if rule.kind == "unreadable":
            self.add(
                "payout_days",
                group,
                label,
                "not_verifiable",
                message=f"Cannot be read: “{_value(self.r, 'payout_days_allowed')}”.",
            )
            return
        if rule.kind == "weekdays":
            ok = self.calendar_today.weekday() < 5
            self.add(
                "payout_days",
                group,
                label,
                "pass" if ok else "fail",
                limit="Monday to Friday",
                message="Today is a business day." if ok else "Requests are taken Monday to Friday.",
            )
            return
        traded = self.today in self.days
        self.add(
            "payout_days",
            group,
            label,
            "fail" if traded else "pass",
            limit="a day with no new trade",
            message="You traded today: request on a day you do not trade."
            if traded
            else "No trade today: keep it that way until the request is in.",
        )

    def restrictions(self) -> None:
        group = "Restrictions"
        overnight = (_value(self.r, "overnight_holding") or "").strip().lower()
        if overnight.startswith("not_allowed"):
            held = [
                t
                for d in self.days.values()
                for t in d.trades
                if trading_date(t.entry_at, self.session) != trading_date(t.exit_at, self.session)
            ]
            self.add(
                "overnight",
                group,
                "No overnight holding",
                "fail" if held else "pass",
                current=plural(len(held), "trade") + " held over a session close",
                message="Every trade closed in the session it opened."
                if not held
                else "A trade was held into the next session.",
            )
            if held:
                self.at_risk.append("a trade was held overnight")
        else:
            self.add("overnight", group, "Overnight holding", "n/a", message=overnight or "Not restricted.")
        flat = flat_time(_value(self.r, "flat_by_time"))
        if flat is None:
            text = _value(self.r, "flat_by_time")
            self.add(
                "flat_by_time",
                group,
                "Flat by",
                "not_verifiable" if text else "n/a",
                message=f"Cannot be read: “{text}”." if text else "No time is set.",
            )
        else:
            at, where = flat
            late = []
            for d in self.days.values():
                for t in d.trades:
                    # A minute's grace: a firm closes what is open at the flat time itself, and that is not a breach.
                    deadline = datetime.combine(d.day, at, tzinfo=zone(where)) + FLAT_GRACE
                    if t.exit_at > deadline:
                        late.append(t)
            self.add(
                "flat_by_time",
                group,
                "Flat by",
                "fail" if late else "pass",
                current=plural(len(late), "trade") + " closed after it",
                limit=f"{at.strftime('%H:%M')} {'ET' if where == NEW_YORK else 'CT'}",
                message="Every trade closed before the flat time."
                if not late
                else "A trade was still open at the flat time.",
            )
            if late:
                self.at_risk.append("a trade was open past the flat time")
        news = (_value(self.r, "news_trading") or "").strip()
        if news and not news.lower().startswith("allowed"):
            self.add("news", group, "News trading", "not_verifiable", message=f"Needs an economic calendar: {news}")
        else:
            self.add("news", group, "News trading", "n/a", message=news or "Not restricted.")
        other = (_value(self.r, "other_restrictions") or "").strip()
        self.add(
            "other_restrictions",
            group,
            "Other restrictions",
            "not_verifiable" if other else "n/a",
            message=f"Check these yourself: {other}" if other else "None set.",
        )

    # -- the summary -------------------------------------------------------------------------------------------------

    def spectrum(self, dll_floor: Decimal | None, payout: PayoutStatus | None) -> Spectrum:
        left: Decimal | None = self.floor
        left_kind: Literal["drawdown", "daily_loss"] | None = "drawdown" if self.floor is not None else None
        if dll_floor is not None and (left is None or dll_floor > left):
            left, left_kind = dll_floor, "daily_loss"
        right: Decimal | None = None
        right_kind: Literal["profit_target", "payout"] | None = None
        if self.funded:
            names = ("payout_buffer_usd", "min_balance_to_request_usd", "min_payout_usd", "profit_target_usd")
            if any(_value(self.r, name) is not None for name in names):
                buffer = _value(self.r, "payout_buffer_usd") or ZERO
                right = self._ready_balance(
                    _value(self.r, "min_balance_to_request_usd"), buffer, _value(self.r, "min_payout_usd")
                )
                right_kind = "payout"
        elif self.effective_target is not None:
            right, right_kind = self.start + self.effective_target, "profit_target"
        position = None
        if left is not None and right is not None and right > left:
            position = float(min(Decimal(1), max(ZERO, (self.balance - left) / (right - left))))
        return Spectrum(
            breach_level=left,
            breach_kind=left_kind,
            target_level=right,
            target_kind=right_kind,
            balance=self.balance,
            to_breach=self.balance - left if left is not None else None,
            to_target=max(ZERO, right - self.balance) if right is not None else None,
            position=position,
        )

    def status(self, payout: PayoutStatus | None) -> AccountStatus:
        if self.breached:
            return "breached"
        if not self.funded:
            gates = ("profit_target", "min_trading_days", "consistency", "qualifying_days")
            met = self.results["profit_target"].status == "pass" and all(
                self.results[key].status in ("pass", "n/a") for key in gates
            )
            if met:
                return "passed"
        elif payout is not None and payout.ready:
            return "payout_ready"
        return "at_risk" if self.at_risk else "ok"


def _add_weekdays(start: date, count: int) -> date:
    """The date `count` weekdays after `start` (exchange holidays are not known, so the count is on the early side)."""
    day = start
    added = 0
    while added < count:
        day += timedelta(days=1)
        if day.weekday() < 5:
            added += 1
    return day
