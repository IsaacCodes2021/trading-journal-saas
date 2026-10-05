"""The report builder (F-P4): how trades are grouped by contract, weekday, hour, hold time, tag and account. No database."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tradejournal.api.reports import HOLD_STEPS, WEEKDAYS, build_report, hold_step
from tradejournal.journal import Tag
from tradejournal.queries import TradeRecord

NEW_YORK = ZoneInfo("America/New_York")
MONDAY = date(2024, 3, 18)


def trade(
    net: str,
    *,
    n: int = 0,
    day: date = MONDAY,
    root: str = "NQ",
    account_id: int = 1,
    account_type: str = "evaluation",
    entry_hour_utc: int = 14,
    hold_seconds: int = 60,
    entered_on: date | None = None,
) -> TradeRecord:
    entered = entered_on or day  # a trade can be entered on the evening before its trading day
    entry_at = datetime(entered.year, entered.month, entered.day, entry_hour_utc, 0, tzinfo=UTC) + timedelta(seconds=n)
    return TradeRecord(
        id=n + 1,
        account_id=account_id,
        account_name=f"Account {account_id}",
        account_type=account_type,
        firm="Apex",
        symbol=f"{root}H4",
        symbol_root=root,
        contract_month=None,
        direction="long",
        entry_at=entry_at,
        exit_at=entry_at + timedelta(seconds=hold_seconds),
        trade_date=day,
        hold_seconds=hold_seconds,
        total_qty=1,
        max_position=1,
        fill_pair_count=1,
        tick_size=Decimal("0.25"),
        avg_entry=Decimal("100"),
        avg_exit=Decimal("101"),
        points=Decimal("1"),
        ticks=Decimal("4"),
        gross_pnl=Decimal(net),
        fees=Decimal("0"),
        net_pnl=Decimal(net),
        notes="",
    )


def report(by, trades, *, tags=None, group_names=None, timezone=NEW_YORK):
    return build_report(by, trades, tags or {}, group_names or {}, timezone).model_dump(mode="json")


def labels(body) -> list[str]:
    return [g["label"] for g in body["groups"]]


def nets(body) -> list[str]:
    return [g["summary"]["net_pnl"] for g in body["groups"]]


# ---- every kind of report on nothing ----------------------------------------------------------------------------------


@pytest.mark.parametrize("by", ["symbol", "account", "hour", "tag"])
def test_no_trades_means_no_groups_where_the_groups_depend_on_the_data(by):
    body = report(by, [])
    assert body["groups"] == [] and body["by"] == by
    assert body["summary"]["trade_count"] == 0 and body["summary"]["net_pnl"] == "0.00"


def test_weekday_and_hold_always_list_every_step_so_the_rows_line_up():
    weekdays = report("weekday", [])
    assert labels(weekdays) == list(WEEKDAYS) and [g["key"] for g in weekdays["groups"]] == list("0123456")
    holds = report("hold", [])
    assert labels(holds) == [label for label, _ in HOLD_STEPS] and len(holds["groups"]) == 7
    for group in weekdays["groups"] + holds["groups"]:  # an empty step has the dashboard's empty numbers, not nulls
        assert group["summary"]["trade_count"] == 0 and group["summary"]["net_pnl"] == "0.00"
        assert group["summary"]["win_rate"] is None and group["summary"]["expectancy"] is None


# ---- contract ---------------------------------------------------------------------------------------------------------


def test_the_symbol_report_groups_by_root_and_lists_the_best_result_first():
    trades = [
        trade("-30.00", n=0, root="ES"),
        trade("50.00", n=1, root="NQ"),
        trade("20.00", n=2, root="CL"),
        trade("-10.00", n=3, root="NQ"),
        trade("5.00", n=4, root="MNQ"),
    ]
    body = report("symbol", trades)
    assert labels(body) == ["NQ", "CL", "MNQ", "ES"]
    assert nets(body) == ["40.00", "20.00", "5.00", "-30.00"]
    assert [g["key"] for g in body["groups"]] == ["NQ", "CL", "MNQ", "ES"]
    assert all(g["detail"] is None for g in body["groups"])
    nq = body["groups"][0]["summary"]
    assert (nq["trade_count"], nq["win_count"], nq["loss_count"]) == (2, 1, 1)
    assert nq["win_rate"] == pytest.approx(0.5) and nq["expectancy"] == "20.00"


def test_a_tie_is_broken_by_name_ignoring_capitals_so_the_order_never_changes():
    trades = [trade("10.00", n=0, root="NQ"), trade("10.00", n=1, root="ES"), trade("10.00", n=2, root="cl")]
    assert labels(report("symbol", trades)) == ["cl", "ES", "NQ"]  # not ES, NQ, cl: capitals do not come first
    assert labels(report("symbol", list(reversed(trades)))) == ["cl", "ES", "NQ"]


def test_two_groups_with_the_same_name_and_the_same_result_keep_a_fixed_order_by_their_key():
    one, other = tag(10, "Good", 1), tag(11, "Good", 2)
    trades = [trade("5.00", n=0), trade("5.00", n=1)]
    names = {1: "Entry", 2: "Exit"}
    assert [g["key"] for g in report("tag", trades, tags={1: [other], 2: [one]}, group_names=names)["groups"]] == [
        "10",
        "11",
    ]
    assert [g["key"] for g in report("tag", trades, tags={1: [one], 2: [other]}, group_names=names)["groups"]] == [
        "10",
        "11",
    ]


def test_the_whole_report_total_is_the_dashboards_summary_of_every_trade():
    trades = [trade("50.00", n=0), trade("-20.00", n=1, root="ES"), trade("0.00", n=2, root="CL")]
    total = report("symbol", trades)["summary"]
    assert (total["trade_count"], total["net_pnl"], total["breakeven_count"]) == (3, "30.00", 1)


# ---- account ----------------------------------------------------------------------------------------------------------


def test_the_account_report_names_the_firm_and_the_kind_of_account():
    trades = [
        trade("-5.00", n=0, account_id=1),
        trade("25.00", n=1, account_id=2, account_type="funded"),
        trade("25.00", n=2, account_id=3),
    ]
    body = report("account", trades)
    assert labels(body) == ["Account 2", "Account 3", "Account 1"]  # 25, 25 (tie: by name), -5
    assert [g["detail"] for g in body["groups"]] == ["Apex · Funded", "Apex · Evaluation", "Apex · Evaluation"]
    assert [g["key"] for g in body["groups"]] == ["2", "3", "1"]
    assert all(g["summary"]["account_count"] == 1 for g in body["groups"])


# ---- weekday ----------------------------------------------------------------------------------------------------------


def test_the_weekday_report_goes_by_the_trading_day_monday_first_and_lists_all_seven():
    trades = [
        trade("10.00", n=0, day=date(2024, 3, 18)),  # a Monday
        trade("-4.00", n=1, day=date(2024, 3, 22)),  # a Friday
        trade("6.00", n=2, day=date(2024, 3, 22)),
        trade("3.00", n=3, day=date(2024, 3, 24)),  # a Sunday
    ]
    body = report("weekday", trades)
    assert labels(body) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert [g["summary"]["trade_count"] for g in body["groups"]] == [1, 0, 0, 0, 2, 0, 1]
    assert nets(body) == ["10.00", "0.00", "0.00", "0.00", "2.00", "0.00", "3.00"]


def test_a_trade_after_the_evening_open_counts_for_its_trading_day_not_the_calendar_day():
    # Globex opens at 18:00 New York (22:00 UTC) on the evening before: this one was entered at 23:00 UTC on Monday the
    # 18th, which is the start of Tuesday the 19th's trading day.
    late = trade("10.00", day=date(2024, 3, 19), entered_on=date(2024, 3, 18), entry_hour_utc=23)
    assert (
        late.entry_at.weekday() == 0 and late.trade_date.weekday() == 1
    )  # the setup: Monday by the clock, Tuesday by day
    body = report("weekday", [late])
    assert [g["summary"]["trade_count"] for g in body["groups"]] == [0, 1, 0, 0, 0, 0, 0]


# ---- hour -------------------------------------------------------------------------------------------------------------


def test_the_hour_is_when_the_trade_was_entered_in_the_persons_own_timezone_earliest_first():
    trades = [
        trade("10.00", n=0, entry_hour_utc=14),  # 10:00 in New York (summer time: UTC-4 on this date)
        trade("-3.00", n=1, entry_hour_utc=14),
        trade("7.00", n=2, entry_hour_utc=19),  # 15:00
        trade("1.00", n=3, entry_hour_utc=2),  # 22:00 the evening before
    ]
    body = report("hour", trades)
    assert labels(body) == ["10:00", "15:00", "22:00"]
    assert [g["key"] for g in body["groups"]] == ["10", "15", "22"]
    assert [g["summary"]["trade_count"] for g in body["groups"]] == [2, 1, 1]
    utc = report("hour", trades, timezone=UTC)
    assert labels(utc) == ["02:00", "14:00", "19:00"]


def test_hours_are_listed_earliest_first_whatever_order_the_trades_came_in():
    trades = [
        trade("1.00", n=0, entry_hour_utc=19),
        trade("1.00", n=1, entry_hour_utc=9),
        trade("1.00", n=2, entry_hour_utc=14),
    ]
    assert labels(report("hour", trades, timezone=UTC)) == ["09:00", "14:00", "19:00"]


def test_the_hour_changes_with_daylight_saving_so_the_same_utc_time_is_not_always_the_same_hour():
    winter = trade("1.00", day=date(2024, 1, 8), entry_hour_utc=14)  # UTC-5: 09:00
    summer = trade("1.00", n=1, day=date(2024, 7, 8), entry_hour_utc=14)  # UTC-4: 10:00
    assert labels(report("hour", [winter, summer])) == ["09:00", "10:00"]


def test_the_hour_is_zero_padded_and_midnight_is_00():
    body = report("hour", [trade("1.00", entry_hour_utc=4)], timezone=UTC)  # 04:00
    assert labels(body) == ["04:00"]
    assert labels(report("hour", [trade("1.00", entry_hour_utc=0)], timezone=UTC)) == ["00:00"]


# ---- hold time --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "step"),
    [
        (0, "Under 1 minute"),
        (59, "Under 1 minute"),
        (60, "1 to 5 minutes"),  # a step includes its lower edge
        (299, "1 to 5 minutes"),
        (300, "5 to 15 minutes"),
        (899, "5 to 15 minutes"),
        (900, "15 to 30 minutes"),
        (1799, "15 to 30 minutes"),
        (1800, "30 to 60 minutes"),
        (3599, "30 to 60 minutes"),
        (3600, "1 to 2 hours"),
        (7199, "1 to 2 hours"),
        (7200, "Over 2 hours"),
        (86_400 * 3, "Over 2 hours"),
    ],
)
def test_a_hold_time_falls_in_the_step_that_includes_its_lower_edge(seconds, step):
    assert HOLD_STEPS[hold_step(seconds)][0] == step
    body = report("hold", [trade("1.00", hold_seconds=seconds)])
    assert [g["label"] for g in body["groups"] if g["summary"]["trade_count"]] == [step]


def test_the_hold_steps_are_in_order_and_cover_every_length():
    ends = [end for _, end in HOLD_STEPS]
    assert ends[-1] is None and ends[:-1] == sorted(e for e in ends[:-1] if e is not None)
    assert len(set(ends)) == len(ends)


def test_the_hold_report_adds_up_each_step():
    trades = [
        trade("5.00", n=0, hold_seconds=30),
        trade("-2.00", n=1, hold_seconds=45),
        trade("9.00", n=2, hold_seconds=600),
    ]
    body = report("hold", trades)
    assert [g["summary"]["trade_count"] for g in body["groups"]] == [2, 0, 1, 0, 0, 0, 0]
    assert nets(body) == ["3.00", "0.00", "9.00", "0.00", "0.00", "0.00", "0.00"]


# ---- tags -------------------------------------------------------------------------------------------------------------


def tag(id: int, name: str, group_id: int = 1) -> Tag:
    return Tag(id=id, group_id=group_id, name=name, color="#112233")


def test_a_trade_counts_once_for_each_tag_it_has_and_a_trade_with_none_is_untagged():
    a_plus, fomo, news = tag(10, "A+ setup", 1), tag(11, "FOMO", 2), tag(12, "News", 2)
    trades = [trade("30.00", n=0), trade("-10.00", n=1), trade("4.00", n=2), trade("-7.00", n=3)]  # trade ids 1 to 4
    tags = {1: [a_plus, news], 2: [fomo], 3: [fomo, news]}  # the fourth has no tag
    body = report("tag", trades, tags=tags, group_names={1: "Setup", 2: "Mistakes"})
    assert labels(body) == ["News", "A+ setup", "FOMO", "Untagged"]
    assert nets(body) == ["34.00", "30.00", "-6.00", "-7.00"]
    assert [g["summary"]["trade_count"] for g in body["groups"]] == [2, 1, 2, 1]
    assert (
        body["summary"]["trade_count"] == 4 and body["summary"]["net_pnl"] == "17.00"
    )  # the total is not double counted


def test_a_tag_shows_its_category_and_the_untagged_group_has_none():
    trades = [trade("1.00", n=0), trade("2.00", n=1)]
    body = report("tag", trades, tags={1: [tag(10, "A+ setup", 7)]}, group_names={7: "Setup"})
    by_label = {g["label"]: g for g in body["groups"]}
    assert by_label["A+ setup"]["detail"] == "Setup" and by_label["A+ setup"]["key"] == "10"
    assert by_label["Untagged"]["detail"] is None and by_label["Untagged"]["key"] == "untagged"


def test_two_tags_with_the_same_name_in_different_categories_stay_apart():
    trades = [trade("5.00", n=0), trade("-1.00", n=1)]
    body = report(
        "tag",
        trades,
        tags={1: [tag(10, "Good", 1)], 2: [tag(11, "Good", 2)]},
        group_names={1: "Entry", 2: "Exit"},
    )
    assert [(g["label"], g["detail"], g["summary"]["net_pnl"]) for g in body["groups"]] == [
        ("Good", "Entry", "5.00"),
        ("Good", "Exit", "-1.00"),
    ]


def test_tags_are_ignored_by_every_other_report():
    trades = [trade("5.00", n=0)]
    assert labels(report("symbol", trades, tags={1: [tag(10, "A+")]})) == ["NQ"]
