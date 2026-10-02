"""ECharts option builders.

Values arrive as Decimal and become floats only here, at the hand-off to the
browser, purely for drawing. Every figure shown as text is formatted from Decimal.
Keys starting with ":" are JavaScript that NiceGUI turns into functions.
"""

from __future__ import annotations

from datetime import tzinfo
from typing import Sequence

from ..finance import DailyResult, PerformanceScore, equity_curve
from ..queries import TradeRecord
from .theme import Palette

_MONEY_JS = (
    "const m = (v) => (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', "
    "{minimumFractionDigits: 2, maximumFractionDigits: 2});"
)
_AXIS_MONEY = (
    ":formatter",
    "(v) => (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', {maximumFractionDigits: 0})",
)


def _tooltip(p: Palette, **extra) -> dict:
    return {
        "backgroundColor": p.surface,
        "borderColor": p.axis,
        "borderWidth": 1,
        "textStyle": {"color": p.ink, "fontSize": 12},
        "extraCssText": "box-shadow: none; border-radius: 8px;",
        **extra,
    }


def _value_axis(p: Palette, grid_index: int = 0, **extra) -> dict:
    return {
        "type": "value",
        "gridIndex": grid_index,
        "axisLabel": {"color": p.muted, "fontSize": 11, "hideOverlap": True, _AXIS_MONEY[0]: _AXIS_MONEY[1]},
        "splitLine": {"lineStyle": {"color": p.grid, "width": 1}},
        "axisLine": {"show": False},
        **extra,
    }


def _category_axis(p: Palette, data: list[str], grid_index: int = 0, **extra) -> dict:
    return {
        "type": "category",
        "data": data,
        "gridIndex": grid_index,
        "axisLine": {"lineStyle": {"color": p.axis}},
        "axisTick": {"show": False},
        "axisLabel": {"color": p.muted, "fontSize": 11, "hideOverlap": True},
        **extra,
    }


def equity_options(trades: Sequence[TradeRecord], zone: tzinfo, p: Palette) -> dict:
    """Cumulative net P&L by trade (top) with its drawdown underneath (bottom).

    Two stacked panels sharing the trade axis, each with its own scale: never two
    scales on one plot.
    """
    curve = equity_curve(t.net_pnl for t in trades)
    points = []
    categories = []
    for index, (trade, point) in enumerate(zip(trades, curve), start=1):
        when = trade.exit_at.astimezone(zone).strftime("%Y-%m-%d %H:%M")
        categories.append(f"{when} #{index}")  # unique per trade; the axis shows the date part
        points.append(
            {
                "when": when,
                "symbol": trade.symbol,
                "net": float(trade.net_pnl),
                "equity": float(point.equity),
                "dd": float(point.drawdown),
            }
        )
    tooltip_js = (
        "(params) => { " + _MONEY_JS + " const d = params[0].data; "
        "return `<div style=\"font-weight:600;margin-bottom:2px\">${d.when} · ${d.symbol}</div>`"
        " + `Trade #${params[0].dataIndex + 1}: ${m(d.net)}<br/>`"
        " + `Cumulative: <b>${m(d.equity)}</b><br/>Drawdown: ${m(d.dd)}`; }"
    )
    return {
        "animation": False,
        "grid": [
            {"left": 72, "right": 16, "top": 12, "height": "54%"},
            {"left": 72, "right": 16, "top": "68%", "bottom": 28},
        ],
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "tooltip": _tooltip(p, trigger="axis", axisPointer={"type": "line", "lineStyle": {"color": p.axis}}, **{":formatter": tooltip_js}),
        "xAxis": [
            _category_axis(p, categories, 0, axisLabel={"show": False}, boundaryGap=False),
            _category_axis(
                p,
                categories,
                1,
                boundaryGap=False,
                axisLabel={"color": p.muted, "fontSize": 11, "hideOverlap": True, ":formatter": "(v) => v.slice(0, 10)"},
            ),
        ],
        "yAxis": [
            _value_axis(p, 0, splitNumber=4),
            _value_axis(p, 1, max=0, splitNumber=2, minInterval=1),
        ],
        "dataZoom": [{"type": "inside", "xAxisIndex": [0, 1], "zoomOnMouseWheel": "ctrl", "moveOnMouseWheel": False}],
        "series": [
            {
                "name": "Cumulative net P&L",
                "type": "line",
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "data": [{"value": pt["equity"], **pt} for pt in points],
                "showSymbol": False,
                "lineStyle": {"width": 2, "color": p.accent},
                "itemStyle": {"color": p.accent},
                "markLine": {
                    "silent": True,
                    "symbol": "none",
                    "label": {"show": False},
                    "lineStyle": {"color": p.axis, "type": "solid", "width": 1},
                    "data": [{"yAxis": 0}],
                },
            },
            {
                "name": "Drawdown",
                "type": "line",
                "xAxisIndex": 1,
                "yAxisIndex": 1,
                "data": [{"value": pt["dd"], **pt} for pt in points],
                "showSymbol": False,
                "lineStyle": {"width": 1, "color": p.critical},
                "itemStyle": {"color": p.critical},
                "areaStyle": {"color": p.critical, "opacity": 0.2},
            },
        ],
    }


