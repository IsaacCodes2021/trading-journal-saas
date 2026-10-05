"""The note for a trading day: what the calendar marks and the day view edits (requirement F-P3, doc 07 section 2).

A day's note is the same kind of text as a trade's (the editor's HTML, cleaned on write and cleaned again on render), kept
per person per trading day. Writing an empty note removes it, so the list of days that have one is exact. Any day in a
sane range may have a note, with or without trades.

Nothing here names another person's row: the key is the person and the day, so row-level security is all that is needed,
and a second person asking for the same date reads their own note, never the first person's.
"""

from __future__ import annotations

from fastapi import APIRouter

from ...pg import journal as pg_journal
from ..deps import ERROR_RESPONSES, TenantDep, WriteTenantDep
from ..sanitize import sanitize_notes
from ..schemas.trades import DayNoteOut, DayNotesQueryParams, DaysWithNotesOut, NoteDay, NotesIn

router = APIRouter(prefix="/api/v1", tags=["journal"], responses=ERROR_RESPONSES)


@router.get("/day-notes", response_model=DaysWithNotesOut)
def list_days_with_notes(window: DayNotesQueryParams, tenant: TenantDep) -> DaysWithNotesOut:
    """Which trading days from `start` to `end` (at most 400 days) have a note. No content: only the days."""
    with tenant.session() as conn:
        days = pg_journal.days_with_notes(conn, window.start, window.end)
    return DaysWithNotesOut(days=days)


@router.get("/day-notes/{day}", response_model=DayNoteOut)
def get_day_note(day: NoteDay, tenant: TenantDep) -> DayNoteOut:
    """The note for one trading day; an empty one when nothing is written."""
    with tenant.session() as conn:
        notes_html = pg_journal.day_note(conn, day)
    return DayNoteOut(day=day, notes_html=notes_html)


@router.put("/day-notes/{day}", response_model=DayNoteOut)
def put_day_note(day: NoteDay, body: NotesIn, tenant: WriteTenantDep) -> DayNoteOut:
    """Replace the note for a trading day. Answers with what was stored (a note with no visible text removes it)."""
    with tenant.session() as conn:
        pg_journal.set_day_note(conn, day, sanitize_notes(body.html))
        notes_html = pg_journal.day_note(conn, day)
    return DayNoteOut(day=day, notes_html=notes_html)
