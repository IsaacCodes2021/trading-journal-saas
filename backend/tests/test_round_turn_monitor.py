"""The Round Turn Monitor's log (NinjaTrader 8), read against a real one.

The golden figures are the add-on's own for one Lucid account on the 2026-11-18 trading day: 18 round turns, +$1,395.00
gross, $28.00 commission, +$1,367.00 net, matching its running `day_pnl_usd_net`. The log also holds 5 Sim101 round
turns, one of them in MNQ. The test copy replaces the real account number. See
docs/design/round-turn-monitor-import-plan.md, section 1.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tradejournal.grouping import group_fill_pairs
from tradejournal.pairing import pair_fifo
from tradejournal.platforms import round_turn_monitor as rtm

D = Decimal
FIXTURES = Path(__file__).parent / "fixtures"
LOG = "rtm_log_2026-11-18.csv"
EXPORT = "rtm_export_new_2026-11-18.csv"  # the updated add-on's Export file: the log's columns, one account's day
OLD_EXPORT = "rtm_export_2026-11-18.csv"  # the add-on's Export file before that: 15 columns, no contract or commission
TZ = "America/Los_Angeles"
LUCID = "LFE0000000000001"  # stands in for the real account number
HEADER = ",".join(rtm.COLUMNS)


def log_bytes(name=LOG):
    return (FIXTURES / name).read_bytes()


def row(entry="06:30:18.368", exit_="06:30:20.718", duration="2.350", *, day="2026-11-18", account=LUCID,
        instrument="NQ 12-26", direction="Long", qty="1", held="1", entry_price="27253.75", exit_price="27227.00",
        gross="-535.00", commission="3.50"):  # fmt: skip
    cells = (day, account, instrument, direction, qty, held, entry, exit_, duration, entry_price, exit_price,
             "1", "1", "1", "1", "1", gross, commission, "0", "0", "0", "no")  # fmt: skip
    return ",".join(cells)


def log(*rows, header=HEADER):
    return "\r\n".join([header, *rows, ""]).encode()


def read(*rows):
    return rtm.read_log(log(*rows), TZ)


# ---- the real log ---------------------------------------------------------------------------------------------------


def test_the_real_log_gives_the_add_ons_own_day_for_each_account():
    read_log = rtm.read_log(log_bytes(), TZ)
    assert read_log.fatal is None and read_log.errors == [] and read_log.warnings == []
    assert read_log.accounts() == {"Sim101": 5, LUCID: 18}
    lucid = read_log.for_account(LUCID)
    assert lucid.rows == 18 and len(lucid.executions) == 36 and lucid.errors == []
    assert [e.fill_id for e in lucid.executions] == list(range(1, 37))
    assert lucid.commission == D("28.00")  # the add-on's commission, kept to compare with the fee setting
    # The first 11 round turns show none (the add-on's rate was set part-way through): the usual rate is the one charged.
    assert lucid.rates == {"NQ": D("3.50")}
    paired = pair_fifo(lucid.executions)
    assert paired.open_lots == [] and len(paired.pairs) == 18
    assert sum(p.pnl for p in paired.pairs) == D("1395.00")  # the add-on's gross for the day, to the cent
    assert len(group_fill_pairs(paired.pairs)) == 18  # every round turn is its own trade
    assert {p.symbol for p in paired.pairs} == {"NQZ6"}

    sim = read_log.for_account("Sim101")
    sim_pairs = pair_fifo(sim.executions).pairs
    assert {p.symbol for p in sim_pairs} == {"MNQZ6", "NQZ6"} and len(sim_pairs) == 5
    assert sum(p.pnl for p in sim_pairs) == D("4748.50")
    assert sim.commission == D("226.54")
    assert sim.rates == {"MNQ": D("1.90"), "NQ": D("5.76")}


def test_times_are_read_in_the_setting_and_the_exit_keeps_the_logs_milliseconds():
    first = rtm.read_log(log_bytes(), TZ).for_account(LUCID).executions[:2]
    # 2026-11-17 22:29:58.128 in Pacific time; the exit is 146.399 seconds later, at 22:32:24.527 as the log writes.
    assert first[0].executed_at == datetime(2026, 11, 18, 6, 29, 58, 128000, tzinfo=UTC)
    assert first[1].executed_at == datetime(2026, 11, 18, 6, 32, 24, 527000, tzinfo=UTC)
    assert (first[0].side, first[1].side, first[0].price, first[1].price) == (
        "buy",
        "sell",
        D("27218.75"),
        D("27223.75"),
    )
    # The Sim101 MNQ row: 88.934 seconds after 22:21:47.450 reckons 22:23:16.384; the log's own 16.383 is used.
    mnq = rtm.read_log(log_bytes(), TZ).for_account("Sim101").executions[1]
    assert mnq.symbol == "MNQZ6" and mnq.executed_at == datetime(2026, 11, 18, 6, 23, 16, 383000, tzinfo=UTC)
    in_new_york = rtm.read_log(log_bytes(), "America/New_York").for_account(LUCID).executions[0]
    assert in_new_york.executed_at == datetime(2026, 11, 18, 3, 29, 58, 128000, tzinfo=UTC)


def test_a_round_turn_over_midnight_exits_on_the_next_day():
    [entry, exit_] = read(row("23:59:50.000", "00:00:10.250", "20.250", day="2026-11-17")).for_account(LUCID).executions
    assert entry.executed_at == datetime(2026, 11, 18, 7, 59, 50, tzinfo=UTC)
    assert exit_.executed_at == datetime(2026, 11, 18, 8, 0, 10, 250000, tzinfo=UTC)


def test_an_exit_time_that_disagrees_with_the_duration_is_a_warning_and_the_duration_wins():
    turns = read(row("06:30:18.368", "06:35:00.000", "2.350"))
    [(owner, warning)] = turns.warnings
    assert owner == LUCID and "exit_time_local 06:35:00.000" in warning.message and warning.line == 2
    assert turns.round_turns[0].exit.executed_at == datetime(2026, 11, 18, 14, 30, 20, 718000, tzinfo=UTC)


def test_a_short_sells_first_and_buys_back():
    [entry, exit_] = (
        read(row(direction="Short", entry_price="27130.25", exit_price="27125.25", gross="100.00"))
        .for_account(LUCID)
        .executions
    )
    assert (entry.side, entry.price, exit_.side, exit_.price) == ("sell", D("27130.25"), "buy", D("27125.25"))
    [pair] = pair_fifo([entry, exit_]).pairs
    assert (pair.direction, pair.pnl, pair.buy_price, pair.sell_price) == (
        "short",
        D("100.00"),
        D("27125.25"),
        D("27130.25"),
    )


# ---- prices, money and contracts -------------------------------------------------------------------------------------


def test_an_average_rounded_to_six_decimals_is_made_exact_again():
    # Three contracts filled at 27420.75, 27420.75 and 27421.00 average 27420.8333..., which the add-on writes rounded.
    turns = read(row(qty="3", held="3", entry_price="27420.833333", exit_price="27430.833333", gross="600.00"))
    entry = turns.round_turns[0].entry
    assert entry.price == D("27420.8333333333") and turns.warnings == []
    assert pair_fifo(turns.for_account(LUCID).executions).pairs[0].pnl == D("600.00")


def test_a_gross_that_the_prices_do_not_make_is_a_warning():
    turns = read(row(gross="-500.00"))
    [(_, warning)] = turns.warnings
    assert (
        warning.message
        == "gross_result_usd -500 is not what the prices make (-535 for 1 contract(s)); Roundturn uses -535"
    )
    assert read(row(gross="-534.99")).warnings == []  # a cent of rounding is not a difference


def test_the_round_turns_commission_is_split_over_its_two_orders():
    for total, entry, exit_ in (("3.50", "1.75", "1.75"), ("1.90", "0.95", "0.95"), ("0.01", "0.00", "0.01")):
        turn = read(row(commission=total)).round_turns[0]
        assert (turn.entry.commission, turn.exit.commission) == (D(entry), D(exit_))
    turn = read(row(commission="")).round_turns[0]
    assert turn.entry.commission is None and turn.exit.commission is None


@pytest.mark.parametrize(
    ("instrument", "symbol"),
    [("NQ 12-26", "NQZ6"), ("MNQ 03-27", "MNQH7"), ("ES 6-27", "ESM7"), ("cl 01-27", "CLF7"), (" 6E 09-26 ", "6EU6")],
)
def test_ninjatraders_contract_names_are_stored_the_way_tradovate_writes_them(instrument, symbol):
    assert rtm.symbol_of(instrument) == symbol


@pytest.mark.parametrize("instrument", ["NQ", "AAPL", "NQ ##-##", "NQ 13-26", "EURUSD"])
def test_an_instrument_that_is_not_a_dated_futures_contract_is_refused(instrument):
    with pytest.raises(ValueError):
        rtm.symbol_of(instrument)


def test_bad_rows_are_skipped_with_their_line_and_belong_to_their_account():
    turns = read(
        row(),
        row(instrument="ZZ 12-26", account="Sim101"),
        row(direction="Flat"),
        row(qty="0"),
        row(entry="25:00:00.000"),
        row(duration="-1"),
        row(entry_price="0"),
        row(account=""),
    )
    assert len(turns.round_turns) == 1
    problems = {issue.line: (owner, issue.message) for owner, issue in turns.errors}
    assert problems[3] == (
        "Sim101",
        "the contract ZZ 12-26 is not in Roundturn's contract list yet, so its P&L cannot be worked out",
    )
    assert problems[4][1] == "direction must be Long or Short, got 'Flat'"
    assert problems[5][1] == "contracts_traded: not a positive whole number: '0'"
    assert problems[6][1] == "entry_time_local: not understood: '25:00:00.000'"
    assert problems[7][1] == "duration_seconds is negative: '-1'"
    assert problems[8][1] == "avg_entry_price must be positive, got '0'"
    assert problems[9] == ("", "account is empty")
    # An account whose every row was refused is still listed, so its problems can be shown when it is chosen.
    assert turns.accounts() == {LUCID: 1, "Sim101": 0}
    assert [i.line for i in turns.for_account(LUCID).errors] == [4, 5, 6, 7, 8, 9]
    assert [i.line for i in turns.for_account("Sim101").errors] == [3, 9]


# ---- ordering --------------------------------------------------------------------------------------------------------


def test_at_one_moment_an_exit_comes_before_the_next_entry_so_a_reversal_is_two_trades():
    reversal = read(
        row("06:30:00.000", "06:30:05.000", "5.000", entry_price="27000", exit_price="27005", gross="100.00"),
        row(
            "06:30:05.000",
            "06:30:09.000",
            "4.000",
            direction="Short",
            entry_price="27005",
            exit_price="27001",
            gross="80.00",
        ),  # fmt: skip
    ).for_account(LUCID)
    assert [(e.side, e.price) for e in reversal.executions] == [
        ("buy", D("27000")), ("sell", D("27005")), ("sell", D("27005")), ("buy", D("27001")),
    ]  # fmt: skip
    pairs = pair_fifo(reversal.executions).pairs
    assert [(p.direction, p.pnl) for p in pairs] == [("long", D("100.00")), ("short", D("80.00"))]
    assert len(group_fill_pairs(pairs)) == 2

    again = read(
        row("06:30:00.000", "06:30:05.000", "5.000", entry_price="27000", exit_price="27005", gross="100.00"),
        row("06:30:05.000", "06:30:09.000", "4.000", entry_price="27005", exit_price="27006", gross="20.00"),
    ).for_account(LUCID)
    pairs = pair_fifo(again.executions).pairs
    assert len(group_fill_pairs(pairs)) == 2  # flat in between, even within one millisecond


def test_a_round_turn_inside_one_millisecond_is_still_entered_before_it_is_exited():
    [entry, exit_] = read(row("06:30:00.000", "06:30:00.000", "0.000", gross="-535.00")).for_account(LUCID).executions
    assert (entry.side, exit_.side, entry.executed_at == exit_.executed_at) == ("buy", "sell", True)
    [pair] = pair_fifo([entry, exit_]).pairs
    assert pair.direction == "long"


def test_the_log_and_its_overflow_file_are_read_together_and_a_row_in_both_counts_once():
    early = row("06:30:18.368", "06:30:20.718", "2.350")
    later = row("06:40:00.000", "06:40:02.350", "2.350")
    turns = rtm.read_log_files([("RoundTurnLog.csv", log(later, early)), ("RoundTurnLog_overflow.csv", log(early))], TZ)
    assert [t.entry.executed_at.minute for t in turns.round_turns] == [30, 40]  # in time order
    assert turns.duplicates == {LUCID: 1} and turns.for_account(LUCID).duplicates_across_files == 1
    # The same round turn always gets the same references, so choosing a longer copy of the log adds only new ones.
    assert rtm.read_log(log(early), TZ).round_turns[0].entry.ref == turns.round_turns[0].entry.ref
    assert turns.round_turns[0].entry.ref.endswith(":in") and turns.round_turns[0].exit.ref.endswith(":out")
    assert rtm.read_log(log(row(account="Sim101")), TZ).round_turns[0].entry.ref != turns.round_turns[0].entry.ref


# ---- the Export button's file -----------------------------------------------------------------------------------------


def test_an_export_file_is_read_like_the_log_and_its_round_turns_are_the_logs_own():
    export = rtm.read_log(log_bytes(EXPORT), TZ)
    assert export.fatal is None and export.errors == [] and export.warnings == []
    assert export.accounts() == {LUCID: 18}  # one account's day: nothing to choose
    lucid = export.for_account(LUCID)
    assert lucid.rows == 18 and lucid.commission == D("28.00") and lucid.rates == {"NQ": D("3.50")}
    # Its rows are the log's, character for character, so each round turn has the same references in both files.
    in_log = rtm.read_log(log_bytes(), TZ).for_account(LUCID)
    assert [e.ref for e in lucid.executions] == [e.ref for e in in_log.executions]
    assert [e.executed_at for e in lucid.executions] == [e.executed_at for e in in_log.executions]


def test_an_export_file_and_the_log_chosen_together_count_each_round_turn_once():
    both = rtm.read_log_files([("RoundTurnLog.csv", log_bytes()), ("RoundTurnExport.csv", log_bytes(EXPORT))], TZ)
    assert both.fatal is None and both.accounts() == {"Sim101": 5, LUCID: 18}
    assert both.duplicates == {LUCID: 18} and both.for_account(LUCID).duplicates_across_files == 18


# ---- the wrong file --------------------------------------------------------------------------------------------------


def test_an_export_file_from_the_older_add_on_is_refused_with_what_to_do():
    assert rtm.read_log(log_bytes(OLD_EXPORT), TZ).fatal == (
        "This Export file is from an older version of the Round Turn Monitor, without the contract or commission. "
        "Update the add-on and export again, or choose RoundTurnLog.csv (in Documents\\NinjaTrader 8\\RoundTurnLog)."
    )


def test_the_old_19_column_log_and_other_platforms_files_are_refused_in_words():
    assert rtm.read_log(log(header=",".join(rtm.OLD_COLUMNS)), TZ).fatal == rtm.OLD_LOG
    from helpers import SAMPLE, fixture_bytes

    assert rtm.read_log(fixture_bytes(SAMPLE), TZ).fatal == (
        "This is a Tradovate Performance export, not the Round Turn Monitor's log."
    )
    tradesea = fixture_bytes("tradesea_2026-11-18_page1_filled.csv")
    assert rtm.read_log(tradesea, TZ).fatal == "This is a TradeSea Orders export, not the Round Turn Monitor's log."
    assert rtm.read_log(b"name,age\n", TZ).fatal.startswith(
        "Not the Round Turn Monitor's log: missing column(s) date_local"
    )
    assert rtm.read_log(b"", TZ).fatal == "The file is empty."
    files = [("RoundTurnLog.csv", log_bytes()), ("old.csv", log(header=",".join(rtm.OLD_COLUMNS)))]
    assert rtm.read_log_files(files, TZ).fatal == f"old.csv: {rtm.OLD_LOG}"


def test_a_log_with_only_its_header_has_nothing_to_import():
    turns = rtm.read_log(log(), TZ)
    assert (turns.fatal, turns.round_turns, turns.accounts()) == (None, [], {})


def test_ninjatraders_practice_accounts_are_told_apart():
    for name in ("Sim101", "sim102", "Playback101", "Backtest", " SimFunded"):
        assert rtm.is_practice(name), name
    for name in (LUCID, "APEX-12345-01", "PA-APEX-1", "TOPSTEP1", "MySim"):
        assert not rtm.is_practice(name), name
