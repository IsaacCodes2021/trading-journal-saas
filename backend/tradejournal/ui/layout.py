"""Page frame: header with navigation and the theme toggle."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from nicegui import ui

from .data import is_dark, set_dark

NAV = [
    ("dashboard", "/", "Dashboard"),
    ("trades", "/trades", "Trade log"),
    ("import", "/import", "Import"),
    ("settings", "/settings", "Settings"),
]


@contextmanager
def frame(active: str) -> Iterator[ui.column]:
    """Wrap a page: header, navigation, and a centered content column."""
    dark = is_dark()
    ui.dark_mode(dark)

    def toggle_theme() -> None:
        set_dark(not dark)
        ui.navigate.reload()  # charts pick their colors when drawn

    with ui.header().classes("tj-header items-center justify-between px-6 py-2"):
        with ui.row().classes("items-center gap-2"):
            ui.label("Trading Journal").classes("text-base font-semibold mr-4")
            for key, path, label in NAV:
                ui.link(label, path).classes("tj-nav" + (" tj-nav-active" if key == active else ""))
        ui.button(icon="light_mode" if dark else "dark_mode", on_click=toggle_theme).props(
            f'flat round dense aria-label="Switch to {"light" if dark else "dark"} mode"'
        ).classes("tj-secondary")

    with ui.column().classes("w-full max-w-[1440px] mx-auto px-6 py-5 gap-4") as content:
        yield content


def card(title: str | None = None, subtitle: str | None = None) -> ui.card:
    """A bordered surface with an optional title line. Use as a context manager."""
    container = ui.card().classes("tj-card w-full gap-3")
    if title:
        with container:
            with ui.column().classes("gap-0"):
                ui.label(title).classes("tj-title")
                if subtitle:
                    ui.label(subtitle).classes("tj-label")
    return container
