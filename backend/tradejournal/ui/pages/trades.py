"""Trade log: sortable, filterable table of trades. Clicking one opens its detail panel
(see trade_detail.py)."""

from __future__ import annotations

from datetime import tzinfo

from nicegui import ui

from ... import finance, journal
from ...queries import TradeRecord, load_trades
from .. import fmt
from ..data import display_zone, open_db
from ..filters import filter_bar
from ..layout import frame
from ..state import filters
from ..theme import tag_style
from ..trade_detail import TradePanel


def _decimals(places: int) -> str:
    return (
        "(p) => p.value == null ? '' : p.value.toLocaleString('en-US', "
        f"{{minimumFractionDigits: 0, maximumFractionDigits: {places}}})"
    )


_MONEY = {
    ":valueFormatter": (
        "(p) => p.value == null ? '' : (p.value < 0 ? '-$' : '$') + "
        "Math.abs(p.value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})"
    ),
    "type": "numericColumn",
    "minWidth": 96,
}
_PRICE = {  # grid shows 2 decimals; the detail panel shows exact averages
    ":valueFormatter": (
        "(p) => p.value == null ? '' : p.value.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})"
    ),
    "type": "numericColumn",
    "minWidth": 96,
}
_HOLD = {
    ":valueFormatter": (
        "(p) => { const s = p.value, h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;"
        " return h ? `${h}h ${String(m).padStart(2, '0')}m` : m ? `${m}m ${String(r).padStart(2, '0')}s` : `${r}s`; }"
    ),
    "type": "numericColumn",
    "minWidth": 76,
}
_NUMBER = {"type": "numericColumn", "minWidth": 64}
_PNL_COLOR = {"cellClassRules": {"tj-pos": "x > 0", "tj-neg": "x < 0"}}
# Draws the row's tags as colored pills. Names are escaped: they're text, never HTML.
_TAG_PILLS = r"""(p) => {
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);
  return (p.data.tag_list || [])
    .map((t) => `<span class="tj-tag" style="${esc(t.s)}">${esc(t.n)}</span>`)
    .join(" ");
}"""
# Small icons for trades that have notes and/or screenshots.
_JOURNAL_ICONS = r"""(p) => {
  const icon = (name, label) =>
    `<i class="q-icon notranslate material-icons tj-journal-icon" role="img" aria-label="${label}">${name}</i>`;
  let html = p.data.has_notes ? icon("notes", "Has notes") : "";
  if (p.data.shots) html += `<span class="tj-journal-shots">${icon("image", "Screenshots")}${p.data.shots}</span>`;
  return html;
}"""

# Net P&L sits near the front so it never scrolls out of view.
COLUMNS = [
    {"field": "entry", "headerName": "Entry time", "sort": "desc", "minWidth": 150},
    {"field": "account", "headerName": "Account", "minWidth": 100},
    {"field": "symbol", "headerName": "Symbol", "minWidth": 84},
    {"field": "side", "headerName": "Side", "minWidth": 72},
    {"field": "qty", "headerName": "Qty", **_NUMBER},
    {"field": "net", "headerName": "Net P&L", **_MONEY, **_PNL_COLOR},
    # Shown as pills; the text filter matches the tag names ("contains scalp").
    {
        "field": "tags",
        "headerName": "Tags",
        "minWidth": 160,
        "filter": "agTextColumnFilter",
        "tooltipField": "tags",
        ":cellRenderer": _TAG_PILLS,
    },
    {
        "field": "journal",  # sorts trades with notes and screenshots together
        "headerName": "Journal",
        "headerTooltip": "Notes and screenshots",
        "minWidth": 84,
        "filter": False,
        ":cellRenderer": _JOURNAL_ICONS,
    },
    {"field": "gross", "headerName": "Gross", **_MONEY, **_PNL_COLOR},
    {"field": "fees", "headerName": "Fees", **_MONEY, "minWidth": 80},
    {"field": "points", "headerName": "Points", **_NUMBER, **_PNL_COLOR, ":valueFormatter": _decimals(2), "minWidth": 72},
    {"field": "ticks", "headerName": "Ticks", **_NUMBER, **_PNL_COLOR, ":valueFormatter": _decimals(1)},
    {"field": "hold", "headerName": "Hold", **_HOLD},
    {"field": "avg_entry", "headerName": "Avg entry", **_PRICE},
    {"field": "avg_exit", "headerName": "Avg exit", **_PRICE},
    {"field": "max_position", "headerName": "Max", "headerTooltip": "Largest position held", **_NUMBER},
    {"field": "fills", "headerName": "Fills", "headerTooltip": "Fill pairs in this trade", **_NUMBER},
]


