"""The trade detail panel, opened from the trade log.

Layout: a header with the trade's key facts and net P&L, the stats in a narrow left
column, and the journal (tags, notes, screenshots) on the right where most of the
work happens, with the raw fill pairs folded away at the bottom. Previous/next walk
the trade log's current order without closing the panel.

Everything saves as you go: tags and screenshots immediately, notes a moment after
you stop typing (and whenever you move away from the trade).
"""

from __future__ import annotations

import time
from datetime import tzinfo
from functools import partial
from typing import Callable

from nicegui import events, ui

from .. import config, db, journal, store
from ..accounts import ACCOUNT_TYPES
from ..models import FillPair
from ..queries import TradeRecord, load_trade
from . import fmt
from .data import open_db
from .widgets import tag_pill

ALL_CATEGORIES = 0  # category-filter value meaning "every category" (group ids start at 1)
SAVE_DELAY = 0.8  # seconds of quiet typing before notes are saved
NOTES_PLACEHOLDER = "What was the setup? Why did you enter and exit? What would you do differently?"
NOTES_TEMPLATE = (
    "<p><b>Setup</b></p><p><br></p>"
    "<p><b>Entry</b></p><p><br></p>"
    "<p><b>Management and exit</b></p><p><br></p>"
    "<p><b>Lesson</b></p><p><br></p>"
)
NOTES_TOOLBAR = [
    ["bold", "italic", "underline", "strike"],
    ["unordered", "ordered", "quote"],
    ["hr", "link"],
    ["removeFormat"],
    ["undo", "redo"],
    ["fullscreen"],
]


