"""What notes may contain before they are stored.

INTERIM. Doc 04 (S6, a launch gate) calls for an allow-list sanitizer on write (`nh3`); adding a dependency needs the
owner's approval, so until then a note is only trimmed and an editor's empty leftovers ("<p></p>", "<br>") become "".
The single-page app still sanitizes every note again on render with DOMPurify and the same allow-list, so nothing
stored here is ever injected as raw HTML. When `nh3` lands this function is the one place that changes, and the XSS
payload tests of M6 pin it.
"""

from __future__ import annotations

from ..journal import clean_notes

MAX_NOTES_CHARS = 100_000  # a request is already capped at 1 MB; this keeps one trade's note a sane size


def sanitize_notes(html: str) -> str:
    return clean_notes(html)
