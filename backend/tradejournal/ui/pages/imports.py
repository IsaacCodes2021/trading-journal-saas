"""Import page: pick an account, drop CSVs, preview, import; history with undo."""

from __future__ import annotations

from nicegui import events, run, ui

from ... import importer
from ...accounts import ACCOUNT_TYPES, Account, list_accounts
from ...importer import FileImportReport, ImportReport, UndoReport
from ...timeutil import from_utc_iso
from .. import fmt
from ..data import display_zone, open_db
from ..layout import card, frame
from ..widgets import new_account_dialog


def _run_import(account_id: int, files: list[tuple[str, bytes]], dry_run: bool) -> ImportReport:
    with open_db() as conn:  # runs in a worker thread, so it opens its own connection
        return importer.import_files(conn, account_id, files, dry_run=dry_run)


def _run_undo(import_id: int, dry_run: bool) -> UndoReport:
    with open_db() as conn:
        return importer.undo_import(conn, import_id, dry_run=dry_run)


def _account_options() -> dict[int, str]:
    """Active accounts only; restore an archived account in Settings to import into it."""
    with open_db() as conn:
        return {
            a.id: f"{a.name} ({ACCOUNT_TYPES[a.account_type]} · {a.firm})"
            for a in list_accounts(conn, include_archived=False)
        }