class TradePanel:
    def __init__(
        self, trade_id: int, zone: tzinfo, order: list[int], on_changed: Callable[[int], None]
    ) -> None:
        """order: trade ids as the log currently lists them. on_changed(trade_id) runs
        whenever that trade's tags, notes or screenshots change."""
        self.trade_id = trade_id
        self.zone = zone
        self.order = order if trade_id in order else [trade_id]
        self.on_changed = on_changed
        self.unsaved_notes: str | None = None
        self.notes_changed_at = 0.0
        self.tag_category = ALL_CATEGORIES

        with ui.dialog() as self.dialog, ui.card().classes("tj-card tj-detail w-[1180px] max-w-[96vw] p-0 gap-0"):
            self.body = ui.column().classes("w-full gap-0")  # redrawn per trade
            self.overlays = ui.element("div")  # pop-ups live here so redraws never remove them
            ui.timer(0.4, self._autosave)
        self.dialog.on_value_change(self._visibility_changed)
        self._render()
        self.dialog.open()

    # -- structure ---------------------------------------------------------------

    def _render(self) -> None:
        with open_db() as conn:
            trade = load_trade(conn, self.trade_id)
            pairs = store.trade_fill_pairs(conn, self.trade_id)
            notes = journal.notes(conn, self.trade_id) if trade else ""
        self.body.clear()
        with self.body:
            if trade is None:
                ui.label("This trade no longer exists.").classes("tj-title p-6")
                return
            self._header(trade)
            ui.separator()
            with ui.element("div").classes("grid w-full grid-cols-1 md:grid-cols-[280px_minmax(0,1fr)]"):
                with ui.column().classes("gap-0 p-5 tj-detail-side"):
                    self._stats(trade)
                with ui.column().classes("gap-5 p-5 min-w-0"):
                    self.tags_box = ui.column().classes("w-full gap-2")
                    self._tags()
                    self._notes(notes)
                    self._screenshots()
                    self._fills(pairs)

    def _header(self, trade: TradeRecord) -> None:
        position = self.order.index(self.trade_id)
        with ui.row().classes("w-full items-center gap-3 px-5 py-3 no-wrap"):
            with ui.row().classes("gap-0 no-wrap"):
                previous = ui.button(icon="chevron_left", on_click=partial(self._step, -1)).props(
                    'flat round dense aria-label="Previous trade"'
                )
                following = ui.button(icon="chevron_right", on_click=partial(self._step, 1)).props(
                    'flat round dense aria-label="Next trade"'
                )
                previous.set_enabled(position > 0)
                following.set_enabled(position < len(self.order) - 1)
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label(f"{trade.symbol} · {trade.direction.capitalize()}").classes("tj-value")
                ui.label(
                    f"{trade.account_name} · {trade.firm} · {ACCOUNT_TYPES[trade.account_type]}  —  "
                    f"{trade.trade_date:%a %b %d, %Y} · {fmt.local(trade.entry_at, self.zone, '%H:%M:%S')} → "
                    f"{fmt.local(trade.exit_at, self.zone, '%H:%M:%S')} · held {fmt.hold(trade.hold_seconds)}"
                ).classes("tj-label")
            with ui.column().classes("gap-0 items-end"):
                ui.label(fmt.money(trade.net_pnl)).classes(f"tj-value tj-hero {fmt.pnl_class(trade.net_pnl)}")
                ui.label(f"Net P&L · trade {position + 1:,} of {len(self.order):,}").classes("tj-label")
            ui.button(icon="close", on_click=self.dialog.close).props('flat round dense aria-label="Close"')

    def _stats(self, trade: TradeRecord) -> None:
        rows = [
            ("Gross P&L", fmt.money(trade.gross_pnl), fmt.pnl_class(trade.gross_pnl)),
            ("Fees", fmt.money(trade.fees), ""),
            ("Points / contract", fmt.price(trade.points), fmt.pnl_class(trade.points)),
            ("Ticks / contract", f"{trade.ticks.normalize():f}", fmt.pnl_class(trade.ticks)),
            ("Contracts traded", str(trade.total_qty), ""),
            ("Max position", str(trade.max_position), ""),
            ("Avg entry", fmt.price(trade.avg_entry), ""),
            ("Avg exit", fmt.price(trade.avg_exit), ""),
            ("Hold time", fmt.hold(trade.hold_seconds), ""),
            ("Entry", fmt.local(trade.entry_at, self.zone, "%H:%M:%S"), ""),
            ("Exit", fmt.local(trade.exit_at, self.zone, "%H:%M:%S"), ""),
            ("Contract", f"{trade.symbol_root} {trade.contract_month or ''}".strip(), ""),
            ("Trading day", trade.trade_date.isoformat(), ""),
        ]
        ui.label("Stats").classes("tj-section")
        for label, value, value_class in rows:
            with ui.row().classes("tj-stat-row w-full no-wrap"):
                ui.label(label).classes("tj-label")
                ui.label(value).classes(f"tj-num {value_class}")

    # -- tags --------------------------------------------------------------------

    def _tags(self) -> None:
        """Tags on this trade, every other tag as a one-click option, and a new-tag field.
        Tags are shared: one created here is offered on every other trade."""
        with open_db() as conn:
            groups = journal.tag_groups(conn)
            known = journal.tags(conn)
            applied = journal.trade_tags(conn, self.trade_id)
        applied_ids = {t.id for t in applied}
        category = self.tag_category
        offered = [t for t in known if t.id not in applied_ids and category in (ALL_CATEGORIES, t.group_id)]

        self.tags_box.clear()
        with self.tags_box:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Tags").classes("tj-section")
                with ui.row().classes("items-center gap-2"):
                    ui.button("Manage tags", icon="tune", on_click=self._manage_tags).props(
                        "flat dense no-caps"
                    ).classes("tj-secondary").tooltip("Rename, recolor, recategorize or delete tags in Settings")
                    ui.select(
                        {ALL_CATEGORIES: "All categories", **{g.id: g.name for g in groups}},
                        value=category,
                        on_change=self._set_tag_category,
                    ).props('dense outlined options-dense aria-label="Show tags from"').classes("w-44")
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("On this trade").classes("tj-label w-24")
                if not applied:
                    ui.label("No tags yet").classes("tj-label tj-muted")
                for tag in applied:
                    tag_pill(tag, action="remove", on_click=partial(self._tag_change, "remove", tag.id))
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Add").classes("tj-label w-24")
                if not offered:
                    ui.label(
                        "No other tags in this category yet." if category != ALL_CATEGORIES
                        else "Tags you create appear here for every trade."
                    ).classes("tj-label tj-muted")
                for tag in offered:
                    tag_pill(tag, action="add", on_click=partial(self._tag_change, "add", tag.id))
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("New tag").classes("tj-label w-24")
                self.new_tag_name = (
                    ui.input(placeholder="e.g. scalp").props("dense outlined hide-bottom-space").classes("w-48")
                )
                self.new_tag_name.on("keydown.enter", self._create_tag)
                default_group = category if category != ALL_CATEGORIES else (groups[0].id if groups else None)
                self.new_tag_group = ui.select({g.id: g.name for g in groups}, value=default_group).props(
                    'dense outlined options-dense aria-label="Category for the new tag"'
                ).classes("w-36")
                ui.button("Add tag", icon="add", on_click=self._create_tag).props("flat dense no-caps")

    def _manage_tags(self) -> None:
        self._save_notes()  # leaving the page; don't lose the last few keystrokes
        ui.navigate.to("/settings?section=tags")

    def _set_tag_category(self, e) -> None:
        self.tag_category = e.value
        self._tags()

    def _tag_change(self, action: str, tag_id: int) -> None:
        change = journal.add_trade_tag if action == "add" else journal.remove_trade_tag
        with open_db() as conn, db.transaction(conn):
            change(conn, self.trade_id, tag_id)
        self._tags()
        self.on_changed(self.trade_id)

    def _create_tag(self) -> None:
        name, group_id = self.new_tag_name.value, self.new_tag_group.value
        try:
            with open_db() as conn, db.transaction(conn):
                tag = journal.get_or_create_tag(conn, group_id, name)  # reuses one with that name
                journal.add_trade_tag(conn, self.trade_id, tag.id)
        except ValueError as exc:
            ui.notify(f"Tag not added: {exc}.", type="warning")
            return
        self._tags()
        self.new_tag_name.run_method("focus")  # ready for the next one
        self.on_changed(self.trade_id)

    # -- notes -------------------------------------------------------------------

    def _notes(self, html: str) -> None:
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-baseline gap-2"):
                    ui.label("Notes").classes("tj-section")
                    self.notes_status = ui.label("Saved" if html else "").classes("tj-label tj-muted")
                ui.button("Insert template", icon="post_add", on_click=self._insert_template).props(
                    "flat dense no-caps"
                ).tooltip("Adds headings: Setup, Entry, Management and exit, Lesson")
            self.editor = ui.editor(value=html, placeholder=NOTES_PLACEHOLDER, on_change=self._notes_edited).classes(
                "w-full tj-notes"
            )
            self.editor.props["toolbar"] = NOTES_TOOLBAR
            self.editor.props["min-height"] = "11rem"

    def _notes_edited(self, e) -> None:
        self.unsaved_notes = e.value or ""
        self.notes_changed_at = time.monotonic()
        self.notes_status.set_text("Editing…")

    def _insert_template(self) -> None:
        current = journal.clean_notes(self.editor.value)
        self.editor.set_value(f"{current}{NOTES_TEMPLATE}" if current else NOTES_TEMPLATE)

    def _autosave(self) -> None:
        if self.unsaved_notes is not None and time.monotonic() - self.notes_changed_at >= SAVE_DELAY:
            self._save_notes()

    def _save_notes(self) -> None:
        if self.unsaved_notes is None:
            return
        html, self.unsaved_notes = self.unsaved_notes, None
        with open_db() as conn, db.transaction(conn):
            journal.set_notes(conn, self.trade_id, html)
        self.notes_status.set_text("Saved")
        self.on_changed(self.trade_id)

    # -- screenshots -------------------------------------------------------------

    def _screenshots(self) -> None:
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full items-baseline gap-2"):
                ui.label("Screenshots").classes("tj-section")
                ui.label("Drop images below, click + to browse, or paste one with Ctrl+V.").classes("tj-label")
            self.shots_box = ui.element("div").classes("grid gap-3 w-full grid-cols-2 lg:grid-cols-3")
            self.uploader = (
                ui.upload(
                    multiple=True,
                    auto_upload=True,
                    on_multi_upload=self._uploaded,
                    label="Drop chart screenshots here",
                )
                .props('accept="image/png,image/jpeg,image/gif,image/webp" flat bordered')
                .classes("tj-dropzone")
            )
        ui.run_javascript(f"window.tjPasteUploader = {self.uploader.id}")
        self._thumbnails()

    def _thumbnails(self) -> None:
        with open_db() as conn:
            shots = journal.screenshots(conn, self.trade_id)
        self.shots_box.clear()
        with self.shots_box:
            for index, shot in enumerate(shots):
                with ui.element("button").classes("tj-thumb").props(
                    f'type=button aria-label="View screenshot {shot.original_name}"'
                ) as thumb:
                    ui.image(f"/screenshots/{shot.stored_name}").props("fit=cover ratio=1.6 no-spinner")
                    ui.label(shot.original_name).classes("tj-thumb-name")
                thumb.on("click", partial(self._view, index))

    async def _uploaded(self, e: events.MultiUploadEventArguments) -> None:
        problems = []
        for upload in e.files:
            data = await upload.read()
            try:
                with open_db() as conn, db.transaction(conn):
                    journal.add_screenshot(conn, config.screenshots_dir(), self.trade_id, upload.name, data)
            except ValueError as exc:
                problems.append(f"{upload.name}: {exc}")
        self.uploader.reset()
        if problems:
            ui.notify("Not added: " + "; ".join(problems), type="warning", multi_line=True)
        self._thumbnails()
        self.on_changed(self.trade_id)

    def _view(self, index: int) -> None:
        with self.overlays:
            ScreenshotViewer(self.trade_id, index, on_deleted=self._screenshot_deleted)

    def _screenshot_deleted(self) -> None:
        self._thumbnails()
        self.on_changed(self.trade_id)

    # -- fill pairs --------------------------------------------------------------

    def _fills(self, pairs: list[FillPair]) -> None:
        with ui.expansion(f"Fill pairs ({len(pairs)})", icon="receipt_long").classes("w-full tj-secondary").props(
            "dense"
        ):
            ui.table(
                columns=[
                    {"name": "entry", "label": "Entry", "field": "entry", "align": "left"},
                    {"name": "exit", "label": "Exit", "field": "exit", "align": "left"},
                    {"name": "qty", "label": "Qty", "field": "qty", "align": "right"},
                    {"name": "buy", "label": "Buy price", "field": "buy", "align": "right"},
                    {"name": "sell", "label": "Sell price", "field": "sell", "align": "right"},
                    {"name": "pnl", "label": "P&L", "field": "pnl", "align": "right"},
                    {"name": "duration", "label": "Duration", "field": "duration", "align": "right"},
                    {"name": "ids", "label": "Buy / sell fill IDs", "field": "ids", "align": "right"},
                ],
                rows=[
                    {
                        "key": f"{p.buy_fill_id}-{p.sell_fill_id}",
                        "entry": fmt.local(p.entry_at, self.zone, "%H:%M:%S"),
                        "exit": fmt.local(p.exit_at, self.zone, "%H:%M:%S"),
                        "qty": p.qty,
                        "buy": fmt.price(p.buy_price),
                        "sell": fmt.price(p.sell_price),
                        "pnl": fmt.money(p.pnl),
                        "duration": fmt.hold(p.duration_seconds),
                        "ids": f"{p.buy_fill_id} / {p.sell_fill_id}",
                    }
                    for p in pairs
                ],
                row_key="key",
            ).classes("w-full tj-table").props("flat dense hide-bottom")

    # -- navigation --------------------------------------------------------------

    def _step(self, step: int) -> None:
        position = self.order.index(self.trade_id) + step
        if 0 <= position < len(self.order):
            self._save_notes()
            self.trade_id = self.order[position]
            self._render()

    def _visibility_changed(self, e) -> None:
        if e.value:
            return
        self._save_notes()
        ui.run_javascript("window.tjPasteUploader = undefined")
        self.dialog.delete()  # last: deleting removes this handler's context


