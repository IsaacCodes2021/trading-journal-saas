"""Writing a trade's journal: its notes and its tags (screenshots wait for the object store, M6)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from psycopg import Connection

from ...pg import journal as pg_journal
from ..deps import ERROR_RESPONSES, WriteTenantDep
from ..sanitize import sanitize_notes
from ..schemas import Problem
from ..schemas.journal import PathId
from ..schemas.trades import NewTagIn, NotesIn, NotesOut, TagOut
from ..trades_view import tag_out

router = APIRouter(prefix="/api/v1", tags=["journal"], responses={**ERROR_RESPONSES, 404: {"model": Problem}})


def _require(conn: Connection, table: str, row_id: int, what: str) -> None:
    """404 for a row that is missing *or someone else's*: row-level security hides it, so the two look the same."""
    if conn.execute(f"SELECT 1 FROM {table} WHERE id = %s", (row_id,)).fetchone() is None:  # noqa: S608  (a fixed table name)
        raise HTTPException(404, f"That {what} does not exist.")


@router.put("/trades/{trade_id}/notes", response_model=NotesOut)
def put_notes(trade_id: PathId, body: NotesIn, tenant: WriteTenantDep) -> NotesOut:
    """Replace a trade's notes. Answers with what was stored (a note with no visible text is stored as empty)."""
    with tenant.session() as conn:
        _require(conn, "trades", trade_id, "trade")
        pg_journal.set_notes(conn, trade_id, sanitize_notes(body.html))
        return NotesOut(notes_html=pg_journal.notes(conn, trade_id))


@router.put("/trades/{trade_id}/tags/{tag_id}", response_model=list[TagOut])
def put_trade_tag(trade_id: PathId, tag_id: PathId, tenant: WriteTenantDep) -> list[TagOut]:
    """Put a tag on a trade (already there is fine). Answers with the trade's tags."""
    with tenant.session() as conn:
        _require(conn, "trades", trade_id, "trade")
        _require(conn, "tags", tag_id, "tag")
        pg_journal.add_trade_tag(conn, trade_id, tag_id)
        return [tag_out(tag) for tag in pg_journal.trade_tags(conn, trade_id)]


@router.delete("/trades/{trade_id}/tags/{tag_id}", response_model=list[TagOut])
def delete_trade_tag(trade_id: PathId, tag_id: PathId, tenant: WriteTenantDep) -> list[TagOut]:
    """Take a tag off a trade (not on it is fine); the tag stays for other trades. Answers with the trade's tags."""
    with tenant.session() as conn:
        _require(conn, "trades", trade_id, "trade")
        _require(conn, "tags", tag_id, "tag")
        pg_journal.remove_trade_tag(conn, trade_id, tag_id)
        return [tag_out(tag) for tag in pg_journal.trade_tags(conn, trade_id)]


@router.post("/tags", response_model=TagOut, status_code=201)
def create_tag(body: NewTagIn, tenant: WriteTenantDep) -> TagOut:
    """A new tag in a category, or the one already there under that name (any capitalization)."""
    with tenant.session() as conn:
        _require(conn, "tag_groups", body.group_id, "category")
        try:
            return tag_out(pg_journal.get_or_create_tag(conn, body.group_id, body.name))
        except ValueError as problem:  # an empty or overlong name
            raise HTTPException(422, str(problem).capitalize() + ".") from None
