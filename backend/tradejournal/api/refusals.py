"""Turning what the domain code says ("no", "not found", "already exists") into the API's answers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from fastapi import HTTPException


def sentence(problem: object) -> str:
    """A domain message ("name is empty") as a sentence for a person ("Name is empty.")."""
    text = str(problem).strip()
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


@contextmanager
def refusals(*, what: str, duplicate: str | None = None) -> Iterator[None]:
    """Wrap a whole `with tenant.session()` block: a `LookupError` is a 404 (the row is missing, or someone else's: the
    two look the same), a `ValueError` is a 422 in the domain's own words, and a unique-index clash is a 409.

    Use it *outside* the transaction so the transaction is already rolled back when the answer is chosen, and build
    response models *after* the block, so a bug in building one cannot pass for a refusal."""
    try:
        yield
    except LookupError:
        raise HTTPException(404, f"That {what} does not exist.") from None
    except ValueError as problem:
        raise HTTPException(422, sentence(problem)) from None
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, duplicate or "That already exists.") from None


def require_row(conn: psycopg.Connection, table: str, row_id: int, what: str) -> None:
    """404 for a row that is missing *or someone else's*: row-level security hides it, so the two look the same.
    `table` is always a literal at the call site, never a request value."""
    if conn.execute(f"SELECT 1 FROM {table} WHERE id = %s", (row_id,)).fetchone() is None:  # noqa: S608
        raise HTTPException(404, f"That {what} does not exist.")
