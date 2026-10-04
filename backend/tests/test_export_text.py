"""The two pure pieces of the export: a note as plain text, and a value as the text of a CSV cell (no database)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradejournal.pg.export import cell, html_to_text


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<p>Good entry.</p><p>Late exit.</p>", "Good entry.\nLate exit.\n"),
        ("<p>One<br>two<br/>three</p>", "One\ntwo\nthree\n"),
        ("<ul><li>first</li><li>second</li></ul>", "- first\n- second\n"),
        ("<ol><li>a</li></ol>", "- a\n"),
        ("<h1>Title</h1><p>Body</p>", "Title\nBody\n"),
        ("<blockquote>quoted</blockquote>", "quoted\n"),
        ("<p>Tom &amp; Jerry &lt;3 &quot;x&quot;</p>", 'Tom & Jerry <3 "x"\n'),
        ("<p><b>bold</b> <i>it</i> <u>u</u> <s>s</s> <a href='https://x.test'>link</a></p>", "bold it u s link\n"),
        ("<p>Zoë 日本語 — ok</p>", "Zoë 日本語 — ok\n"),
        ("plain text, no tags", "plain text, no tags\n"),
        ("<p>a</p><p></p><p></p><p></p><p>b</p>", "a\n\nb\n"),  # runs of blank lines are squeezed to one
    ],
)
def test_a_note_becomes_plain_text_line_by_line(html, text):
    assert html_to_text(html) == text


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script><p>kept</p>",
        "<style>p{color:red}</style><p>kept</p>",
        "<p onclick='alert(1)'>kept</p><img src=x onerror=alert(1)>",
        "<iframe src='https://evil.test'></iframe><p>kept</p>",
    ],
)
def test_scripts_styles_and_markup_never_reach_the_text(html):
    text = html_to_text(html)
    assert text == "kept\n"
    for forbidden in ("alert", "<", "color:red", "evil", "onclick"):
        assert forbidden not in text


def test_an_empty_note_is_just_a_newline():
    assert html_to_text("") == "\n"
    assert html_to_text("<p></p>") == "\n"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, ""),
        ("", ""),
        ("Apex 07", "Apex 07"),
        (7, "7"),
        (True, "true"),
        (False, "false"),
        (Decimal("12.50"), "12.50"),  # the scale is kept
        (Decimal("0.000100"), "0.000100"),
        (Decimal("1E+2"), "100"),  # never scientific notation
        (Decimal("-0.25"), "-0.25"),
        (Decimal("20412.25"), "20412.25"),
        (date(2024, 1, 5), "2024-01-05"),
        (datetime(2024, 1, 5, 14, 30, tzinfo=UTC), "2024-01-05T14:30:00Z"),
        (datetime(2024, 1, 5, 14, 30, 7, 250000, tzinfo=UTC), "2024-01-05T14:30:07.250000Z"),
        (datetime(2024, 1, 5, 9, 30, tzinfo=timezone(timedelta(hours=-5))), "2024-01-05T14:30:00Z"),  # to UTC
    ],
)
def test_a_value_becomes_the_exact_text_of_a_cell(value, text):
    assert cell(value) == text


def test_a_datetime_is_not_taken_for_a_plain_date():
    assert cell(datetime(2024, 1, 5, 0, 0, tzinfo=UTC)) != cell(date(2024, 1, 5))
