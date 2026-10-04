"""Everything a person has stored, as a ZIP they can keep (requirement F-A5, doc 03 section 8 "Export").

What is in the ZIP
    profile.json                 who the account is and its settings
    accounts.csv, fee_schedules.csv, imports.csv, trades.csv, fill_pairs.csv, import_fill_pairs.csv,
    tag_groups.csv, tags.csv, trade_tags.csv, ledger.csv, screenshots.csv      one file per table, same column names
    notes/trade-<id>.html and .txt, notes/day-<date>.html and .txt            the notes, as written and as plain text
    screenshots/<id>.<ext>       the image files, named by the id column of screenshots.csv (when the server can read them)
    manifest.json                every file with its size, row count and SHA-256, and how to read the values

Rules
- **Complete by construction.** Each table's columns are read from the catalog, so a column added later is exported
  without anyone remembering to; and `COVERAGE` must name every table that holds a person's rows (a test compares it
  with the catalog), so a new table fails the build until someone decides where it goes.
- **Exact.** Money and prices are decimal strings as stored ("12.50", never a float or 1.25E+1); times are UTC in ISO 8601
  with a Z; dates are ISO dates. An empty cell is a null or an empty text (the two cannot be told apart in CSV).
- **Only this person's rows.** The caller passes a connection from `tenant_session`, so row-level security decides what
  can be read; the export has no way to name another person. The caller should ask for `snapshot=True`, so that all the
  tables are read as of one moment.
- **Streamed.** Rows come through a server-side cursor in batches and go straight into the ZIP, so a large journal does
  not have to fit in memory; the finished file is held in a spooled temporary file (memory up to 16 MB, then disk).

Images are copied as they are stored and are not compressed again (they already are). One whose file cannot be found is
left out and its id is listed in the manifest (`screenshot_files.missing`), so a gap is never silent; a server with no
file store says so (`screenshot_files.included` is false). The stored file name is internal and is not exported.

Not exported: `user_usage` (counters derived from the rows above).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape, unescape
from typing import IO, Any

import nh3
import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

from ..filestore import BadKey, FileMissing, FileStore

FORMAT = "roundturn-export"
SCHEMA_VERSION = 1
BATCH = 2000
SPOOL_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Table:
    file: str
    table: str
    order: str  # ORDER BY, so two exports of the same data are the same bytes
    leave_out: frozenset[str] = field(default_factory=lambda: frozenset({"user_id"}))


TABLES = (
    Table("accounts.csv", "accounts", "id"),
    Table("fee_schedules.csv", "fee_schedules", "account_id, symbol_root"),
    Table("imports.csv", "imports", "id"),
    Table("trades.csv", "trades", "id", frozenset({"user_id", "notes"})),  # notes go in notes/, where they are readable
    Table("fill_pairs.csv", "fill_pairs", "id"),
    Table("import_fill_pairs.csv", "import_fill_pairs", "import_id, fill_pair_id"),
    Table("tag_groups.csv", "tag_groups", "id"),
    Table("tags.csv", "tags", "id"),
    Table("trade_tags.csv", "trade_tags", "trade_id, tag_id"),
    Table("ledger.csv", "account_ledger", "id"),
    Table("screenshots.csv", "screenshots", "id", frozenset({"user_id", "stored_name"})),
)

# Every table that holds one person's rows, and where it went (or why it did not).
COVERAGE: dict[str, str] = {
    **{t.table: t.file for t in TABLES},
    "day_notes": "notes/day-<date>.html and .txt",
    "user_settings": "profile.json",
}
NOT_EXPORTED: dict[str, str] = {"user_usage": "counters derived from the rows that are exported"}

# Every note file starts with this: a policy that allows no script and no loading of anything, so a note opened in a
# browser cannot run code even if an old row was never sanitized.
_NOTE_HEAD = (
    '<!doctype html>\n<meta charset="utf-8">\n'
    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
)
_BLOCK_END = re.compile(r"</(?:p|div|li|h[1-6]|blockquote|ul|ol)>|<br\s*/?>", re.IGNORECASE)
_LIST_ITEM = re.compile(r"<li[^>]*>", re.IGNORECASE)


def html_to_text(html: str) -> str:
    """A note's HTML as plain text: one line per paragraph or list item, no tags, entities turned back into characters."""
    spaced = _LIST_ITEM.sub("- ", _BLOCK_END.sub("\n", html))
    text = unescape(nh3.clean(spaced, tags=set(), attributes={}))  # every tag out; script and style content with it
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def cell(value: Any) -> str:
    """One value as the text a CSV cell holds (see "Exact" above)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class _Entry(io.RawIOBase):
    """A file being written into the ZIP that counts and hashes what passes through it."""

    def __init__(self, target: IO[bytes]) -> None:
        self._target = target
        self.size = 0
        self.sha256 = hashlib.sha256()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:  # type: ignore[override]
        chunk = bytes(data)
        self._target.write(chunk)
        self.sha256.update(chunk)
        self.size += len(chunk)
        return len(chunk)


@dataclass
class ExportResult:
    file: IO[bytes]  # positioned at the start, ready to be read; the caller closes it
    size: int
    counts: dict[str, int]  # file name to rows (CSV) or 1 (anything else): what the audit log may say, nothing more


class _Writer:
    def __init__(self, archive: zipfile.ZipFile, moment: datetime) -> None:
        self._zip = archive
        self._stamp = moment.astimezone(UTC).timetuple()[:6]
        self.manifest: dict[str, dict[str, Any]] = {}

    def _info(self, name: str, *, compress: bool = True) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=self._stamp)
        info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        info.external_attr = 0o600 << 16
        return info

    def add_bytes(self, name: str, data: bytes, rows: int | None = None, *, compress: bool = True) -> None:
        with self._zip.open(self._info(name, compress=compress), "w") as raw:
            entry = _Entry(raw)
            entry.write(data)
        self.manifest[name] = _described(entry, rows)

    def add_csv(self, name: str, header: list[str], rows: Iterator[list[str]]) -> int:
        count = 0
        with self._zip.open(self._info(name), "w") as raw:
            entry = _Entry(raw)
            text = io.TextIOWrapper(io.BufferedWriter(entry), encoding="utf-8", newline="")  # type: ignore[arg-type]
            writer = csv.writer(text)
            writer.writerow(header)
            for row in rows:
                writer.writerow(row)
                count += 1
            text.flush()
            text.detach()
        self.manifest[name] = _described(entry, count)
        return count


def _described(entry: _Entry, rows: int | None) -> dict[str, Any]:
    described: dict[str, Any] = {"bytes": entry.size, "sha256": entry.sha256.hexdigest()}
    if rows is not None:
        described["rows"] = rows
    return described


def _columns(conn: psycopg.Connection, table: str, leave_out: frozenset[str]) -> list[str]:
    rows = conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position""",
        (table,),
    ).fetchall()
    return [r["column_name"] for r in rows if r["column_name"] not in leave_out]


