"""Settings: timezone and trading day, accounts and their fee schedules, and tags."""

from __future__ import annotations

import sqlite3
from datetime import date
from functools import partial
from zoneinfo import available_timezones

from nicegui import ui

from ... import db, finance, journal, ledger, settings, store
from ...accounts import (
    ACCOUNT_TYPES,
    FIRMS,
    OUTCOMES,
    OUTCOMES_BY_TYPE,
    Account,
    archive_account,
    archive_summary,
    fee_schedule,
    last_trading_day,
    list_accounts,
    remove_fee,
    restore_account,
    set_fee,
    update_account,
)
from ...finance import DEFAULT_FEE_KEY
from .. import fmt
from ..data import open_db
from ..layout import card, frame
from ..theme import on_color, tag_style
from ..widgets import amount_input, date_input, new_account_dialog, parse_amount, tag_pill

# Icon per archive outcome, so passed and failed differ by shape as well as color.
_OUTCOME_ICONS = {"passed": "check_circle", "failed": "cancel", "closed": "inventory_2"}

_TIMEZONES = sorted(tz for tz in available_timezones() if "/" in tz and not tz.startswith(("Etc/", "SystemV/"))) + ["UTC"]


@ui.page("/settings")
def settings_page(section: str = "") -> None:
    """section=tags scrolls to the tags editor (the trade panel links there)."""
    with frame("settings"):
        _general_card()
        with card(
            "Accounts",
            "Details and fees per account. Changing an account's type moves all its trades between the funded "
            "and evaluation views. Saving fees recomputes that account's net P&L. Archive an account once it "
            "has passed, failed or closed: its trades stay, but drop out of the dashboard and trade log.",
        ):
            business_summary()
            accounts_list()
            ui.button("New account", icon="add", on_click=lambda: new_account_dialog(_account_added)).props(
                "flat no-caps"
            )
        with card(
            "Tags",
            "Tags are shared by all trades: add them from any trade's detail panel, or here ahead of time. Click "
            "a tag to rename it, change its color or category, or delete it; click a category to rename or "
            "delete it. The number on each tag is how many trades carry it.",
        ) as tags_card:
            tags_editor()
    if section == "tags":
        ui.timer(
            0.1,
            lambda: ui.run_javascript(f'getHtmlElement({tags_card.id}).scrollIntoView({{block: "start"}})'),
            once=True,
        )


def _general_card() -> None:
    with open_db() as conn:
        current = {key: settings.get_setting(conn, key) for key in settings.DEFAULTS}

    with card("General"):
        with ui.element("div").classes("grid gap-4 w-full grid-cols-1 md:grid-cols-3"):
            timezone = ui.select(_TIMEZONES, value=current["timezone"], label="Your timezone", with_input=True).props(
                "dense outlined"
            )
            day_start = ui.input(
                "Trading day starts at",
                value=current["trading_day_start"],
                validation=lambda v: None if _valid_time(v) else "Use HH:MM, e.g. 18:00",
            ).props("dense outlined")
            day_zone = ui.select(
                _TIMEZONES, value=current["trading_day_timezone"], label="…in this timezone", with_input=True
            ).props("dense outlined")
        ui.label(
            "Your timezone is how Tradovate timestamps are read on import and how times are shown. Changing it "
            "affects future imports; trades already imported keep their times. The trading day default is the "
            "CME session: 18:00 New York time starts the next day. Use 00:00 in your own timezone for calendar days."
        ).classes("tj-label")

        def save() -> None:
            if not _valid_time(day_start.value):
                ui.notify("Trading day start must look like 18:00.", type="warning")
                return
            day_changed = (day_start.value, day_zone.value) != (
                current["trading_day_start"],
                current["trading_day_timezone"],
            )
            with open_db() as conn, db.transaction(conn):
                settings.set_setting(conn, "timezone", timezone.value)
                settings.set_setting(conn, "trading_day_start", day_start.value)
                settings.set_setting(conn, "trading_day_timezone", day_zone.value)
                recomputed = store.recalculate_trades(conn) if day_changed else 0
            current.update(
                timezone=timezone.value, trading_day_start=day_start.value, trading_day_timezone=day_zone.value
            )
            ui.notify(
                "Settings saved." + (f" Reassigned trading days for {recomputed} trades." if recomputed else ""),
                type="positive",
            )

        ui.button("Save", on_click=save).props("unelevated no-caps color=primary")