def daily_pnl_options(days: Sequence[DailyResult], p: Palette) -> dict:
    """Net P&L per trading day: green above zero, red below, rounded at the data end."""
    data = []
    for day in days:
        positive = day.net_pnl >= 0
        data.append(
            {
                "value": float(day.net_pnl),
                "trades": day.trade_count,
                "itemStyle": {
                    "color": p.muted if day.net_pnl == 0 else (p.good if positive else p.critical),
                    "borderRadius": [4, 4, 0, 0] if positive else [0, 0, 4, 4],
                },
            }
        )
    tooltip_js = (
        "(params) => { " + _MONEY_JS + " const d = params[0]; "
        "return `<div style=\"font-weight:600;margin-bottom:2px\">${d.name}</div>`"
        " + `${m(d.value)} · ${d.data.trades} trade${d.data.trades === 1 ? '' : 's'}`; }"
    )
    return {
        "animation": False,
        "grid": {"left": 72, "right": 16, "top": 12, "bottom": 28},
        "tooltip": _tooltip(p, trigger="axis", axisPointer={"type": "shadow", "shadowStyle": {"color": p.grid, "opacity": 0.5}}, **{":formatter": tooltip_js}),
        "xAxis": _category_axis(p, [d.day.isoformat() for d in days]),
        "yAxis": _value_axis(p, splitNumber=4),
        "series": [
            {
                "name": "Net P&L",
                "type": "bar",
                "data": data,
                "barMaxWidth": 24,
                "barCategoryGap": "25%",
            }
        ],
    }


def radar_options(score: PerformanceScore, p: Palette) -> dict:
    return {
        "animation": False,
        "tooltip": _tooltip(p, trigger="item"),
        "radar": {
            "indicator": [{"name": axis.name, "max": 100} for axis in score.axes],
            "radius": "62%",
            "center": ["50%", "52%"],
            "splitNumber": 4,
            "axisName": {"color": p.ink_2, "fontSize": 12},
            "splitLine": {"lineStyle": {"color": p.grid}},
            "splitArea": {"show": False},
            "axisLine": {"lineStyle": {"color": p.grid}},
        },
        "series": [
            {
                "type": "radar",
                "data": [{"name": "Score", "value": [float(axis.score) for axis in score.axes]}],
                "symbol": "circle",
                "symbolSize": 6,
                "lineStyle": {"width": 2, "color": p.accent},
                "itemStyle": {"color": p.accent, "borderColor": p.surface, "borderWidth": 2},
                "areaStyle": {"color": p.accent, "opacity": 0.15},
            }
        ],
    }