@ui.page("/import")
def import_page() -> None:
    zone = display_zone()
    pending: dict[str, bytes] = {}  # file name -> content, in the order dropped
    preview: dict[str, ImportReport | None] = {"report": None}

    async def refresh_preview() -> None:
        preview["report"] = None
        if account_select.value is not None and pending:
            preview["report"] = await run.io_bound(_run_import, account_select.value, list(pending.items()), True)
        files_section.refresh()
        preview_section.refresh()

    async def files_received(e: events.MultiUploadEventArguments) -> None:
        for upload in e.files:
            pending[upload.name] = await upload.read()
        uploader.reset()
        await refresh_preview()

    async def remove_file(name: str) -> None:
        pending.pop(name, None)
        await refresh_preview()

    async def clear_files() -> None:
        pending.clear()
        await refresh_preview()

    async def do_import() -> None:
        report = preview["report"]
        if report is None or account_select.value is None:
            return
        import_button.props("loading")
        result = await run.io_bound(_run_import, account_select.value, list(pending.items()), False)
        added = sum(f.rows_added for f in result.files)
        created = sum(f.trades_created for f in result.files)
        extended = sum(f.trades_extended for f in result.files)
        ui.notify(
            f"Imported {added} fill pairs: {created} new trades, {extended} extended.",
            type="positive",
        )
        pending.clear()
        preview["report"] = None
        files_section.refresh()
        preview_section.refresh()
        history_section.refresh()

    def account_created(account: Account) -> None:
        account_select.set_options(_account_options(), value=account.id)

    @ui.refreshable
    def files_section() -> None:
        if not pending:
            return
        with ui.row().classes("w-full items-center gap-2"):
            for name in pending:
                ui.chip(
                    name, icon="description", removable=True, on_value_change=lambda _, n=name: remove_file(n)
                ).props("outline dense")
            ui.button("Clear all", on_click=clear_files).props("flat dense no-caps").classes("tj-secondary")

    @ui.refreshable
    def preview_section() -> None:
        nonlocal import_button
        report = preview["report"]
        if not pending:
            return
        if account_select.value is None:
            ui.label("Pick the account these files belong to.").classes("tj-label")
            return
        if report is None:
            ui.spinner()
            return
        _preview_table(report)
        _preview_issues(report)
        added = sum(f.rows_added for f in report.files)
        created = sum(f.trades_created for f in report.files)
        extended = sum(f.trades_extended for f in report.files)
        with ui.row().classes("w-full items-center justify-between"):
            if added:
                ui.label(
                    f"Ready: {added} new fill pairs → {created} new trade{'s' if created != 1 else ''}"
                    + (f", {extended} existing trade{'s' if extended != 1 else ''} extended" if extended else "")
                    + "."
                ).classes("tj-secondary")
            else:
                ui.label("Nothing new to import: every row is already in the journal or was rejected.").classes(
                    "tj-secondary"
                )
            ready = sum(1 for f in report.files if f.rows_added)
            import_button = ui.button(
                f"Import {ready} file{'s' if ready != 1 else ''}", icon="download_done", on_click=do_import
            ).props("unelevated no-caps color=primary")
            import_button.set_enabled(added > 0)

    @ui.refreshable
    def history_section() -> None:
        with open_db() as conn:
            records = importer.list_imports(conn)
        if not records:
            ui.label("No imports yet.").classes("tj-label")
            return
        rows = [
            {
                "id": r["id"],
                "when": fmt.local(from_utc_iso(r["imported_at"]), zone, "%Y-%m-%d %H:%M"),
                "account": r["account_name"],
                "file": r["filename"],
                "rows": r["rows_parsed"],
                "added": r["rows_added"],
                "dupes": r["rows_duplicate"],
                "errors": r["rows_error"],
                "trades": r["trades_affected"],
                "status": "Undone" if r["undone_at"] else "",
            }
            for r in records
        ]
        table = (
            ui.table(
                columns=[
                    {"name": "id", "label": "#", "field": "id", "align": "left"},
                    {"name": "when", "label": "Imported", "field": "when", "align": "left"},
                    {"name": "account", "label": "Account", "field": "account", "align": "left"},
                    {"name": "file", "label": "File", "field": "file", "align": "left"},
                    {"name": "rows", "label": "Rows", "field": "rows", "align": "right"},
                    {"name": "added", "label": "Added", "field": "added", "align": "right"},
                    {"name": "dupes", "label": "Duplicates", "field": "dupes", "align": "right"},
                    {"name": "errors", "label": "Errors", "field": "errors", "align": "right"},
                    {"name": "trades", "label": "Trades", "field": "trades", "align": "right"},
                    {"name": "action", "label": "", "field": "status", "align": "right"},
                ],
                rows=rows,
                row_key="id",
                pagination=20,
            )
            .classes("w-full tj-table")
            .props("flat dense")
        )
        table.add_slot(
            "body-cell-action",
            """
            <q-td :props="props">
              <span v-if="props.row.status" class="tj-muted">{{ props.row.status }}</span>
              <q-btn v-else flat dense no-caps size="sm" icon="undo" label="Undo"
                     @click="() => $parent.$emit('undo', props.row)" />
            </q-td>
            """,
        )
        table.on("undo", lambda e: confirm_undo(e.args["id"], e.args["file"]))

    async def confirm_undo(import_id: int, filename: str) -> None:
        try:
            effect = await run.io_bound(_run_undo, import_id, True)
        except (LookupError, ValueError) as exc:
            ui.notify(str(exc), type="warning")
            return
        with ui.dialog() as dialog, ui.card().classes("tj-card w-[480px] max-w-[95vw] gap-3"):
            ui.label(f"Undo import #{import_id}?").classes("tj-title")
            ui.label(filename).classes("tj-label")
            ui.label(
                f"Removes {effect.rows_removed} fill pair{'s' if effect.rows_removed != 1 else ''} and "
                f"{effect.trades_removed} trade{'s' if effect.trades_removed != 1 else ''}"
                + (
                    f"; {effect.trades_changed} trade{'s' if effect.trades_changed != 1 else ''} will be regrouped"
                    if effect.trades_changed
                    else ""
                )
                + "."
            )
            if effect.rows_kept:
                ui.label(
                    f"{effect.rows_kept} fill pair{'s' if effect.rows_kept != 1 else ''} stay because another import also contains them."
                ).classes("tj-label")
            if effect.trades_with_journal_removed:
                ui.label(
                    f"{effect.trades_with_journal_removed} of the removed trades have notes, tags or screenshots, "
                    "which will be deleted too."
                ).classes("tj-neg")

            async def undo() -> None:
                await run.io_bound(_run_undo, import_id, False)
                ui.notify(f"Import #{import_id} undone.", type="positive")
                history_section.refresh()
                dialog.close()  # last: closing deletes the dialog and its event context

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Undo import", on_click=undo).props("unelevated no-caps color=negative")
        dialog.on_value_change(lambda e: None if e.value else dialog.delete())
        dialog.open()

    import_button: ui.button | None = None
    with frame("import"):
        with card(
            "Import Tradovate Performance CSVs",
            "Pick the account the files came from, drop one or more files, check the preview, then import.",
        ):
            options = _account_options()
            with ui.row().classes("items-center gap-3"):
                account_select = (
                    ui.select(
                        options,
                        label="Account",
                        value=next(iter(options), None) if len(options) == 1 else None,
                        on_change=refresh_preview,
                    )
                    .props("dense outlined")
                    .classes("min-w-[260px]")
                )
                ui.button("New account", icon="add", on_click=lambda: new_account_dialog(account_created)).props(
                    "flat no-caps"
                )
            uploader = (
                ui.upload(
                    multiple=True,
                    auto_upload=True,
                    on_multi_upload=files_received,
                    label="Drop CSV files here, or click to choose",
                )
                .props('accept=".csv,text/csv" flat bordered')
                .classes("tj-upload")
            )
            files_section()
            preview_section()
        with card("Import history", "Undo removes the rows an import added and regroups what's left."):
            history_section()