@ui.refreshable
def accounts_list() -> None:
    with open_db() as conn:
        accounts = [
            (a, fee_schedule(conn, a.id), conn.execute("SELECT COUNT(*) FROM trades WHERE account_id = ?", (a.id,)).fetchone()[0])
            for a in list_accounts(conn)
        ]
    if not accounts:
        ui.label("No accounts yet. Create one here or while importing.").classes("tj-label")
    active = [entry for entry in accounts if not entry[0].archived]
    archived = [entry for entry in accounts if entry[0].archived]
    for account_type in ("funded", "evaluation"):
        group = [entry for entry in active if entry[0].account_type == account_type]
        if group:
            ui.label(ACCOUNT_TYPES[account_type]).classes("tj-label font-semibold mt-2")
        for account, fees, trade_count in group:
            _account_editor(account, fees, trade_count)
    if archived:
        archived.sort(key=lambda entry: entry[0].ended_on or date.min, reverse=True)  # most recent first
        with ui.expansion(
            f"Archived ({len(archived)})", caption=archive_summary([a for a, _, _ in archived]), icon="inventory_2"
        ).classes("w-full mt-3").props("dense"):
            for account, fees, trade_count in archived:
                _account_editor(account, fees, trade_count)


def _caption(account: Account, trade_count: int, items: list[ledger.LedgerEntry]) -> str:
    """e.g. "Apex · 720 trades · cost $167.00 · paid out $1,500.00"."""
    parts = [account.firm]
    if account.archived:
        ended = f" {account.ended_on:%b %d, %Y}" if account.ended_on else ""
        parts += [ACCOUNT_TYPES[account.account_type], f"{OUTCOMES[account.outcome]}{ended}"]
    parts.append(f"{trade_count:,} trade{'s' if trade_count != 1 else ''}")
    totals = finance.business_totals(items)
    parts.append(f"cost {fmt.money(totals.costs)}" if totals.costs else "no cost recorded")
    if totals.payouts:
        parts.append(f"paid out {fmt.money(totals.payouts)}")
    return " · ".join(parts)


