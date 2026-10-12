"""The compliance and payout engine (`compliance.py`), table by table.

The rule sets are the research CSV's rows for the named programs (`docs/prop-rules/prop_firm_rules_v2.csv`), copied here
so the tests do not need the file; where the CSV leaves a payout cap empty and a test needs one, the test says it is the
person's own entry. Trades are closed at 10:00 New York time unless a test says otherwise, so each lands on its own
calendar day as the firm's trading day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tradejournal import compliance as c
from tradejournal.prop_rules import FIELD_NAMES

NY = ZoneInfo("America/New_York")
D = Decimal


@dataclass(frozen=True)
class Trade:
    entry_at: datetime
    exit_at: datetime
    net_pnl: Decimal
    symbol_root: str = "NQ"
    max_position: int = 1


def at(day: date, hour: int = 10, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NY).astimezone(UTC)


def trades(*days: tuple[date, str | list[str]]) -> list[Trade]:
    """One trade per amount, five minutes apart, from 10:00 on each day."""
    made = []
    for day, amounts in days:
        for n, amount in enumerate([amounts] if isinstance(amounts, str) else amounts):
            start = at(day) + timedelta(minutes=5 * n)
            made.append(Trade(start, start + timedelta(minutes=2), D(amount)))
    return made


def weekdays(first: date, count: int) -> list[date]:
    out, day = [], first
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def rules(**values) -> dict:
    """A rule set with every field empty except the ones given (strings become Decimals for money and percentages)."""
    filled = dict.fromkeys(FIELD_NAMES)
    for key, value in values.items():
        assert key in filled, key
        filled[key] = D(value) if isinstance(value, str) and key.endswith(("_usd", "_pct")) else value
    return filled


def run(rule_set, *, start="50000", phase="funded", day_trades=(), payouts=(), now=None, purchase=None):
    return c.evaluate(
        rule_set,
        c.AccountFacts(start=D(start), phase=phase, purchase_date=purchase),
        trades(*day_trades),
        list(payouts),
        now or at(date(2026, 10, 12), 12),
    )


def result(evaluation, key):
    return next(r for r in evaluation.rules if r.key == key)


# ---- the programs the owner trades, and the report's worked examples --------------------------------------------------

APEX_EOD_50K_PA = rules(
    firm="Apex Trader Funding",
    program="EOD Trailing (Apex 4.0)",
    account_size="50K",
    phase="funded",
    rule_version="Apex 4.0 (new accounts)",
    effective_from=date(2026, 3, 1),
    max_loss_usd="2000",
    drawdown_type="eod_trailing_realtime_enforced",
    drawdown_lock_level="start_balance_plus_100",
    daily_loss_limit_usd="1000",
    daily_loss_type="soft",
    daily_loss_basis="realized_plus_unrealized",
    max_contracts_minis=4,
    max_contracts_micros=40,
    scaling_plan="yes: $0-1,499 -> 2 minis/DLL $1,000; ...",
    consistency_pct="50",
    consistency_basis="best_day_vs_cycle_profit",
    qualifying_day_min_profit_usd="250",
    qualifying_days_required=5,
    payout_buffer_usd="2100",
    min_balance_to_request_usd="52600",
    min_payout_usd="500",
    max_payout_usd="1500",
    payout_cap_schedule="1:1500|2:1500|3:2000|4:2500|5:2500|6:3000",
    payout_frequency="every 5 new qualifying days (up to weekly)",
    payout_days_allowed="any (no weekday restriction stated)",
    max_payouts_before_live_or_close="6 (PA closes after 6th)",
    profit_split="100/0",
    overnight_holding="not_allowed",
    inactivity_rule="at least 2 trading days with $50+ net profit in every rolling 30-day window; dormant from day 15; closed at day 30",
)
APEX_DAYS = list(zip(weekdays(date(2026, 9, 28), 5), ["600", "800", "700", "500", "1400"]))  # 54,000 at the close


def test_apex_eod_50k_pa_report_example_is_payout_ready_for_the_first_cap():
    # The report: closing at $54,000 after five $250+ days, no day at 50% or more of cycle profit:
    # min($1,500 first-payout cap, $54,000 - $52,100) = $1,500.
    e = run(APEX_EOD_50K_PA, day_trades=APEX_DAYS, purchase=date(2026, 9, 25))
    assert e.balance == D("54000") and e.net_pnl == D("4000")
    assert e.status == "payout_ready"
    assert e.payout.ready and e.payout.gross == D("1500.00") and e.payout.net == D("1500.00")  # 100/0 split
    assert e.payout.next_number == 1 and e.payout.blockers == ()
    assert e.floor == D("50100")  # trailed to 52,000, stopped at start + $100
    assert result(e, "consistency").status == "pass" and result(e, "consistency").current == "35%"
    assert result(e, "qualifying_days").current == "5"
    # Enforced on live equity, and the daily limit counts open losses: never a pass from closed trades.
    assert result(e, "drawdown").status == "not_verifiable"
    assert result(e, "daily_loss").status == "not_verifiable"
    assert result(e, "contracts").status == "not_verifiable"  # within 4 minis, but a scaling plan applies


def test_apex_payout_cap_follows_the_payout_number_and_the_cadence_waits_a_week():
    first = c.PayoutRecord(date(2026, 10, 6), D("1500"), D("1500"))
    more = list(zip(weekdays(date(2026, 10, 7), 5), ["400", "450", "500", "400", "350"]))
    early = run(APEX_EOD_50K_PA, day_trades=[*APEX_DAYS, *more], payouts=[first], now=at(date(2026, 10, 12), 12))
    assert result(early, "payout_cadence").status == "fail"  # 6 days after the first payout, at most weekly
    assert early.status != "payout_ready"
    later = run(APEX_EOD_50K_PA, day_trades=[*APEX_DAYS, *more], payouts=[first], now=at(date(2026, 10, 14), 12))
    assert later.payout.next_number == 2 and later.payout.lifetime_gross == D("1500")
    # 54,000 - 1,500 + 2,100 = 54,600: min(cap #2 $1,500, 54,600 - 52,100 = 2,500) = 1,500.
    assert later.balance == D("54600") and later.payout.gross == D("1500.00") and later.status == "payout_ready"


@pytest.mark.parametrize(
    ("amounts", "status"),
    [(["1999.99", "1000", "1000.01"], "pass"), (["2000", "1000", "1000"], "fail")],  # strict: 50% or more fails
)
def test_apex_consistency_is_strict_at_exactly_half(amounts, status):
    days = list(zip(weekdays(date(2026, 9, 28), 3), amounts))
    e = run(APEX_EOD_50K_PA, day_trades=days)
    assert result(e, "consistency").status == status


def test_apex_consistency_headroom_is_the_largest_day_that_keeps_under_half():
    e = run(APEX_EOD_50K_PA, day_trades=APEX_DAYS)
    # Best 1,400 of 4,000: tomorrow's x must stay below 50% of (4,000 + x), so x < 4,000; to the cent below it.
    assert e.consistency_headroom == D("3999.99")


def test_apex_not_payout_ready_with_four_qualifying_days_and_the_progress_says_why():
    days = list(zip(weekdays(date(2026, 9, 28), 5), ["600", "800", "700", "200", "1400"]))
    e = run(APEX_EOD_50K_PA, day_trades=days)
    assert e.status == "ok" and not e.payout.ready and e.payout.gross is None
    assert "Qualifying days" in e.payout.blockers
    assert e.payout.progress == "4/5 qualifying days · best day 37.8% of 50% max"  # 1,400 of 3,700


def test_apex_progress_counts_what_is_left_to_the_buffer():
    e = run(APEX_EOD_50K_PA, day_trades=[(date(2026, 10, 1), "1960")])
    assert e.payout.progress == "1/5 qualifying days · best day 100% of 50% max · $640.00 to buffer"
    assert e.spectrum.target_level == D("52600") and e.spectrum.to_target == D("640")
    # Today's daily limit (51,960 - 1,000) is closer than the drawdown floor (51,960 - 2,000): it is the left end.
    assert e.floor == D("49960")
    assert e.spectrum.breach_kind == "daily_loss" and e.spectrum.breach_level == D("50960")
    assert e.spectrum.to_breach == D("1000")


def test_apex_inactivity_counts_down_from_the_second_most_recent_qualifying_day():
    days = [(date(2026, 9, 1), "60"), (date(2026, 9, 20), "75"), (date(2026, 10, 1), "30")]  # $30 does not count
    e = run(APEX_EOD_50K_PA, day_trades=days, purchase=date(2026, 8, 25))
    # Qualifying days 9/1 and 9/20: 9/1 leaves the 30-day window after 9/30, so the rule ran out.
    assert e.status == "breached" and result(e, "inactivity").status == "fail"
    fresh = run(APEX_EOD_50K_PA, day_trades=[(date(2026, 9, 25), "60"), (date(2026, 10, 2), "80")])
    # From 9/25: the window that still holds both ends 10/24, twelve days after 10/12.
    assert fresh.inactivity.days_left == 12 and fresh.inactivity.deadline == date(2026, 10, 24)


# LucidPro 50K funded, bought after 2025-11-28: the CSV leaves the cap, the cadence and the qualifying days empty
# (flagged), so on its own it can never say "payout ready"; with the person's cap it can.
LUCID_PRO_50K = rules(
    firm="Lucid Trading",
    program="LucidPro",
    account_size="50K",
    phase="funded",
    rule_version="current (bought/reset after 2025-11-28 3:00 PM EST)",
    effective_from=date(2025, 11, 28),
    max_loss_usd="2000",
    drawdown_type="eod_trailing",
    drawdown_lock_level="start_balance_plus_100 (once EOD balance > 52100)",
    daily_loss_type="soft",
    max_contracts_minis=4,
    max_contracts_micros=40,
    scaling_plan="no (full size immediately)",
    consistency_pct="40",
    consistency_basis="best_day_vs_cycle_profit",
    payout_buffer_usd="2100",
    min_balance_to_request_usd="52600",
    min_payout_usd="500",
    payout_days_allowed="any",
    max_payouts_before_live_or_close="live review after payout 5",
    profit_split="90/10",
    overnight_holding="not_allowed",
    flat_by_time="4:45 PM EST Mon-Fri (earlier on holidays); auto-close is not a failure; reopens 6 PM EST",
    inactivity_rule="no trade with net P&L of at least $1 in 30 calendar days -> account deleted",
)
LUCID_DAYS = list(zip(weekdays(date(2026, 9, 28), 6), ["700", "800", "600", "900", "500", "500"]))  # 54,000


def test_lucid_pro_is_not_payout_ready_while_the_cap_is_unknown():
    e = run(LUCID_PRO_50K, day_trades=LUCID_DAYS)
    assert result(e, "payout_cap").status == "not_verifiable"
    assert e.status == "ok" and not e.payout.ready
    # The daily limit is switched on (soft) with no amount: the CSV had text there, so there is nothing to apply.
    assert result(e, "daily_loss").status == "n/a"


def test_lucid_pro_with_the_persons_cap_pays_the_least_of_cap_and_buffer_room_at_90_percent():
    with_cap = {**LUCID_PRO_50K, "payout_cap_schedule": "all:1500"}  # the person's own entry
    e = run(with_cap, day_trades=LUCID_DAYS)
    assert e.status == "payout_ready"
    assert e.payout.gross == D("1500.00") and e.payout.net == D("1350.00") and e.payout.split_pct == D("90")
    assert result(e, "drawdown").status == "pass" and e.floor == D("50100")  # end of day: exact from closed trades
    assert result(e, "consistency").current == "22.5%"  # 900 of 4,000, under 40%
    assert result(e, "inactivity").status == "pass"


def test_lucid_flat_time_has_a_minute_of_grace_for_the_firms_own_close():
    day = date(2026, 10, 5)
    auto_closed = Trade(at(day, 16, 30), at(day, 16, 45) + timedelta(seconds=20), D("100"))
    held = Trade(at(day, 16, 30), at(day, 16, 50), D("100"))
    for trade, status in ((auto_closed, "pass"), (held, "fail")):
        e = c.evaluate(LUCID_PRO_50K, c.AccountFacts(D("50000"), "funded"), [trade], [], at(date(2026, 10, 12)))
        assert result(e, "flat_by_time").status == status


# Topstep XFA 50K, standard path: a $0 starting balance, floor at -$2,000 that stops at $0, reset to $0 after the
# first payout; 50% of the balance; five $150+ days. The cap is empty in the CSV (flagged conflict).
TOPSTEP_XFA_50K = rules(
    firm="Topstep",
    program="Express Funded Account XFA (Standard payout path)",
    account_size="50K",
    phase="funded",
    rule_version="XFA dual paths (chosen at activation)",
    max_loss_usd="2000",
    drawdown_type="eod_trailing",
    drawdown_lock_level="start_balance ($0 XFA balance)",
    drawdown_after_payout="resets_to_zero (MLL set to $0 balance permanently after first payout)",
    daily_loss_limit_usd="1000",
    daily_loss_type="optional",
    daily_loss_basis="realized_plus_unrealized",
    scaling_plan="yes (tiers not found)",
    consistency_basis="none",
    qualifying_day_min_profit_usd="150",
    qualifying_days_required=5,
    min_payout_usd="125",
    payout_pct_of_profit_cap="50% of balance",
    payout_frequency="each completed cycle (5 winning days of $150+ net (non-consecutive)); after 1st payout must also be net profitable since last payout",
    payout_days_allowed="Sun 5 PM CT - Fri 5 PM CT",
    profit_split="90/10 (sign-ups from 2026-01-12; earlier dashboards: 100% of first $10K lifetime then 90/10)",
    overnight_holding="not_allowed",
    flat_by_time="3:10 PM CT (trading day 5:00 PM CT - 3:10 PM CT); no weekend holds",
    inactivity_rule="XFA closed after 30 days of inactivity (FuturesHive)",
)
XFA_DAYS = list(zip(weekdays(date(2026, 9, 28), 5), ["300", "300", "300", "300", "300"]))


def test_topstep_xfa_pays_half_the_balance_on_a_business_day():
    xfa = {**TOPSTEP_XFA_50K, "payout_cap_schedule": "all:2000"}  # the person's own entry
    monday = run(xfa, start="0", day_trades=XFA_DAYS, now=at(date(2026, 10, 12), 12))
    assert monday.balance == D("1500") and monday.status == "payout_ready"
    assert monday.payout.gross == D("750.00") and monday.payout.net == D("675.00")  # 50% of 1,500, at 90%
    assert monday.floor == D("-500")  # 1,500 - 2,000: still trailing, below its stop at the $0 start
    saturday = run(xfa, start="0", day_trades=XFA_DAYS, now=at(date(2026, 10, 10), 12))
    assert result(saturday, "payout_days").status == "fail" and saturday.status != "payout_ready"


def test_topstep_xfa_floor_resets_to_zero_after_the_first_payout_and_a_loss_through_it_breaches():
    xfa = {**TOPSTEP_XFA_50K, "payout_cap_schedule": "all:2000"}
    paid = c.PayoutRecord(date(2026, 10, 5), D("750"), D("675"))
    e = run(xfa, start="0", day_trades=[*XFA_DAYS, (date(2026, 10, 6), "-760")], payouts=[paid])
    assert e.balance == D("-10") and e.status == "breached"
    assert result(e, "drawdown").status == "fail" and "2026-10-06" in result(e, "drawdown").message


def test_topstep_xfa_without_a_cap_is_not_payout_ready():
    e = run(TOPSTEP_XFA_50K, start="0", day_trades=XFA_DAYS)
    assert result(e, "payout_cap").status == "not_verifiable" and not e.payout.ready


# Tradeify Select Flex 50K funded (bought after 2026-09-01): five $150+ days, 50% of profit up to $2,500, no buffer.
TRADEIFY_SELECT_FLEX_50K = rules(
    firm="Tradeify",
    program="Select Flex",
    account_size="50K",
    phase="funded",
    rule_version="bought after 2026-09-01",
    effective_from=date(2026, 9, 1),
    max_loss_usd="2000",
    drawdown_type="eod_trailing_realtime_enforced",
    drawdown_lock_level="start_balance_plus_100 (once EOD balance reaches 52100)",
    drawdown_after_payout="locks_at_start_plus_100 immediately on payout request",
    daily_loss_type="none",
    scaling_plan="yes: by EOD equity, shared Flex/Daily table (not found)",
    consistency_basis="none",
    qualifying_day_min_profit_usd="150",
    qualifying_days_required=5,
    payout_buffer_usd=None,
    min_payout_usd="250",
    max_payout_usd="2500",
    payout_cap_schedule="all:2500",
    payout_pct_of_profit_cap="50% of profit",
    payout_frequency="every 5 winning days; cycle must show positive net profit",
    payout_days_allowed="any",
    max_payouts_before_live_or_close="Elite Live eligibility: 3 payouts on one account or 10 across plans",
    profit_split="90/10",
    overnight_holding="not_allowed",
    flat_by_time="4:45 PM ET (12:59 PM ET on holidays); auto-closed without failing",
)


def test_tradeify_select_flex_pays_half_the_profit_like_the_reports_flex_example():
    # The report's LucidFlex example has the same shape: $3,000 profit, five $150+ days, min($2,000 cap, 50% x $3,000).
    days = list(zip(weekdays(date(2026, 9, 28), 5), ["600", "600", "600", "600", "600"]))
    e = run(TRADEIFY_SELECT_FLEX_50K, day_trades=days)
    assert e.status == "payout_ready" and e.payout.gross == D("1500.00") and e.payout.net == D("1350.00")
    assert result(e, "consistency").status == "n/a" and e.consistency_headroom is None


def test_tradeify_floor_locks_at_start_plus_100_on_the_first_payout():
    days = list(zip(weekdays(date(2026, 9, 28), 5), ["600", "600", "600", "600", "600"]))
    paid = c.PayoutRecord(date(2026, 10, 5), D("1500"), D("1350"))
    e = run(TRADEIFY_SELECT_FLEX_50K, day_trades=days, payouts=[paid])
    assert e.floor == D("50100") and e.balance == D("51500")
    assert e.payout.next_number == 2 and e.payout.lifetime_net == D("1350")
    assert result(e, "qualifying_days").current == "0"  # counted again from after the payout


# ---- an evaluation that passes, a static drawdown, an account with rules closed trades cannot prove -------------------

APEX_EOD_50K_EVAL = rules(
    firm="Apex Trader Funding",
    program="EOD Trailing (Apex 4.0)",
    account_size="50K",
    phase="evaluation",
    rule_version="Apex 4.0 (new accounts)",
    profit_target_usd="3000",
    max_loss_usd="2000",
    drawdown_type="eod_trailing_realtime_enforced",
    drawdown_lock_level="none (Tradovate); profit_target_balance (Rithmic/WealthCharts)",
    daily_loss_limit_usd="1000",
    daily_loss_type="soft",
    daily_loss_basis="realized_plus_unrealized",
    min_trading_days=0,
    consistency_basis="none",
    inactivity_rule="30 calendar day evaluation access window",
)


def test_an_evaluation_that_reaches_its_target_has_passed():
    days = [(date(2026, 10, 1), "1200"), (date(2026, 10, 2), "900"), (date(2026, 10, 5), "1100")]
    e = run(APEX_EOD_50K_EVAL, phase="evaluation", day_trades=days, purchase=date(2026, 9, 30))
    assert e.status == "passed" and e.payout is None and e.balance == D("53200")
    assert result(e, "profit_target").status == "pass"
    assert e.spectrum.target_kind == "profit_target" and e.spectrum.target_level == D("53000")
    assert e.spectrum.to_target == 0 and e.spectrum.position == 1.0
    assert e.progress == "target reached"
    # The evaluation never stops trailing on Tradovate: 53,200 - 2,000 after the last full day closed.
    assert e.floor == D("51200")


def test_an_evaluation_short_of_its_target_is_ok_and_counts_its_access_window():
    days = [(date(2026, 10, 1), "1200"), (date(2026, 10, 2), "300")]
    e = run(APEX_EOD_50K_EVAL, phase="evaluation", day_trades=days, purchase=date(2026, 9, 30))
    assert e.status == "ok" and e.progress == "$1,500.00 to target"
    assert e.inactivity.deadline == date(2026, 10, 29) and e.inactivity.days_left == 17
    expired = run(APEX_EOD_50K_EVAL, phase="evaluation", day_trades=days, purchase=date(2026, 9, 1))
    assert expired.status == "breached" and result(expired, "inactivity").status == "fail"


def test_a_topstep_style_target_rises_with_a_big_best_day():
    combine = rules(
        firm="Topstep",
        program="Combine",
        account_size="50K",
        phase="evaluation",
        rule_version="x",
        profit_target_usd="3000",
        consistency_pct="50",
        consistency_basis="best_day_vs_profit_target",
    )
    days = [(date(2026, 10, 1), "2000"), (date(2026, 10, 2), "1000")]
    e = run(combine, phase="evaluation", day_trades=days)
    # Best day 2,000 is 50% or more of 3,000? 66.7%: the target becomes 2,000 / 50% = 4,000.
    assert e.effective_target == D("4000.00") and e.status == "ok"
    assert result(e, "profit_target").limit == "$4,000.00"
    assert result(e, "consistency").status == "fail"


STATIC_25K = rules(
    firm="Elite Trader Funding",
    program="Static",
    account_size="25K",
    phase="evaluation",
    rule_version="x",
    profit_target_usd="1500",
    max_loss_usd="1000",
    drawdown_type="static",
)


@pytest.mark.parametrize(
    ("amounts", "status", "verdict"),
    [
        (["800", "200", "-1499.99"], "ok", "pass"),  # a trailing floor would have been 25,000; the static one is 24,000
        (["800", "200", "-2000"], "breached", "fail"),  # exactly at the floor is a breach
    ],
)
def test_a_static_drawdown_never_trails(amounts, status, verdict):
    e = run(STATIC_25K, start="25000", phase="evaluation", day_trades=[(date(2026, 10, 1), amounts)])
    assert e.floor == D("24000") and e.status == status and result(e, "drawdown").status == verdict


NOT_VERIFIABLE = rules(
    firm="Take Profit Trader",
    program="PRO",
    account_size="50K",
    phase="funded",
    rule_version="x",
    max_loss_usd="2000",
    drawdown_type="intraday_trailing",
    daily_loss_limit_usd="1100",
    daily_loss_type="hard",
    daily_loss_basis="realized_plus_unrealized",
    news_trading="restricted: Tier 1 blackout 2 min before to 2 min after release",
    other_restrictions="no hedging",
    inactivity_rule="Tradovate archives accounts not logged into for a calendar month (counts as failure)",
    payout_days_allowed="weekly cycle",
)


def test_rules_that_need_open_equity_are_never_a_pass():
    e = run(NOT_VERIFIABLE, day_trades=[(date(2026, 10, 1), ["500", "-300"])])
    for key in ("drawdown", "daily_loss", "news", "other_restrictions", "inactivity", "payout_days"):
        assert result(e, key).status == "not_verifiable", key
    assert all(r.status != "pass" for r in e.rules if r.key in ("drawdown", "daily_loss"))
    assert e.status == "ok" and not e.payout.ready  # not verifiable is not a breach, and it is not a yes either


def test_but_a_breach_that_closed_trades_prove_is_a_breach():
    # Realized peak 50,900 trails the approximate floor to 48,900; a close at 48,850 is below it whatever happened inside.
    e = run(NOT_VERIFIABLE, day_trades=[(date(2026, 10, 1), ["900"]), (date(2026, 10, 2), ["-1000", "-1050"])])
    assert e.status == "breached" and result(e, "drawdown").status == "fail"
    hard = run(NOT_VERIFIABLE, day_trades=[(date(2026, 10, 1), ["-600", "-500"])])
    assert hard.status == "breached" and result(hard, "daily_loss").status == "fail"


def test_a_soft_daily_limit_reached_today_pauses_and_is_at_risk_not_breached():
    today = date(2026, 10, 12)
    e = run(APEX_EOD_50K_PA, day_trades=[(date(2026, 10, 9), "500"), (today, "-1000")], now=at(today, 14))
    assert result(e, "daily_loss").status == "fail" and e.status == "at_risk"
    # Today's floor (49,500) is closer than the drawdown floor (48,500): it is the left end of the bar.
    assert e.spectrum.breach_kind == "daily_loss" and e.spectrum.breach_level == D("49500")


def test_contracts_open_at_the_same_time_are_added_up_and_micros_count_as_tenths():
    day = date(2026, 10, 1)
    overlapping = [
        Trade(at(day, 10), at(day, 10, 30), D("10"), "NQ", 3),
        Trade(at(day, 10, 10), at(day, 10, 20), D("10"), "MNQ", 15),  # 3 minis + 1.5 = 4.5 > 4
        Trade(at(day, 10, 30), at(day, 10, 40), D("10"), "NQ", 4),  # starts as the first one ends: 4
    ]
    lucid = LUCID_PRO_50K
    e = c.evaluate(lucid, c.AccountFacts(D("50000"), "funded"), overlapping, [], at(date(2026, 10, 12)))
    assert result(e, "contracts").status == "fail" and result(e, "contracts").current == "4.5 minis (45 micros)"


def test_a_trade_held_into_the_next_session_breaks_no_overnight():
    held = Trade(at(date(2026, 10, 1), 15), at(date(2026, 10, 1), 18, 30), D("50"))  # 18:30 is the next day's session
    e = c.evaluate(LUCID_PRO_50K, c.AccountFacts(D("50000"), "funded"), [held], [], at(date(2026, 10, 12)))
    assert result(e, "overnight").status == "fail" and e.status == "at_risk"


def test_close_to_the_floor_is_at_risk():
    e = run(LUCID_PRO_50K, day_trades=[(date(2026, 10, 1), "-1600")])  # 400 above a 48,000 floor: within 25%
    assert e.status == "at_risk" and result(e, "drawdown").status == "pass"


def test_no_trades_and_no_payouts():
    e = run(APEX_EOD_50K_PA)
    assert e.balance == D("50000") and e.status == "ok" and e.payout.next_number == 1
    assert result(e, "consistency").status == "n/a"


# ---- reading the descriptive columns --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schedule", "number", "expected"),
    [
        ("1:1500|2:1500|3:2000|4:2500|5:2500|6:3000", 3, ("amount", D("2000"))),
        ("1:1500|2:1500|3:2000|4:2500|5:2500|6:3000", 7, ("unknown", None)),
        ("1-3:1500|4+:uncapped", 2, ("amount", D("1500"))),
        ("1-3:1500|4+:uncapped", 9, ("uncapped", None)),
        ("1:1500|2:2000|3:2500|4+:3000", 5, ("amount", D("3000"))),
        ("all:1000 (flat; lesser of 50% of profit and cap)", 4, ("amount", D("1000"))),
        ("all:600; up to 2x profit since last payout", 1, ("amount", D("600"))),
        ("per cycle:2000", 3, ("amount", D("2000"))),
        ("per cycle:unknown", 1, ("unknown", None)),
        ("uncapped; reviewed at $10K gross profit", 1, ("uncapped", None)),
        ("no max; daily profit cap $5000 (excess forfeited)", 1, ("uncapped", None)),
        ("no cap stated", 1, ("unknown", None)),
        (
            "scales with size and cycle (50K example: $100-$1,250 first cycle rising toward $2,000)",
            1,
            ("unknown", None),
        ),
        ("standard 1500; optional bonus payout adds 1500 again on payouts 2-4", 2, ("amount", D("1500"))),
        ("", 1, ("absent", None)),
    ],
)
def test_cap_schedules(schedule, number, expected):
    cap = c.cap_for(schedule, number)
    assert (cap.kind, cap.amount) == expected


def test_a_percentage_written_into_the_cap_schedule_is_kept():
    cap = c.cap_for("50% of profit capped at 6000 until 30 Benchmark Days; then up to 100%", 1)
    assert cap.amount == D("6000") and cap.pct == (D("50"), "profit")


@pytest.mark.parametrize(
    ("text", "share"),
    [
        ("90/10", D("90")),
        ("80/20; 90/10 in Live", D("80")),
        ("100% of first $10,000, then 90/10", D("90")),
        ("50/50 on profit < $4K per account, then 80/20; 90/10 in Funded Live", D("50")),
        ("90/10 minus 3% processing fee", D("90")),
        ("55/45 or 80/20 (chosen at signup)", D("55")),
        ("up to 100% (sim)", None),
        ("100/0", D("100")),
        ("", None),
    ],
)
def test_profit_split_is_the_lowest_share_it_promises(text, share):
    assert c.split_share(text) == share


@pytest.mark.parametrize(
    ("text", "start", "level"),
    [
        ("start_balance_plus_100", "50000", D("50100")),
        ("start_balance_plus_100 (once EOD balance > 52100)", "50000", D("50100")),
        ("start_balance ($0 XFA balance)", "0", D("0")),
        ("50100", "50000", D("50100")),
        ("none (Tradovate); profit_target_balance (Rithmic/WealthCharts)", "50000", None),
        ("locks when EOD balance reaches drawdown + $100", "25000", D("25100")),
        ("something else", "50000", None),
    ],
)
def test_lock_levels(text, start, level):
    assert c.lock_level(text, D(start), D("3000")) == level


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("locks_at_start_plus_100 immediately on payout request", "lock_start_plus_100"),
        ("resets_to_zero (MLL set to $0 balance permanently after first payout)", "reset_zero"),
        ("locks_at_start (1st withdrawal resets MLL to initial balance)", "lock_start"),
        ("static at start+100 (50100) after 1st payout", "lock_start_plus_100"),
        ("becomes static after first payout request", "freeze"),
        ("unchanged (MLL does not move after payout)", "unchanged"),
        ("", "unchanged"),
    ],
)
def test_after_payout_modes(text, mode):
    assert c.after_payout_mode(text) == mode


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("daily", (True, None, None, None, True)),
        ("every 14 calendar days (eligible 14 days after first trade)", (True, 14, None, None, False)),
        ("every 10 trading days", (True, None, 10, None, False)),
        ("at least 8 trading days between requests", (True, None, 8, None, False)),
        ("every 5 winning days (non-consecutive); up to 4 requests per month", (True, None, None, 4, False)),
        ("every 5 new qualifying days (up to weekly)", (True, 7, None, None, False)),
        ("per cycle; first request 48h after first trade; >=2 trading days per cycle", (True, None, 2, None, False)),
        ("something the reader does not know", (False, None, None, None, False)),
    ],
)
def test_payout_cadence(text, expected):
    rule = c.cadence(text)
    assert (rule.recognised, rule.calendar_gap, rule.trading_gap, rule.per_month, rule.once_a_day) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "at least 2 trading days with $50+ net profit in every rolling 30-day window; dormant from day 15; closed at day 30",
            ("rolling", 30, 2, D("50")),
        ),
        (
            "promo Legacy accounts bought after 2026-03-01: 2 days $50+ per rolling 30 days; older: unknown",
            ("rolling", 30, 2, D("50")),
        ),
        ("no trade with net P&L of at least $1 in 30 calendar days -> account deleted", ("every", 30, 1, D("1"))),
        ("one trade every 7 calendar days (funded)", ("every", 7, 1, None)),
        (
            "at least one trade per calendar week per funded account or closed and pending payouts voided",
            ("every", 7, 1, None),
        ),
        ("trade at least once every 10 trading days (eval and qualified) or data archived", ("every", 10, 1, None)),
        ("30 calendar day evaluation access window", ("window", 30, 1, None)),
        ("30-day access window", ("window", 30, 1, None)),
        ("7 calendar days", ("every", 7, 1, None)),
        ("no time limit (challenge)", ("none", 0, 1, None)),
        (
            "Tradovate archives accounts not logged into for a calendar month (counts as failure)",
            ("unreadable", 0, 1, None),
        ),
    ],
)
def test_inactivity_rules(text, expected):
    rule = c.inactivity(text)
    assert (rule.kind, rule.window, rule.count, rule.min_profit) == expected


def test_money_and_percentages_read_like_a_person_writes_them():
    assert c.money(D("-1234.5")) == "-$1,234.50" and c.money(D("0")) == "$0.00"
    assert c.pct(D("38.888")) == "38.9%" and c.pct(D("50")) == "50%"


# ---- edges the mutation check asked for ---------------------------------------------------------------------------


def test_the_trailing_floor_stops_exactly_at_the_lock():
    e = run(LUCID_PRO_50K, day_trades=[(date(2026, 10, 1), "2100.50")])
    assert e.floor == D("50100")  # 52,100.50 - 2,000 would be 50,100.50: one cent of it is past the lock


def test_an_end_of_day_floor_does_not_move_on_a_day_still_open():
    today = date(2026, 10, 12)
    e = run(LUCID_PRO_50K, day_trades=[(date(2026, 10, 9), "500"), (today, "800")], now=at(today, 14))
    assert e.floor == D("48500")  # yesterday's close of 50,500; today's 51,300 counts once the day closes


def test_the_payout_day_itself_belongs_to_the_cycle_before_it():
    days = list(zip(weekdays(date(2026, 9, 28), 5), ["600", "600", "600", "600", "600"]))
    paid = c.PayoutRecord(date(2026, 10, 2), D("1500"), D("1350"))  # the fifth day
    e = run(TRADEIFY_SELECT_FLEX_50K, day_trades=[*days, (date(2026, 10, 5), "300")], payouts=[paid])
    assert result(e, "qualifying_days").current == "1"  # only 10/05 is after the payout


def test_the_topstep_reset_puts_the_floor_at_exactly_zero():
    xfa = {**TOPSTEP_XFA_50K, "payout_cap_schedule": "all:2000"}
    paid = c.PayoutRecord(date(2026, 10, 5), D("750"), D("675"))
    e = run(xfa, start="0", day_trades=[*XFA_DAYS, (date(2026, 10, 6), "-750")], payouts=[paid])
    assert e.floor == D("0") and e.balance == D("0") and e.status == "breached"  # at the floor is a breach