def _preview_table(report: ImportReport) -> None:
    rows = []
    for f in report.files:
        other = sum(f.duplicates_other_accounts.values())
        if f.fatal:
            status = "Rejected"
        elif f.rows_added:
            status = "Ready"
        else:
            status = "Nothing new"
        rows.append(
            {
                "file": f.filename,
                "status": status,
                "rows": f.rows_parsed,
                "new": f.rows_added,
                "dupes": f.duplicates + f.duplicates_in_file,
                "other": other,
                "errors": len(f.errors),
                "warnings": len(f.warnings),
                "created": f.trades_created,
                "extended": f.trades_extended,
            }
        )
    ui.table(
        columns=[
            {"name": "file", "label": "File", "field": "file", "align": "left"},
            {"name": "status", "label": "Status", "field": "status", "align": "left"},
            {"name": "rows", "label": "Rows parsed", "field": "rows", "align": "right"},
            {"name": "new", "label": "New", "field": "new", "align": "right"},
            {"name": "dupes", "label": "Duplicates", "field": "dupes", "align": "right"},
            {"name": "other", "label": "In other account", "field": "other", "align": "right"},
            {"name": "errors", "label": "Errors", "field": "errors", "align": "right"},
            {"name": "warnings", "label": "Warnings", "field": "warnings", "align": "right"},
            {"name": "created", "label": "New trades", "field": "created", "align": "right"},
            {"name": "extended", "label": "Extended trades", "field": "extended", "align": "right"},
        ],
        rows=rows,
        row_key="file",
    ).classes("w-full tj-table").props("flat dense hide-bottom")


def _preview_issues(report: ImportReport) -> None:
    for f in report.files:
        messages = _issues(f)
        if not messages:
            continue
        icon = "error" if f.fatal or f.errors else "info"
        with (
            ui.expansion(f"{f.filename}: {len(messages)} note{'s' if len(messages) != 1 else ''}", icon=icon)
            .classes("w-full")
            .props("dense")
        ):
            for message in messages:
                ui.label(message).classes("tj-label")


def _issues(f: FileImportReport) -> list[str]:
    messages = []
    if f.fatal:
        messages.append(f.fatal)
    messages += [f"Line {issue.line}: {issue.message} (row skipped)" for issue in f.errors]
    messages += [f"Line {issue.line}: {issue.message}" for issue in f.warnings]
    messages += [
        f"{count} row{'s' if count != 1 else ''} already belong to account '{name}' and will be skipped."
        for name, count in f.duplicates_other_accounts.items()
    ]
    if f.previous_imports:
        messages.append(
            "This exact file was imported before (import #" + ", #".join(map(str, f.previous_imports)) + ")."
        )
    return messages