def _account_editor(account: Account, fees: dict, trade_count: int) -> None:
    with open_db() as conn:
        items = ledger.entries(conn, account.id)
    icon = _OUTCOME_ICONS[account.outcome] if account.archived else "account_balance"
    expansion = ui.expansion(account.name, caption=_caption(account, trade_count, items), icon=icon)
    if account.archived:
        expansion.classes(f"tj-outcome tj-outcome-{account.outcome}")  # passed green, failed red
    with expansion.classes("w-full").props("dense"):
        if account.archived:
            note = account.archive_note.strip()
            if note and note[-1] not in ".!?":
                note += "."
            with ui.row().classes("w-full items-center justify-between tj-archived-note"):
                ui.label(
                    f"Archived: {OUTCOMES[account.outcome].lower()}"
                    + (f" on {account.ended_on:%b %d, %Y}." if account.ended_on else ".")
                    + (f" Note: {note}" if note else "")
                    + " Its trades are hidden unless Show archived is on."
                ).classes("tj-label")
                ui.button("Restore", icon="unarchive", on_click=partial(_restore, account)).props(
                    "flat dense no-caps"
                )
        with ui.element("div").classes("grid gap-3 w-full grid-cols-1 md:grid-cols-5 pt-2"):
            name = ui.input("Name", value=account.name).props("dense outlined")
            account_type = ui.select(ACCOUNT_TYPES, value=account.account_type, label="Account type").props(
                "dense outlined"
            )
            firm = ui.select(list(FIRMS), value=account.firm, label="Firm").props("dense outlined")
            size = amount_input("Account size ($)", account.account_size, required=False)
            balance = amount_input("Starting balance ($)", account.starting_balance)

        ui.label("Fees: $ per contract, round trip").classes("tj-title mt-2")
        ui.label("The default applies to every symbol without its own rate (e.g. NQ, MNQ, ES).").classes("tj-label")
        # (is the default row, symbol root field, rate field)
        rows: list[tuple[bool, ui.input, ui.input]] = []
        fee_rows = ui.column().classes("gap-2")

        def add_row(root: str = "", rate: str = "") -> None:
            is_default = root == DEFAULT_FEE_KEY
            with fee_rows, ui.row().classes("items-center gap-2") as row:
                if is_default:
                    root_field = ui.input("Symbol", value="Default (all symbols)").props("dense outlined readonly")
                else:
                    root_field = ui.input("Symbol root", value=root, placeholder="e.g. MNQ").props("dense outlined")
                rate_field = amount_input("Rate ($)", rate)
                entry = (is_default, root_field, rate_field)
                rows.append(entry)

                def remove() -> None:
                    rows.remove(entry)
                    row.delete()

                if not is_default:
                    ui.button(icon="close", on_click=remove).props('flat round dense aria-label="Remove rate"')

        add_row(DEFAULT_FEE_KEY, str(fees.get(DEFAULT_FEE_KEY, "0")))
        for root, rate in sorted(fees.items()):
            if root != DEFAULT_FEE_KEY:
                add_row(root, str(rate))
        ui.button("Add symbol rate", icon="add", on_click=lambda: add_row()).props("flat dense no-caps")

        def save() -> None:
            try:
                size_value = parse_amount(size.value, required=False)
                balance_value = parse_amount(balance.value)
                new_fees = {}
                for is_default, root_field, rate_field in rows:
                    root = DEFAULT_FEE_KEY if is_default else root_field.value.strip().upper()
                    if not root:
                        continue
                    new_fees[root] = parse_amount(rate_field.value)
            except ValueError:
                ui.notify("Check the highlighted amounts.", type="warning")
                return
            try:
                with open_db() as conn, db.transaction(conn):
                    update_account(
                        conn,
                        account.id,
                        name=name.value,
                        firm=firm.value,
                        account_type=account_type.value,
                        account_size=size_value,
                        starting_balance=balance_value,
                    )
                    for root in fee_schedule(conn, account.id):
                        if root not in new_fees:
                            remove_fee(conn, account.id, root)
                    for root, rate in new_fees.items():
                        set_fee(conn, account.id, root, rate)
                    recomputed = store.recalculate_trades(conn, account.id)
            except sqlite3.IntegrityError:
                ui.notify(f"Another account is already named '{name.value.strip()}'.", type="warning")
                return
            except ValueError as exc:
                ui.notify(str(exc), type="warning")
                return
            ui.notify(f"Saved {name.value.strip()}. Recomputed {recomputed} trades.", type="positive")
            accounts_list.refresh()

        with ui.row().classes("w-full items-center justify-between mt-2"):
            ui.button("Save account", on_click=save).props("unelevated no-caps color=primary")
            if not account.archived:
                ui.button("Archive…", icon="inventory_2", on_click=partial(_archive_dialog, account, fees)).props(
                    "flat no-caps"
                ).tooltip("For an account that passed, failed or closed")

        ui.separator().classes("my-2")
        _ledger_section(account, trade_count, expansion)