def _stream(conn: psycopg.Connection, name: str, query: sql.Composable) -> Iterator[tuple]:
    """Rows through a server-side cursor, `BATCH` at a time, so the whole table is never in memory."""
    with conn.cursor(name=name, row_factory=tuple_row) as cursor:
        cursor.itersize = BATCH
        cursor.execute(query)
        yield from cursor


def build(
    conn: psycopg.Connection,
    *,
    profile: dict[str, Any],
    exported_at: datetime,
    app_version: str,
    clean_html: Callable[[str], str],
    store: FileStore | None = None,
) -> ExportResult:
    """Read everything the connection's tenant may see into one ZIP. `clean_html` is the notes sanitizer: notes are
    cleaned again on the way out, so a row that predates the sanitizer cannot carry a script into a downloaded file.
    `store` is where the image files are kept: with none, the ZIP says it holds no images."""
    spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES)  # noqa: SIM115 - closed by the caller
    counts: dict[str, int] = {}
    try:
        with zipfile.ZipFile(spool, "w", allowZip64=True) as archive:
            files = _Writer(archive, exported_at)
            files.add_bytes("profile.json", _json(profile))
            counts["profile.json"] = 1

            for number, table in enumerate(TABLES):
                columns = _columns(conn, table.table, table.leave_out)
                query = sql.SQL("SELECT {cols} FROM {table} ORDER BY {order}").format(
                    cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                    table=sql.Identifier(table.table),
                    order=sql.SQL(table.order),
                )
                rows = ([cell(v) for v in row] for row in _stream(conn, f"export_{number}", query))
                counts[table.file] = files.add_csv(table.file, columns, rows)

            counts["notes"] = _notes(conn, files, clean_html)
            written, missing = _images(conn, files, store) if store is not None else (0, [])
            counts["screenshots"] = written

            files.add_bytes(
                "manifest.json",
                _json(
                    {
                        "format": FORMAT,
                        "schema_version": SCHEMA_VERSION,
                        "exported_at": cell(exported_at),
                        "app_version": app_version,
                        "how_to_read": {
                            "text": "UTF-8. CSV files have a header row; commas, quotes and line breaks inside values are quoted.",
                            "empty_cell": "no value (a null, or an empty text: CSV cannot tell them apart)",
                            "money_and_prices": "exact decimal strings in US dollars, as stored; never rounded",
                            "times": "UTC, ISO 8601 with a Z",
                            "dates": "ISO 8601 dates; a trading day is the day your trading-day settings put the trade in",
                            "notes": "notes/ holds each non-empty note as a small HTML file and as plain text",
                            "screenshots": (
                                "screenshots.csv lists them; each image file is screenshots/<id>.<extension>, "
                                "with <id> from the id column"
                            ),
                            "not_included": sorted(NOT_EXPORTED),
                        },
                        "screenshot_files": {"included": store is not None, "count": written, "missing": missing},
                        "files": dict(sorted(files.manifest.items())),
                    }
                ),
            )
        size = spool.tell()
        spool.seek(0)
        return ExportResult(file=spool, size=size, counts=counts)
    except BaseException:
        spool.close()
        raise


