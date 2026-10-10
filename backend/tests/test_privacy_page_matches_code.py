"""The public privacy page states numbers that the code decides.

This fails when one of them changes and the page does not, so the page cannot quietly become untrue.
"""

from pathlib import Path

from tradejournal import filebackup
from tradejournal.pg import deletion, sessions, sweep

PAGE = Path(__file__).resolve().parents[2] / "site" / "public" / "privacy" / "index.html"


def _page() -> str:
    return " ".join(PAGE.read_text(encoding="utf-8").split())


def test_the_page_exists_where_the_test_looks() -> None:
    assert PAGE.is_file(), f"no privacy page at {PAGE}"


def test_an_unused_sign_up_is_kept_as_long_as_the_page_says() -> None:
    assert f"{sweep.BETA_KEEP_HOURS} hours after the code was made" in _page()


def test_the_deletion_grace_is_what_the_page_says() -> None:
    assert f"takes effect {deletion.GRACE.days} days after you ask" in _page()


def test_the_session_limits_are_what_the_page_says() -> None:
    page = _page()
    idle, absolute = sessions.IDLE_TIMEOUT.days, sessions.ABSOLUTE_LIFETIME.days
    assert f"end after {idle} days without use, or after {absolute} days in all" in page


def test_a_deleted_screenshot_stays_in_the_copy_as_long_as_the_page_says() -> None:
    assert f"stays in that copy for up to {filebackup.DEFAULT_KEEP_DAYS} days" in _page()