def _ledger_section(account: Account, trade_count: int, expansion: ui.expansion) -> None:
    """What the account cost and paid out. Saves as you add or remove entries,
    independent of the Save account button."""
    ui.label("Costs and payouts").classes("tj-title")
    ui.label(
        "Evaluation, reset, activation and monthly fees you paid, and payouts you received. Kept separate "
        "from trading P&L; they add up to your prop business net at the top of this page."
    ).classes("tj-label")
    box = ui.column().classes("w-full gap-0")

    def render() -> list[ledger.LedgerEntry]:
        with open_db() as conn:
            items = ledger.entries(conn, account.id)
        box.clear()
        with box:
            if not items:
                ui.label("No cost recorded yet.").classes("tj-label tj-muted py-1")
            for item in items:
                with ui.row().classes("w-full items-center gap-3 no-wrap tj-ledger-row"):
                    ui.label(f"{item.entry_date:%Y-%m-%d}").classes("tj-num tj-label w-24")
                    ui.label(ledger.KINDS[item.kind]).classes("w-32")
                    ui.label(("+" if item.is_payout else "−") + fmt.money(item.amount)).classes(
                        f"tj-num w-28 text-right {'tj-pos' if item.is_payout else ''}"
                    )
                    ui.label(item.note).classes("tj-label grow min-w-0 truncate")
                    ui.button(icon="close", on_click=partial(remove, item)).props(
                        f'flat round dense aria-label="Delete {ledger.KINDS[item.kind]} of {fmt.money(item.amount)}"'
                    )
            if items:
                totals = finance.business_totals(items)
                with ui.row().classes("w-full gap-6 pt-2"):
                    ui.label(f"Cost {fmt.money(totals.costs)}").classes("tj-label")
                    ui.label(f"Paid out {fmt.money(totals.payouts)}").classes("tj-label")
                    ui.label(f"Net {fmt.money(totals.net)}").classes(f"tj-label font-semibold {fmt.pnl_class(totals.net)}")
        expansion.props["caption"] = _caption(account, trade_count, items)
        return items

    def add() -> None:
        try:
            value = parse_amount(amount.value)
            when = date.fromisoformat(entry_date.value)
        except (TypeError, ValueError):
            ui.notify("Enter an amount and a date (YYYY-MM-DD).", type="warning")
            return
        try:
            with open_db() as conn, db.transaction(conn):
                ledger.add_entry(conn, account.id, kind.value, value, when, note.value or "")
        except ValueError as exc:
            ui.notify(f"Not added: {exc}.", type="warning")
            return
        amount.set_value("")
        note.set_value("")
        items = render()
        kind.set_value(_next_kind(account, items))
        business_summary.refresh()

    def remove(item: ledger.LedgerEntry) -> None:
        with open_db() as conn, db.transaction(conn):
            ledger.delete_entry(conn, item.id)
        ui.notify(f"Deleted {ledger.KINDS[item.kind].lower()} of {fmt.money(item.amount)}.")
        render()
        business_summary.refresh()

    items = render()
    with ui.row().classes("w-full items-center gap-2 pt-2"):
        kind = ui.select(ledger.KINDS, value=_next_kind(account, items), label="Type").props(
            "dense outlined options-dense"
        ).classes("w-40")
        amount = amount_input("Amount ($)", None, required=False).classes("w-32")
        entry_date = date_input("Date", date.today()).classes("w-40")
        note = ui.input("Note (optional)").props("dense outlined hide-bottom-space").classes("grow min-w-[160px]")
        ui.button("Add", icon="add", on_click=add).props("flat dense no-caps")


def _next_kind(account: Account, items: list[ledger.LedgerEntry]) -> str:
    """The likeliest next entry: the purchase fee if none is recorded, else a payout for a
    funded account or a reset for an evaluation."""
    if not any(not item.is_payout for item in items):
        return ledger.default_cost_kind(account.account_type)
    return ledger.PAYOUT if account.account_type == "funded" else "reset_fee"