def _notes(conn: psycopg.Connection, files: _Writer, clean_html: Callable[[str], str]) -> int:
    count = 0
    trade_notes = _stream(
        conn, "export_trade_notes", sql.SQL("SELECT id, notes FROM trades WHERE notes <> '' ORDER BY id")
    )
    for trade_id, notes in trade_notes:
        count += _note(files, f"notes/trade-{trade_id}", f"Trade {trade_id}", notes, clean_html)
    day_notes = _stream(
        conn,
        "export_day_notes",
        sql.SQL("SELECT trade_date, notes FROM day_notes WHERE notes <> '' ORDER BY trade_date"),
    )
    for day, notes in day_notes:
        count += _note(files, f"notes/day-{day.isoformat()}", f"Notes for {day.isoformat()}", notes, clean_html)
    return count


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _images(conn: psycopg.Connection, files: _Writer, store: FileStore) -> tuple[int, list[int]]:
    """Copy each screenshot's file into `screenshots/<id>.<ext>`. Returns how many were written and the ids whose file
    could not be found (a row with no file: one made before the files were kept here, or one lost since)."""
    written, missing = 0, []
    rows = _stream(conn, "export_images", sql.SQL("SELECT id, stored_name FROM screenshots ORDER BY id"))
    for screenshot_id, key in rows:
        try:
            data = store.get(key)
        except (FileMissing, BadKey):
            missing.append(screenshot_id)
            continue
        extension = os.path.splitext(key)[1]  # a key is always lower case (the store refuses any other)
        files.add_bytes(
            f"screenshots/{screenshot_id}{extension if extension in _IMAGE_EXTENSIONS else '.bin'}",
            data,
            compress=False,
        )
        written += 1
    return written, missing


def _note(files: _Writer, stem: str, title: str, html: str, clean_html: Callable[[str], str]) -> int:
    cleaned = clean_html(html)
    document = f"{_NOTE_HEAD}<title>{escape(title)}</title>\n{cleaned}\n"
    files.add_bytes(f"{stem}.html", document.encode("utf-8"))
    files.add_bytes(f"{stem}.txt", html_to_text(cleaned).encode("utf-8"))
    return 1


def _json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n").encode("utf-8")
