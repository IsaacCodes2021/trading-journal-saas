from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tradejournal import finance

D = Decimal
DAY = date(2024, 1, 5)


@dataclass(frozen=True)
class Result:
    net_pnl: Decimal
    trade_date: date = DAY
    fees: Decimal = D("0")

    @property
    def gross_pnl(self) -> Decimal:
        return self.net_pnl + self.fees


# Net P&L of the sample file's eight trades at $4 per contract round trip, in exit order.
SAMPLE_NETS = ["-495", "605", "-945", "-420", "130", "1175", "630", "155"]
SAMPLE_FEES = ["20", "20", "20", "20", "20", "40", "40", "20"]
SAMPLE = [Result(D(n), fees=D(f)) for n, f in zip(SAMPLE_NETS, SAMPLE_FEES)]


def test_summary_of_sample_day():
    s = finance.summarize(SAMPLE)
    assert (s.trade_count, s.win_count, s.loss_count, s.breakeven_count) == (8, 5, 3, 0)
    assert (s.gross_pnl, s.fees, s.net_pnl) == (D("1035"), D("200"), D("835"))
    assert (s.total_won, s.total_lost) == (D("2695"), D("-1860"))
    assert s.win_rate == D("0.625")
    assert s.profit_factor == D("2695") / D("1860")
    assert (s.avg_win, s.avg_loss) == (D("539"), D("-620"))
    assert s.win_loss_ratio == D("539") / D("620")
    assert s.expectancy == D("104.375")
    assert (s.largest_win, s.largest_loss) == (D("1175"), D("-945"))
    assert (s.trading_days, s.winning_days, s.losing_days, s.day_win_rate) == (1, 1, 0, D("1"))
    assert s.max_drawdown == D("1365")  # from +110 down to -1255


def test_equity_curve_peak_starts_at_zero():
    curve = finance.equity_curve([D("-50"), D("80"), D("-20"), D("10")])
    assert [p.equity for p in curve] == [D("-50"), D("30"), D("10"), D("20")]
    assert [p.drawdown for p in curve] == [D("-50"), D("0"), D("-20"), D("-10")]
    assert finance.max_drawdown([D("-50"), D("80"), D("-20")]) == D("50")
    assert finance.max_drawdown([]) == D("0")


def test_breakeven_trades_do_not_count_toward_win_rate():
    s = finance.summarize([Result(D("100")), Result(D("0")), Result(D("-50"))])
    assert s.breakeven_count == 1
    assert s.win_rate == D("0.5")
    assert s.expectancy == D("50") / 3


def test_daily_results_and_day_win_rate():
    trades = [
        Result(D("100"), date(2024, 1, 2)),
        Result(D("-30"), date(2024, 1, 2)),
        Result(D("-80"), date(2024, 1, 3)),
        Result(D("0"), date(2024, 1, 4)),
        Result(D("40"), date(2024, 1, 5)),
    ]
    days = finance.daily_results(trades)
    assert [(d.day.day, d.net_pnl, d.trade_count) for d in days] == [
        (2, D("70"), 2), (3, D("-80"), 1), (4, D("0"), 1), (5, D("40"), 1),
    ]  # fmt: skip
    s = finance.summarize(trades)
    assert (s.trading_days, s.winning_days, s.losing_days) == (4, 2, 1)
    assert s.day_win_rate == D("2") / D("3")  # the flat day counts neither way


def test_empty_summary_and_score():
    s = finance.summarize([])
    assert s.trade_count == 0
    assert s.win_rate is None and s.profit_factor is None and s.expectancy is None
    assert s.max_drawdown == D("0")
    assert finance.performance_score(s, []) is None


def test_performance_score_of_sample_day():
    s = finance.summarize(SAMPLE)
    score = finance.performance_score(s, finance.daily_results(SAMPLE))
    by_name = {axis.name: axis.score for axis in score.axes}
    assert by_name == {
        "Win rate": D("100.0"),  # 62.5% is above the 60% cap
        "Profit factor": D("29.9"),  # (1.449 - 1) / 1.5
        "Avg win/loss": D("34.8"),  # 0.869 / 2.5
        "Consistency": D("0.0"),  # one winning day made all the profit
        "Max drawdown": D("49.4"),  # gave back 1365 of 2695 won
        "Recovery factor": D("20.4"),  # 835 / 1365 = 0.61, out of 3
    }
    assert score.overall == D("39.1")
    assert all(axis.rule == finance.SCORE_RULES[axis.name] for axis in score.axes)


def test_performance_score_without_losses_or_drawdown():
    trades = [Result(D("100"), date(2024, 1, d)) for d in range(1, 6)]
    s = finance.summarize(trades)
    score = finance.performance_score(s, finance.daily_results(trades))
    by_name = {axis.name: axis.score for axis in score.axes}
    assert by_name["Profit factor"] == D("100.0")  # undefined PF with only wins
    assert by_name["Avg win/loss"] == D("100.0")
    assert by_name["Max drawdown"] == D("100.0")
    assert by_name["Recovery factor"] == D("100.0")
    assert by_name["Consistency"] == D("100.0")  # best day is 20% of the profit


def test_performance_score_all_losses():
    trades = [Result(D("-100")), Result(D("-50"))]
    s = finance.summarize(trades)
    score = finance.performance_score(s, finance.daily_results(trades))
    assert {axis.score for axis in score.axes} == {D("0.0")}
    assert score.overall == D("0.0")
