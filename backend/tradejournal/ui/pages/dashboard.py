"""Dashboard: headline statistics, equity curve, performance score, daily P&L."""

from __future__ import annotations

from nicegui import ui

from ... import finance
from ...accounts import ACCOUNT_TYPES, Account, list_accounts
from ...queries import load_trades
from .. import charts, fmt
from ..data import display_zone, is_dark, open_db
from ..filters import filter_bar
from ..layout import card, frame
from ..state import filters
from ..theme import palette
from ..widgets import stat_tile

# How each performance-score axis's measured value is displayed.
_AXIS_FORMAT = {
    "Win rate": fmt.percent,
    "Profit factor": fmt.ratio,
    "Avg win/loss": fmt.ratio,
    "Consistency": fmt.percent,
    "Max drawdown": fmt.percent,
    "Recovery factor": fmt.ratio,
}


@ui.page("/")
def dashboard_page() -> None:
    zone = display_zone()
    colors = palette(is_dark())

    @ui.refreshable
    def body() -> None:
        with open_db() as conn:
            accounts = list_accounts(conn)
            trades = load_trades(conn, filters.to_filter())
        if not trades:
            _empty_state(accounts)
            return

        summary = finance.summarize(trades)
        days = finance.daily_results(trades)
        score = finance.performance_score(summary, days)
        _stat_tiles(summary)

        with ui.element("div").classes("grid gap-4 w-full grid-cols-1 lg:grid-cols-3 items-start"):
            with card("Cumulative net P&L", "By trade, with drawdown from the running peak below").classes(
                "lg:col-span-2"
            ):
                ui.echart(charts.equity_options(trades, zone, colors)).classes("w-full h-[380px]")
            with card("Performance score", "Six measures, each scored 0–100"):
                _score_panel(score, colors)

        with card("Daily net P&L", f"{len(days)} trading day{'s' if len(days) != 1 else ''}"):
            ui.echart(charts.daily_pnl_options(days, colors)).classes("w-full h-[300px]")
            with ui.expansion("Show as table").classes("w-full tj-secondary").props("dense"):
                ui.table(
                    columns=[
                        {"name": "day", "label": "Trading day", "field": "day", "align": "left", "sortable": True},
                        {"name": "trades", "label": "Trades", "field": "trades", "align": "right", "sortable": True},
                        {"name": "net", "label": "Net P&L", "field": "net", "align": "right"},
                    ],
                    rows=[
                        {"day": d.day.isoformat(), "trades": d.trade_count, "net": fmt.money(d.net_pnl)}
                        for d in reversed(days)
                    ],
                    row_key="day",
                    pagination=15,
                ).classes("w-full tj-table").props("flat dense")

    with frame("dashboard"):
        filter_bar(on_change=body.refresh)
        body()


def _stat_tiles(s: finance.Summary) -> None:
    with ui.element("div").classes("grid gap-3 w-full grid-cols-2 md:grid-cols-3 xl:grid-cols-6"):
        stat_tile(
            "Net P&L",
            fmt.money(s.net_pnl),
            sub=f"Gross {fmt.money(s.gross_pnl)} · fees {fmt.money(s.fees)}",
            value_class=fmt.pnl_class(s.net_pnl),
            hero=True,
        )
        stat_tile(
            "Win rate",
            fmt.percent(s.win_rate),
            sub=f"{s.win_count} won · {s.loss_count} lost"
            + (f" · {s.breakeven_count} flat" if s.breakeven_count else ""),
        )
        stat_tile(
            "Profit factor",
            fmt.ratio(s.profit_factor),
            sub=f"Won {fmt.money(s.total_won)} · lost {fmt.money(-s.total_lost)}",
        )
        stat_tile(
            "Expectancy", fmt.money(s.expectancy), sub="Net P&L per trade", value_class=fmt.pnl_class(s.expectancy)
        )
        stat_tile(
            "Trades", f"{s.trade_count:,}", sub=f"Over {s.trading_days} trading day{'s' if s.trading_days != 1 else ''}"
        )
        stat_tile(
            "Day win rate",
            fmt.percent(s.day_win_rate),
            sub=f"{s.winning_days} green · {s.losing_days} red",
        )
        stat_tile("Average win", fmt.money(s.avg_win), value_class=fmt.pnl_class(s.avg_win))
        stat_tile("Average loss", fmt.money(s.avg_loss), value_class=fmt.pnl_class(s.avg_loss))
        stat_tile("Win/loss ratio", fmt.ratio(s.win_loss_ratio), sub="Average win ÷ average loss")
        stat_tile("Largest win", fmt.money(s.largest_win), value_class=fmt.pnl_class(s.largest_win))
        stat_tile("Largest loss", fmt.money(s.largest_loss), value_class=fmt.pnl_class(s.largest_loss))
        stat_tile(
            "Max drawdown",
            fmt.money(-s.max_drawdown),
            sub="Deepest drop from a peak",
            value_class=fmt.pnl_class(-s.max_drawdown),
        )


def _score_panel(score: finance.PerformanceScore, colors) -> None:
    with ui.row().classes("items-baseline gap-1"):
        ui.label(f"{score.overall:.1f}").classes("tj-value tj-hero")
        ui.label("/ 100").classes("tj-label")
    ui.echart(charts.radar_options(score, colors)).classes("w-full h-[260px]")
    with ui.expansion("How it's scored").classes("w-full tj-secondary").props("dense"):
        ui.table(
            columns=[
                {"name": "name", "label": "Measure", "field": "name", "align": "left"},
                {"name": "value", "label": "Yours", "field": "value", "align": "right"},
                {"name": "score", "label": "Score", "field": "score", "align": "right"},
            ],
            rows=[
                {"name": a.name, "value": _AXIS_FORMAT[a.name](a.value), "score": f"{a.score:.1f}"} for a in score.axes
            ],
            row_key="name",
        ).classes("w-full tj-table").props("flat dense hide-bottom")
        with ui.column().classes("gap-1 mt-2"):
            for axis in score.axes:
                ui.label(f"{axis.name}: {axis.rule}").classes("tj-label")
            ui.label("The overall score is the plain average of the six.").classes("tj-label")


def _empty_state(accounts: list[Account]) -> None:
    with card().classes("items-center py-12"):
        if not accounts:
            ui.label("Welcome! Start by importing a Tradovate Performance export.").classes("tj-title")
            ui.button("Import trades", icon="upload_file", on_click=lambda: ui.navigate.to("/import")).props(
                "unelevated no-caps color=primary"
            )
        elif filters.account_type != "all" and not any(a.account_type == filters.account_type for a in accounts):
            kind = ACCOUNT_TYPES[filters.account_type].lower()
            ui.label(f"No {kind} accounts yet.").classes("tj-title")
            ui.label(f"Mark an account as {kind} in Settings, or create one when importing.").classes("tj-label")
        elif not filters.show_archived and all(
            a.archived for a in accounts if filters.account_type in ("all", a.account_type)
        ):
            ui.label("Every account in this view is archived.").classes("tj-title")
            ui.label("Turn on Show archived above to see their trades, or restore an account in Settings.").classes(
                "tj-label"
            )
        else:
            ui.label("No trades match these filters.").classes("tj-title")
            ui.label("Widen the date range or clear a filter.").classes("tj-label")
