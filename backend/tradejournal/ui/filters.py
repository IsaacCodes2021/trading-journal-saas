"""The filter row shown above the dashboard and the trade log."""

from __future__ import annotations

from datetime import date
from typing import Callable

from nicegui import ui

from ..accounts import list_accounts
from ..queries import symbol_roots
from .data import open_db
from .state import PRESETS, filters
from .widgets import date_input

ACCOUNT_VIEWS = {"all": "All accounts", "funded": "Funded", "evaluation": "Evaluation"}
_ACCOUNT_LABELS = {"all": "Accounts", "funded": "Funded accounts", "evaluation": "Evaluation accounts"}


def filter_bar(on_change: Callable[[], None]) -> None:
    with open_db() as conn:
        accounts = list_accounts(conn)
        root_options = symbol_roots(conn)

    firm_options = sorted({a.firm for a in accounts})  # firms you have accounts with

    def account_options() -> dict[int, str]:
        """Accounts matching the selected type and firms (archived ones only when shown)."""
        return {
            a.id: f"{a.name} (archived)" if a.archived else a.name
            for a in accounts
            if filters.account_type in ("all", a.account_type)
            and (not filters.firms or a.firm in filters.firms)
            and (filters.show_archived or not a.archived)
        }

    # Forget selections that no longer exist or no longer match.
    filters.firms = [f for f in filters.firms if f in firm_options]
    filters.account_ids = [a for a in filters.account_ids if a in account_options()]
    filters.symbol_roots = [r for r in filters.symbol_roots if r in root_options]
    syncing = False  # True while one control updates another, to avoid feedback loops

    def narrow_accounts() -> None:
        """Offer only accounts matching the type and firms, dropping any that no longer do."""
        nonlocal syncing
        options = account_options()
        filters.account_ids = [a for a in filters.account_ids if a in options]
        syncing = True
        account_field.set_options(options, value=filters.account_ids)
        account_field.props(f'label="{_ACCOUNT_LABELS[filters.account_type]}"')
        syncing = False

    def set_account_type(e) -> None:
        filters.account_type = e.value or "all"
        narrow_accounts()
        on_change()

    def set_firms(e) -> None:
        if syncing:
            return
        filters.firms = list(e.value or [])
        narrow_accounts()
        on_change()

    def set_show_archived(e) -> None:
        filters.show_archived = bool(e.value)
        narrow_accounts()
        on_change()

    def set_accounts(e) -> None:
        if syncing:
            return
        filters.account_ids = list(e.value or [])
        on_change()

    def set_roots(e) -> None:
        filters.symbol_roots = list(e.value or [])
        on_change()

    def set_direction(e) -> None:
        filters.direction = e.value or "all"
        on_change()

    def set_preset(e) -> None:
        nonlocal syncing
        if syncing:
            return
        filters.apply_preset(e.value)
        syncing = True
        start_field.set_value(filters.start.isoformat() if filters.start else "")
        end_field.set_value(filters.end.isoformat() if filters.end else "")
        syncing = False
        on_change()

    def set_dates(_) -> None:
        nonlocal syncing
        if syncing:
            return
        try:
            start = date.fromisoformat(start_field.value) if start_field.value else None
            end = date.fromisoformat(end_field.value) if end_field.value else None
        except ValueError:
            return  # still typing
        if (start, end) == (filters.start, filters.end):
            return
        filters.start, filters.end, filters.preset = start, end, "custom"
        syncing = True
        preset_field.set_value("custom")
        syncing = False
        on_change()

    def reset() -> None:
        filters.account_type, filters.direction, filters.show_archived = "all", "all", False
        filters.firms, filters.account_ids, filters.symbol_roots = [], [], []
        filters.apply_preset("all")
        ui.navigate.reload()

    with ui.row().classes("w-full items-center gap-3"):
        ui.toggle(ACCOUNT_VIEWS, value=filters.account_type, on_change=set_account_type).props("dense no-caps")
        ui.select(firm_options, multiple=True, value=filters.firms, label="Firms", on_change=set_firms).props(
            "dense outlined use-chips clearable"
        ).classes("min-w-[130px]")
        account_field = ui.select(
            account_options(),
            multiple=True,
            value=filters.account_ids,
            label=_ACCOUNT_LABELS[filters.account_type],
            on_change=set_accounts,
        ).props("dense outlined use-chips clearable").classes("min-w-[200px]")
        ui.switch("Show archived", value=filters.show_archived, on_change=set_show_archived).props("dense").classes(
            "tj-secondary"
        ).tooltip("Include accounts you've archived after they passed, failed or closed")
        preset_field = ui.select(PRESETS, value=filters.preset, label="Date range", on_change=set_preset).props(
            "dense outlined"
        ).classes("w-40")
        start_field = date_input("From", filters.start, on_change=set_dates).classes("w-36")
        end_field = date_input("To", filters.end, on_change=set_dates).classes("w-36")
        ui.select(
            root_options, multiple=True, value=filters.symbol_roots, label="Symbols", on_change=set_roots
        ).props("dense outlined use-chips clearable").classes("min-w-[140px]")
        ui.toggle({"all": "All", "long": "Long", "short": "Short"}, value=filters.direction, on_change=set_direction).props(
            "dense no-caps"
        )
        ui.button("Reset", on_click=reset).props("flat dense no-caps").classes("tj-secondary")
