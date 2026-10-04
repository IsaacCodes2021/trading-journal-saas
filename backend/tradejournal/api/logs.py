"""Structured logs that cannot leak (doc 04 section 5: "never log passwords, session or CSRF tokens, notes, trade contents,
screenshot names, full IPs").

In production every record is one line of JSON: when, level, which logger, a message, the request it belongs to, and a few
named facts. Three rules keep secrets out, so that no one has to remember them at each `log.info(...)`:

1. **Scrubbing.** Every message and every text fact passes through `scrub`, which replaces the things that look like
   secrets (query-string values such as `code=` and `state=`, bearer tokens, cookies, JWTs, long hexadecimal strings,
   passwords inside connection URLs) and email addresses. A pattern that is too eager costs a readable log line; one
   that is too shy costs a leak, so the patterns lean eager.
2. **An error is its kind and its path, never its message.** A database error's message can contain the value that
   broke a constraint (a note, an email), so a traceback is written as the exception's type, the database error code
   and constraint name when there is one, and the file, line and function of each frame.
3. **Nothing about the request is logged but its method, route, status and time.** Not the query string, not the
   headers, not the cookies, not the body, not the client's address.

Development keeps Python's usual quiet logging (the dev server prints its own lines), so tests and a local run are
not changed by any of this.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any, TextIO

REDACTED = "[redacted]"

# The request a record belongs to, for records written by other loggers while it runs.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Our own records go nowhere unless `configure_logging` has been called: without a handler, Python would print warnings to
# standard error as a bare message.
logging.getLogger("tj").addHandler(logging.NullHandler())

_QUERY_VALUE = re.compile(
    r"""(?ix)
    \b([\w-]*(?:code|state|token|secret|password|passwd|pwd|key|authorization|cookie|session|csrf|nonce|verifier)[\w-]*)
    =
    ([^&\s"'<>]+)
    """
)
_AUTH_SCHEME = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s@]+):[^@/\s]+@")
_JWT = re.compile(r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]*")
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def scrub(text: str) -> str:
    """The text with anything that looks like a secret or an email address replaced. Idempotent."""
    text = _URL_PASSWORD.sub(rf"\1:{REDACTED}@", text)
    text = _QUERY_VALUE.sub(rf"\1={REDACTED}", text)
    text = _AUTH_SCHEME.sub(rf"\1 {REDACTED}", text)
    text = _JWT.sub(REDACTED, text)
    text = _LONG_HEX.sub(REDACTED, text)
    return _EMAIL.sub("[email]", text)


def _type_name(error: BaseException) -> str:
    kind = type(error)
    return kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"


def _frame(filename: str, line: int, function: str) -> str:
    return f"{'/'.join(PurePath(filename).parts[-3:])}:{line} {function}"


def error_summary(error: BaseException) -> dict[str, Any]:
    """What an error says about itself without its message: its type, the database's error code and the names of the
    table and constraint when it is a database error, the types it was raised from, and where it happened."""
    summary: dict[str, Any] = {"type": _type_name(error)}
    state = getattr(error, "sqlstate", None)
    if isinstance(state, str):
        summary["sqlstate"] = state
    diag = getattr(error, "diag", None)
    for name in ("table_name", "constraint_name"):
        value = getattr(diag, name, None)
        if isinstance(value, str):
            summary[name] = value
    causes: list[str] = []
    cause = error.__cause__ or error.__context__
    while cause is not None and len(causes) < 3:
        causes.append(_type_name(cause))
        cause = cause.__cause__ or cause.__context__
    if causes:
        summary["caused_by"] = causes
    frames: list[str] = []
    tb = error.__traceback__
    while tb is not None:
        frames.append(_frame(tb.tb_frame.f_code.co_filename, tb.tb_lineno, tb.tb_frame.f_code.co_name))
        tb = tb.tb_next
    summary["frames"] = frames[-12:]
    return summary


class JsonFormatter(logging.Formatter):
    """One line of JSON per record. Named facts come from `extra={"fields": {...}}`; they can add to a record but not
    replace its time, level, logger, message or request."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        request_id = request_id_var.get()
        if request_id:
            data["request_id"] = request_id
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key in data:
                    continue
                scalar = value is None or isinstance(value, bool | int | float)
                data[str(key)] = value if scalar else scrub(str(value))
        error = record.exc_info[1] if record.exc_info else None
        if error is not None:
            data["error"] = error_summary(error)
        return json.dumps(data, separators=(",", ":"), default=str)


HANDLER_NAME = "tj-json"
QUIET_LIBRARIES = ("httpx", "httpcore")


def configure_logging(stream: TextIO | None = None) -> logging.Handler:
    """Send every log record, ours and the server's, to one JSON stream (standard output unless told otherwise).
    Calling it again replaces the handler rather than adding a second one."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [existing for existing in root.handlers if existing.get_name() != HANDLER_NAME] + [handler]
    root.setLevel(logging.INFO)
    # The server's own loggers would otherwise write plain text, with an unscrubbed traceback, beside ours.
    for name in ("uvicorn", "uvicorn.error"):
        server = logging.getLogger(name)
        server.handlers = []
        server.propagate = True
    access = logging.getLogger("uvicorn.access")  # replaced by our request line, which has no query string
    access.handlers = []
    access.propagate = False
    # The HTTP client libraries write every outgoing URL (with its query string) at INFO. Nothing here needs that.
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