def grid_row(trade: TradeRecord, zone: tzinfo, tags: list[journal.Tag], notes_and_shots: tuple[bool, int]) -> dict:
    # Numbers go to the grid as floats so it can sort them; the detail panel and
    # all totals use the Decimal values.
    return {
        "id": trade.id,
        "entry": fmt.local(trade.entry_at, zone),
        "account": trade.account_name,
        "symbol": trade.symbol,
        "side": trade.direction.capitalize(),
        **_journal_fields(tags, notes_and_shots),
        "qty": trade.total_qty,
        "max_position": trade.max_position,
        "avg_entry": float(trade.avg_entry),
        "avg_exit": float(trade.avg_exit),
        "points": float(trade.points),
        "ticks": float(trade.ticks),
        "hold": trade.hold_seconds,
        "gross": float(trade.gross_pnl),
        "fees": float(trade.fees),
        "net": float(trade.net_pnl),
        "fills": trade.fill_pair_count,
    }


def _journal_fields(tags: list[journal.Tag], notes_and_shots: tuple[bool, int]) -> dict:
    """Tag names as text (for filtering and search) and as colored pills, plus the
    notes/screenshot markers."""
    has_notes, shots = notes_and_shots
    return {
        "tags": ", ".join(t.name for t in tags),
        "tag_list": [{"n": t.name, "s": tag_style(t.color)} for t in tags],
        "has_notes": has_notes,
        "shots": shots,
        "journal": int(has_notes) + shots,
    }


@ui.page("/trades")
def trades_page() -> None:
    zone = display_zone()
    loaded: dict[int, TradeRecord] = {}  # trades from the filter bar, by id

    def show_totals(shown: list[TradeRecord]) -> None:
        net = sum((t.net_pnl for t in shown), finance.ZERO)
        of_total = f" of {len(loaded):,}" if len(shown) != len(loaded) else ""
        count_label.set_text(f"{len(shown):,}{of_total} trade{'s' if len(loaded) != 1 else ''}")
        net_label.set_text(fmt.money(net))
        net_label.classes(replace=f"tj-title tj-num {fmt.pnl_class(net)}")

    def refresh() -> None:
        with open_db() as conn:
            trades = load_trades(conn, filters.to_filter())
            tags = journal.tags_by_trade(conn)
            summary = journal.journal_summary(conn)
        loaded.clear()
        loaded.update((t.id, t) for t in trades)
        show_totals(trades)
        grid.options["rowData"] = [
            grid_row(t, zone, tags.get(t.id, []), summary.get(t.id, (False, 0))) for t in trades
        ]
        grid.update()

    async def grid_filters_changed() -> None:
        """Column filters and search narrow the rows; recount from the exact stored values."""
        rows = await grid.get_client_data(method="filtered_unsorted")
        show_totals([loaded[row["id"]] for row in rows if row["id"] in loaded])

    def journal_changed(trade_id: int) -> None:
        """Redraw one row's tags and journal icons in place, keeping sort and scroll."""
        with open_db() as conn:
            tags = journal.trade_tags(conn, trade_id)
            notes_and_shots = journal.journal_summary(conn).get(trade_id, (False, 0))
        for row in grid.options["rowData"]:
            if row["id"] == trade_id:
                row.update(_journal_fields(tags, notes_and_shots))
                grid.run_row_method(str(trade_id), "setData", row)

    async def open_trade(e) -> None:
        # Previous/next in the panel follow the log's current filter and sort order.
        rows = await grid.get_client_data(method="filtered_sorted")
        TradePanel(e.args["data"]["id"], zone, [row["id"] for row in rows], on_changed=journal_changed)

    with frame("trades"):
        filter_bar(on_change=refresh)
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-baseline gap-2"):
                count_label = ui.label().classes("tj-title")
                ui.label("net").classes("tj-label")
                net_label = ui.label().classes("tj-title tj-num")
                ui.label("Click a trade to add tags, notes and screenshots.").classes("tj-label ml-4")
            ui.input(
                placeholder="Search trades",
                on_change=lambda e: grid.run_grid_method("setGridOption", "quickFilterText", e.value or ""),
            ).props("dense outlined clearable").classes("w-64")
        grid = (
            ui.aggrid(
                {
                    "columnDefs": COLUMNS,
                    # Column filters live in a row under the headers, so header text isn't
                    # squeezed by a filter icon.
                    "defaultColDef": {
                        "sortable": True,
                        "filter": True,
                        "floatingFilter": True,
                        "suppressHeaderFilterButton": True,
                        "suppressHeaderMenuButton": True,
                        "resizable": True,
                    },
                    "rowData": [],
                    ":getRowId": "(params) => String(params.data.id)",
                    "animateRows": False,
                    "rowSelection": {"mode": "singleRow", "checkboxes": False, "enableClickSelection": True},
                },
                theme="quartz",
            )
            .classes("tj-grid w-full")
            .style("height: calc(100vh - 230px); min-height: 420px")
        )
        # Only send the row data: AG Grid's full event holds circular internals.
        grid.on("rowClicked", open_trade, ["data"])
        grid.on("filterChanged", grid_filters_changed, [])  # [] = send no event details
        refresh()
