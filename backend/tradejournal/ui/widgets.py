"""Small building blocks shared by several pages."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from nicegui import ui

from .. import db, ledger
from ..accounts import ACCOUNT_TYPES, FIRMS, Account, create_account, set_fee
from ..finance import DEFAULT_FEE_KEY, parse_decimal
from ..journal import Tag
from .data import open_db
from .theme import tag_style


def stat_tile(label: str, value: str, *, sub: str | None = None, value_class: str = "", hero: bool = False) -> None:
    with ui.card().classes("tj-card gap-1 min-w-0"):
        ui.label(label).classes("tj-label")
        ui.label(value).classes(f"tj-value {'tj-hero' if hero else ''} {value_class}")
        if sub:
            ui.label(sub).classes("tj-label tj-muted")


def tag_pill(
    tag: Tag,
    *,
    action: str | None = None,
    on_click: Callable[[], None] | None = None,
    count: int | None = None,
) -> ui.element:
    """A colored tag pill.

    action "remove" shows × on a filled pill; "add" shows + on an outlined pill (a tag
    that isn't applied yet); "edit" opens the tag's editor; None is display-only. With
    on_click it's a real button.
    """
    pill = ui.element("button" if on_click else "span").classes("tj-tag").style(tag_style(tag.color))
    if action == "add":
        pill.classes("tj-tag-option")
    with pill:
        ui.label(tag.name)
        if count is not None:
            ui.label(str(count)).classes("tj-tag-count")
        if action in ("remove", "add"):
            ui.icon("close" if action == "remove" else "add")
    if on_click:
        verb = {"remove": "Remove", "add": "Add", "edit": "Edit"}.get(action or "", "Select")
        pill.props(f'type=button aria-label="{verb} tag {tag.name}"')
        pill.on("click", on_click)
    return pill


def date_input(label: str, value: date | None, *, on_change: Callable | None = None) -> ui.input:
    """A YYYY-MM-DD field with a calendar picker behind its icon."""
    with ui.input(label, value=value.isoformat() if value else "", on_change=on_change).props(
        "dense outlined clearable"
    ) as field:
        with ui.menu().props("no-parent-event") as menu:
            with ui.date(mask="YYYY-MM-DD").bind_value(field):
                with ui.row().classes("justify-end"):
                    ui.button("Done", on_click=menu.close).props("flat dense no-caps")
        with field.add_slot("append"):
            ui.icon("event").on("click", menu.open).classes("cursor-pointer")
    return field


def parse_amount(text: str | None, *, required: bool = True) -> Decimal | None:
    """Parse a dollar amount typed by the user ("50,000", "$4.12"). Raises ValueError."""
    cleaned = (text or "").strip().replace(",", "").replace("$", "")
    if not cleaned:
        if required:
            raise ValueError("required")
        return None
    value = parse_decimal(cleaned)
    if value < 0:
        raise ValueError("must not be negative")
    return value


def amount_input(label: str, value: Decimal | str | None = None, *, required: bool = True) -> ui.input:
    """A text field for money. Text, not a number field, so values stay exact decimals."""

    def check(text: str) -> str | None:
        try:
            parse_amount(text, required=required)
        except ValueError as exc:
            return "Required" if str(exc) == "required" else "Enter an amount like 4.12"
        return None

    shown = "" if value is None else str(value)
    return ui.input(label, value=shown, validation=check).props("dense outlined hide-bottom-space")


def new_account_dialog(
    on_created: Callable[[Account], None],
    *,
    title: str = "New account",
    based_on: Account | None = None,
    account_type: str | None = None,
    fee_default: Decimal | None = None,
) -> None:
    """Ask for a new account's details and create it.

    based_on pre-fills firm, size and balance (e.g. the funded account after an
    evaluation passes); account_type pre-selects evaluation or funded.
    """
    size_text = str(based_on.account_size) if based_on and based_on.account_size else "50000"
    balance_text = str(based_on.starting_balance) if based_on else "50000"
    with ui.dialog() as dialog, ui.card().classes("tj-card w-[440px] max-w-[95vw] gap-3"):
        ui.label(title).classes("tj-title")
        name = ui.input("Name", placeholder="e.g. Apex 50K #07").props("dense outlined autofocus").classes("w-full")
        with ui.row().classes("w-full gap-3 no-wrap"):
            # No default unless the caller knows: every account gets a deliberate choice.
            type_field = (
                ui.select(ACCOUNT_TYPES, value=account_type, label="Evaluation or funded?")
                .props("dense outlined")
                .classes("flex-1")
            )
            firm = (
                ui.select(list(FIRMS), value=based_on.firm if based_on else "Apex", label="Firm")
                .props("dense outlined")
                .classes("flex-1")
            )
        size = amount_input("Account size ($)", size_text, required=False).classes("w-full")
        balance = amount_input("Starting balance ($)", balance_text).classes("w-full")
        fee = amount_input("Fees per contract, round trip ($)", str(fee_default or "0")).classes("w-full")
        ui.label("Set different fee rates per symbol later in Settings.").classes("tj-label")
        ui.separator()
        with ui.row().classes("w-full gap-3 no-wrap"):
            cost = amount_input("What the account cost ($)", None, required=False).classes("flex-1")
            cost_date = date_input("Paid on", date.today()).classes("w-40")
        ui.label(
            "The evaluation or activation fee. Optional. Add resets, monthly fees and payouts later in Settings."
        ).classes("tj-label")

        def create() -> None:
            if not name.value.strip():
                ui.notify("Give the account a name.", type="warning")
                return
            if type_field.value is None:
                ui.notify("Choose whether this is an evaluation or a funded account.", type="warning")
                return
            try:
                size_value = parse_amount(size.value, required=False)
                balance_value = parse_amount(balance.value)
                fee_value = parse_amount(fee.value)
                cost_value = parse_amount(cost.value, required=False)
                paid_on = date.fromisoformat(cost_date.value) if cost_value else None
            except (TypeError, ValueError):  # TypeError: the date field was cleared
                ui.notify("Check the highlighted amounts and the date (YYYY-MM-DD).", type="warning")
                return
            try:
                with open_db() as conn, db.transaction(conn):
                    account = create_account(
                        conn,
                        name.value,
                        firm=firm.value,
                        account_type=type_field.value,
                        account_size=size_value,
                        starting_balance=balance_value,
                    )
                    set_fee(conn, account.id, DEFAULT_FEE_KEY, fee_value)
                    if cost_value:
                        ledger.add_entry(
                            conn, account.id, ledger.default_cost_kind(type_field.value), cost_value, paid_on
                        )
            except sqlite3.IntegrityError:
                ui.notify(f"An account named '{name.value.strip()}' already exists.", type="warning")
                return
            ui.notify(f"Created {account.name}.", type="positive")
            on_created(account)
            dialog.close()  # last: closing deletes the dialog and its event context

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Create account", on_click=create).props("unelevated no-caps color=primary")
    dialog.on_value_change(lambda e: None if e.value else dialog.delete())
    dialog.open()
