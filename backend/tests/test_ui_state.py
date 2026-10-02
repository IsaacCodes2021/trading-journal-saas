from datetime import date

from tradejournal.ui.state import FilterState, preset_range

TODAY = date(2024, 3, 13)


def test_preset_ranges():
    assert preset_range("all", TODAY) == (None, None)
    assert preset_range("7d", TODAY) == (date(2024, 3, 7), TODAY)
    assert preset_range("30d", TODAY) == (date(2024, 2, 13), TODAY)
    assert preset_range("mtd", TODAY) == (date(2024, 3, 1), TODAY)
    assert preset_range("last_month", TODAY) == (date(2024, 2, 1), date(2024, 2, 29))
    assert preset_range("last_month", date(2024, 1, 15)) == (date(2023, 12, 1), date(2023, 12, 31))
    assert preset_range("ytd", TODAY) == (date(2024, 1, 1), TODAY)


def test_custom_preset_keeps_dates_and_filter_translation():
    state = FilterState(account_ids=[1, 2], symbol_roots=["NQ"], direction="short")
    state.apply_preset("mtd", TODAY)
    state.apply_preset("custom", TODAY)
    trade_filter = state.to_filter()
    assert (trade_filter.start, trade_filter.end) == (date(2024, 3, 1), TODAY)
    assert trade_filter.account_ids == (1, 2)
    assert trade_filter.symbol_roots == ("NQ",)
    assert trade_filter.direction == "short"
    assert FilterState().to_filter().direction is None


def test_account_type_view():
    assert FilterState().to_filter().account_type is None  # "All accounts"
    assert FilterState(account_type="funded").to_filter().account_type == "funded"
    assert FilterState(account_type="evaluation").to_filter().account_type == "evaluation"