@ui.refreshable
def business_summary() -> None:
    """Totals across every account, archived ones included."""
    with open_db() as conn:
        items = ledger.entries(conn)
        everyone = list_accounts(conn)
    totals = finance.business_totals(items)
    with_cost = {item.account_id for item in items if not item.is_payout}
    missing = [a for a in everyone if a.id not in with_cost]
    with ui.row().classes("w-full items-end gap-8 tj-business"):
        for label, value, value_class in (
            ("Spent on accounts", fmt.money(totals.costs), ""),
            ("Payouts received", fmt.money(totals.payouts), "tj-pos" if totals.payouts else ""),
            ("Prop business net", fmt.money(totals.net), fmt.pnl_class(totals.net)),
        ):
            with ui.column().classes("gap-0"):
                ui.label(label).classes("tj-label")
                ui.label(value).classes(f"tj-value {value_class}")
        if missing:
            names = ", ".join(a.name for a in missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
            ui.label(
                f"{len(missing)} account{'s have' if len(missing) != 1 else ' has'} no cost recorded ({names}). "
                "Open an account below to add one."
            ).classes("tj-label tj-muted grow")


def _archive_dialog(account: Account, fees: dict) -> None:
    """Ask how the account ended, then archive it. A passed evaluation can go straight
    on to setting up its funded account."""
    with open_db() as conn:
        last_day = last_trading_day(conn, account.id)
    choices = {o: OUTCOMES[o] for o in OUTCOMES_BY_TYPE[account.account_type]}
    # Dialogs live at the page root: the accounts list redraws underneath them.
    with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes("tj-card w-[460px] max-w-[95vw] gap-3"):
        ui.label(f"Archive {account.name}").classes("tj-title")
        ui.label(
            "Its trades stay in your journal. They're hidden from the dashboard and trade log unless you turn on "
            "Show archived, and you can restore the account at any time."
        ).classes("tj-label")
        outcome = ui.toggle(choices, value=None).props("no-caps")
        ended = date_input("Ended on", last_day or date.today()).classes("w-full")
        ui.label("Defaults to the account's last trading day.").classes("tj-label -mt-2")
        note = ui.textarea("Note (optional)", placeholder="e.g. Hit the trailing drawdown on CPI day").props(
            "dense outlined autogrow"
        ).classes("w-full")
        next_funded = ui.checkbox("Set up the funded account next", value=True).bind_visibility_from(
            outcome, "value", backward=lambda value: value == "passed"
        )

        def archive() -> None:
            if outcome.value is None:
                ui.notify(f"Choose how {account.name} ended.", type="warning")
                return
            try:
                ended_on = date.fromisoformat(ended.value)
            except (TypeError, ValueError):
                ui.notify("Enter the end date as YYYY-MM-DD.", type="warning")
                return
            with open_db() as conn, db.transaction(conn):
                archive_account(conn, account.id, outcome=outcome.value, ended_on=ended_on, note=note.value or "")
            ui.notify(f"Archived {account.name} ({OUTCOMES[outcome.value].lower()}).", type="positive")
            accounts_list.refresh()
            if outcome.value == "passed" and next_funded.value:
                with ui.context.client.layout:
                    new_account_dialog(
                        _account_added,
                        title="New funded account",
                        based_on=account,
                        account_type="funded",
                        fee_default=fees.get(DEFAULT_FEE_KEY),
                    )
            dialog.close()  # last: closing deletes the dialog and its event context

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button("Archive", icon="inventory_2", on_click=archive).props("unelevated no-caps color=primary")
    dialog.on_value_change(lambda e: None if e.value else dialog.delete())
    dialog.open()


def _account_added(_: Account) -> None:
    business_summary.refresh()  # a cost entered at creation changes the totals
    accounts_list.refresh()


def _restore(account: Account) -> None:
    with open_db() as conn, db.transaction(conn):
        restore_account(conn, account.id)
    ui.notify(f"{account.name} is active again.", type="positive")
    accounts_list.refresh()  # last: redraws the list this button is in


def _valid_time(text: str) -> bool:
    try:
        hours, minutes = (int(part) for part in (text or "").split(":"))
    except ValueError:
        return False
    return 0 <= hours < 24 and 0 <= minutes < 60


@ui.refreshable
def tags_editor() -> None:
    with open_db() as conn:
        groups = journal.tag_groups(conn)
        known = journal.tags(conn)
    for group in groups:
        group_tags = [t for t in known if t.group_id == group.id]
        with ui.row().classes("w-full items-center gap-2 py-1"):
            ui.button(group.name, color=None, on_click=partial(_category_dialog, group, group_tags)).props(
                f'flat dense no-caps align=left icon-right=edit aria-label="Rename or delete the {group.name} category"'
            ).classes("tj-category")
            for tag in group_tags:
                tag_pill(tag, action="edit", count=tag.trade_count, on_click=partial(_tag_dialog, tag, groups))
            with ui.row().classes("items-center gap-0 no-wrap"):
                new_tag = ui.input(placeholder="Add a tag").props("dense outlined hide-bottom-space").classes("w-36")
                new_tag.on("keydown.enter", partial(_add_tag, group.id, new_tag))
                ui.button(icon="add", on_click=partial(_add_tag, group.id, new_tag)).props(
                    f'flat round dense aria-label="Add the tag to {group.name}"'
                )
    with ui.row().classes("items-center gap-2 mt-2"):
        new_group = ui.input(placeholder="New category, e.g. Market condition").props(
            "dense outlined hide-bottom-space"
        ).classes("w-72")
        new_group.on("keydown.enter", partial(_add_group, new_group))
        ui.button("Add category", icon="add", on_click=partial(_add_group, new_group)).props("flat no-caps")


def _add_tag(group_id: int, field: ui.input) -> None:
    try:
        with open_db() as conn, db.transaction(conn):
            journal.get_or_create_tag(conn, group_id, field.value)
    except ValueError as exc:
        ui.notify(f"Tag not added: {exc}.", type="warning")
        return
    tags_editor.refresh()


def _add_group(field: ui.input) -> None:
    try:
        with open_db() as conn, db.transaction(conn):
            journal.create_group(conn, field.value)
    except ValueError as exc:
        ui.notify(f"Category not added: {exc}.", type="warning")
        return
    tags_editor.refresh()


def _tag_dialog(tag: journal.Tag, groups: list[journal.TagGroup]) -> None:
    """Rename a tag, change its color or category, merge it into another, or delete it."""
    with open_db() as conn:
        yours = journal.custom_colors(conn)
    chosen = {"color": tag.color}
    group_names = {g.id: g.name for g in groups}
    trades = f"{tag.trade_count:,} trade{'s' if tag.trade_count != 1 else ''}"

    dialog, body = _editor_dialog("Edit tag")
    with body:
        with ui.row().classes("items-center gap-3"):
            with ui.element("span").classes("tj-tag").style(tag_style(tag.color)) as preview:
                preview_name = ui.label(tag.name)
            ui.label(f"On {trades}" if tag.trade_count else "Not on any trades yet").classes("tj-label")
        with ui.row().classes("w-full gap-3 no-wrap"):
            name = ui.input(
                "Name", value=tag.name, on_change=lambda e: preview_name.set_text(e.value or "")
            ).props("dense outlined").classes("flex-1")
            category = ui.select(group_names, value=tag.group_id, label="Category").props("dense outlined").classes(
                "flex-1"
            )
        ui.label("Color").classes("tj-label")
        with ui.row().classes("items-center gap-3 no-wrap"):
            ui.label("Presets").classes("tj-label tj-muted w-20")
            presets_box = ui.row().classes("items-center gap-2")
        with ui.row().classes("items-center gap-3 no-wrap"):
            ui.label("Your colors").classes("tj-label tj-muted w-20")
            yours_box = ui.row().classes("items-center gap-2")
            # Outside the redrawn boxes, so the picker stays open while you try colors.
            with ui.element("button").classes("tj-swatch tj-swatch-custom").props(
                'type=button aria-label="Pick any color" title="Pick any color"'
            ):
                ui.icon("add")
                picker = ui.color_picker(on_pick=lambda e: pick(e.color))
                picker.q_color.props("format-model=hex")
            hint = ui.label("Pick any color; it's kept here to reuse.").classes("tj-label tj-muted")
        footer = ui.column().classes("w-full gap-2 mt-1")

    def draw_swatches() -> None:
        color = chosen["color"]
        trying = [] if color in journal.TAG_COLOR_PRESETS or color in yours else [color]  # one, until saved
        for box, colors in ((presets_box, list(journal.TAG_COLOR_PRESETS)), (yours_box, yours + trying)):
            box.clear()
            with box:
                for option in colors:
                    _swatch(option, option == color, partial(pick, option))
        hint.set_visibility(not yours and not trying)
        picker.set_color(color)

    def pick(color: str) -> None:
        try:
            chosen["color"] = journal.clean_color(color)
        except ValueError:
            return
        preview.style(replace=tag_style(chosen["color"]))
        draw_swatches()

    def show_main() -> None:
        _footer(
            footer,
            delete=("Delete tag", ask_delete),
            actions=[("Cancel", dialog.close, "flat"), ("Save", save, "primary")],
        )

    def ask_delete() -> None:
        if not tag.trade_count:
            delete()
            return
        _footer(
            footer,
            note=f"Delete '{tag.name}'? It's on {trades}, which will lose it.",
            actions=[("Back", show_main, "flat"), ("Delete tag", delete, "negative")],
        )

    def delete() -> None:
        with open_db() as conn, db.transaction(conn):
            journal.delete_tag(conn, tag.id)
        ui.notify(f"Deleted the tag '{tag.name}'.")
        dialog.close()  # last: closing deletes the dialog and its event context

    def save(merge: bool = False) -> None:
        try:
            new_name = journal.clean_name(name.value)
        except ValueError as exc:
            ui.notify(f"Not saved: {exc}.", type="warning")
            return
        with open_db() as conn:
            clash = journal.find_tag(conn, category.value, new_name)
        if clash and clash.id != tag.id:
            if not merge:
                _footer(
                    footer,
                    note=(
                        f"{group_names[category.value]} already has a tag called '{clash.name}'. Merge into it? "
                        + (f"The {trades} tagged '{tag.name}' will carry '{clash.name}' instead, and " if tag.trade_count else "")
                        + f"'{tag.name}' will be deleted. '{clash.name}' keeps its own color."
                    ),
                    actions=[("Back", show_main, "flat"), ("Merge", lambda: save(merge=True), "primary")],
                )
                return
            with open_db() as conn, db.transaction(conn):
                journal.merge_tags(conn, tag.id, clash.id)
            ui.notify(f"Merged '{tag.name}' into '{clash.name}'.", type="positive")
        else:
            with open_db() as conn, db.transaction(conn):
                journal.update_tag(conn, tag.id, name=new_name, group_id=category.value, color=chosen["color"])
            ui.notify(f"Saved '{new_name}'.", type="positive")
        dialog.close()  # last

    name.on("keydown.enter", lambda: save())
    draw_swatches()
    show_main()
    dialog.open()


def _swatch(color: str, selected: bool, on_click) -> None:
    label = journal.TAG_COLOR_PRESETS.get(color, color)
    swatch = ui.element("button").classes("tj-swatch" + (" tj-swatch-on" if selected else ""))
    swatch.style(f"background: {color}; color: {on_color(color)}")
    swatch.props(f'type=button title="{label}" aria-label="{label}" aria-pressed={str(selected).lower()}')
    swatch.on("click", on_click)
    if selected:
        with swatch:
            ui.icon("check")


def _category_dialog(group: journal.TagGroup, group_tags: list[journal.Tag]) -> None:
    """Rename a category, or delete it along with its tags."""
    count = f"{len(group_tags)} tag{'s' if len(group_tags) != 1 else ''}"
    tagged = sum(t.trade_count for t in group_tags)
    dialog, body = _editor_dialog("Edit category")
    with body:
        name = ui.input("Name", value=group.name).props("dense outlined autofocus").classes("w-full")
        ui.label(
            f"{count}, on trades {tagged:,} time{'s' if tagged != 1 else ''}" if tagged else count
        ).classes("tj-label")
        footer = ui.column().classes("w-full gap-2 mt-1")

    def show_main() -> None:
        _footer(
            footer,
            delete=("Delete category", ask_delete),
            actions=[("Cancel", dialog.close, "flat"), ("Save", save, "primary")],
        )

    def ask_delete() -> None:
        if not group_tags:
            delete()
            return
        _footer(
            footer,
            note=f"Delete {group.name} and its {count}?"
            + (f" They're on trades {tagged:,} time{'s' if tagged != 1 else ''}; those trades lose them." if tagged else ""),
            actions=[("Back", show_main, "flat"), ("Delete category", delete, "negative")],
        )

    def delete() -> None:
        with open_db() as conn, db.transaction(conn):
            journal.delete_group(conn, group.id)
        ui.notify(f"Deleted the {group.name} category.")
        dialog.close()  # last

    def save() -> None:
        try:
            with open_db() as conn, db.transaction(conn):
                renamed = journal.rename_group(conn, group.id, name.value)
        except ValueError as exc:
            ui.notify(f"Not saved: {exc}.", type="warning")
            return
        if renamed.name != group.name:
            ui.notify(f"Renamed {group.name} to {renamed.name}.", type="positive")
        dialog.close()  # last

    name.on("keydown.enter", save)
    show_main()
    dialog.open()


def _editor_dialog(title: str) -> tuple[ui.dialog, ui.card]:
    """A dialog for the tags editor. It lives at the page root so redraws can't remove it,
    and redraws the tags from the database once it closes, whatever happened inside."""
    with ui.context.client.layout, ui.dialog() as dialog, ui.card().classes(
        "tj-card w-[500px] max-w-[95vw] gap-3"
    ) as body:
        ui.label(title).classes("tj-title")
    dialog.on_value_change(lambda e: None if e.value else (dialog.delete(), tags_editor.refresh()))
    return dialog, body


_BUTTON_PROPS = {
    "flat": "flat no-caps",
    "primary": "unelevated no-caps color=primary",
    "negative": "unelevated no-caps color=negative",
}


def _footer(box: ui.column, *, note: str = "", delete: tuple | None = None, actions: list[tuple]) -> None:
    """Fill a dialog's footer: an optional note (a question to confirm), an optional
    delete button on the left, and (label, handler, style) buttons on the right."""
    box.clear()
    with box:
        if note:
            ui.label(note).classes("tj-confirm w-full")
        with ui.row().classes("w-full items-center gap-2"):
            if delete:
                label, handler = delete
                ui.button(label, icon="delete_outline", on_click=handler).props("flat no-caps color=negative")
            ui.space()
            for label, handler, style in actions:
                ui.button(label, on_click=handler).props(_BUTTON_PROPS[style])