class ScreenshotViewer:
    """A trade's screenshots at full size, with previous/next, open-in-tab and delete."""

    def __init__(self, trade_id: int, index: int, on_deleted: Callable[[], None]) -> None:
        self.trade_id = trade_id
        self.index = index
        self.on_deleted = on_deleted
        with ui.dialog() as self.dialog, ui.card().classes("tj-card w-[1280px] max-w-[96vw] gap-3"):
            self.body = ui.column().classes("w-full gap-3")  # redrawn per screenshot
            # The delete confirmation sits outside the redrawn part and is reused.
            with ui.dialog() as self.confirm, ui.card().classes("tj-card w-[400px] max-w-[95vw] gap-3"):
                ui.label("Delete this screenshot?").classes("tj-title")
                self.confirm_detail = ui.label()
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancel", on_click=self.confirm.close).props("flat no-caps")
                    ui.button("Delete", on_click=self._delete).props("unelevated no-caps color=negative")
        self.dialog.on_value_change(lambda e: None if e.value else self.dialog.delete())
        self._render()
        self.dialog.open()

    def _render(self) -> None:
        with open_db() as conn:
            self.shots = journal.screenshots(conn, self.trade_id)
        if not self.shots:
            self.dialog.close()
            return
        self.index = min(self.index, len(self.shots) - 1)
        shot = self.shots[self.index]
        self.body.clear()
        with self.body:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                previous = ui.button(icon="chevron_left", on_click=partial(self._step, -1)).props(
                    'flat round dense aria-label="Previous screenshot"'
                )
                following = ui.button(icon="chevron_right", on_click=partial(self._step, 1)).props(
                    'flat round dense aria-label="Next screenshot"'
                )
                previous.set_enabled(self.index > 0)
                following.set_enabled(self.index < len(self.shots) - 1)
                with ui.column().classes("gap-0 grow min-w-0"):
                    ui.label(shot.original_name).classes("tj-title")
                    ui.label(f"{self.index + 1} of {len(self.shots)}").classes("tj-label")
                ui.link("Open full size", f"/screenshots/{shot.stored_name}", new_tab=True).classes("tj-label")
                ui.button("Delete", icon="delete_outline", on_click=self._confirm_delete).props(
                    "flat dense no-caps color=negative"
                )
                ui.button(icon="close", on_click=self.dialog.close).props('flat round dense aria-label="Close"')
            ui.image(f"/screenshots/{shot.stored_name}").props("fit=contain no-spinner").classes(
                "w-full tj-viewer-image"
            )

    def _step(self, step: int) -> None:
        self.index += step
        self._render()

    def _confirm_delete(self) -> None:
        self.confirm_detail.set_text(
            f"{self.shots[self.index].original_name} will be removed from the trade and from disk."
        )
        self.confirm.open()

    def _delete(self) -> None:
        shot = self.shots[self.index]
        with open_db() as conn, db.transaction(conn):
            stored_name = journal.delete_screenshot(conn, shot.id)
        if stored_name:  # the file goes only once the database change is committed
            journal.remove_file(config.screenshots_dir(), stored_name)
        self.confirm.close()
        self.on_deleted()
        self._render()  # shows the next screenshot, or closes when none are left
