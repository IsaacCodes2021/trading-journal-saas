"""What notes may contain before they are stored (doc 04, T4 and S6: an allow-list, on write *and* on render).

A note is the editor's HTML. Only formatting a person can make in the editor survives; everything else is removed (a tag
that is not on the list is dropped but its text stays; `script` and `style` go with their contents). Links keep only an
`http`, `https` or `mailto` address and always get `rel="noopener noreferrer"`. The single-page app sanitizes every note
again when it shows it (`SafeHtml`, DOMPurify, the same list): the two layers are meant to agree, and neither trusts the
other. `nh3` is the Python binding of Ammonia, a Rust HTML sanitizer that parses the document the way a browser does,
which is what makes an allow-list sound (string-matching tags is not).
"""

from __future__ import annotations

import nh3

from ..journal import clean_notes

MAX_NOTES_CHARS = 100_000  # a request is already capped at 1 MB; this keeps one trade's note a sane size

# The same list as the front end's `SafeHtml` (frontend/src/components/SafeHtml.tsx). Change both together.
ALLOWED_TAGS = {"p", "br", "b", "strong", "i", "em", "u", "s", "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "a"}
ALLOWED_ATTRIBUTES = {"a": {"href"}}
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_notes(html: str) -> str:
    """The note with everything off the allow-list removed, trimmed, and "" if no visible text is left."""
    cleaned = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    return clean_notes(cleaned)
