"""The notes allow-list: what survives, what is removed, and that no payload gets through (doc 04, T4 and S6).

These run without a database. The XSS payloads are the classic families (script, event handlers, script URLs in links,
embedded objects, styles, comments and malformed markup that browsers repair into something live); each case checks
the *output* contains nothing active, not merely that a particular string is gone.
"""

from __future__ import annotations

import re

import pytest

from tradejournal.api.sanitize import ALLOWED_ATTRIBUTES, ALLOWED_TAGS, sanitize_notes

FORMATTING = (
    "<p>One <b>bold</b>, <strong>strong</strong>, <i>italic</i>, <em>em</em>, <u>underlined</u> and <s>struck</s>.</p>"
    "<ul><li>first</li><li>second</li></ul><ol><li>one</li></ol><blockquote>quoted</blockquote>"
    "<h1>One</h1><h2>Two</h2><h3>Three</h3><p>line<br>break</p>"
)

PAYLOADS = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<img src=x onerror=alert(1)>",
    '<p onclick="alert(1)">click</p>',
    '<a href="javascript:alert(1)">x</a>',
    '<a href=" jAvAsCrIpT:alert(1)">x</a>',
    '<a href="java\tscript:alert(1)">x</a>',
    '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
    '<a href="vbscript:msgbox(1)">x</a>',
    '<iframe src="https://evil.example"></iframe>',
    '<object data="x.swf"></object><embed src="x.swf">',
    "<svg onload=alert(1)><circle/></svg>",
    '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
    "<style>body{background:url(javascript:alert(1))}</style>",
    '<p style="background:url(javascript:alert(1))">x</p>',
    "<link rel=stylesheet href=//evil.example/x.css>",
    '<meta http-equiv=refresh content="0;url=javascript:alert(1)">',
    '<form action="https://evil.example"><input name=x><button>go</button></form>',
    "<!--<script>alert(1)</script>-->",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",
    '<p><b>unclosed <i>tags <a href="javascript:alert(1)">x',
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
    '<base href="javascript:alert(1)//">',
    '<img src="x" srcset="javascript:alert(1)">',
    '<video><source onerror="alert(1)"></video>',
    "<details open ontoggle=alert(1)>x</details>",
]

# Anything that could run code or load something: tags and attributes that must never appear in the output.
ACTIVE = re.compile(
    r"<\s*(script|style|iframe|object|embed|svg|math|img|link|meta|form|input|button|base|video|source|details|noscript)\b"
    r"|\son\w+\s*="
    r"|javascript:|vbscript:|data:text|srcset|style\s*=",
    re.IGNORECASE,
)


def test_the_formatting_the_editor_makes_survives_untouched():
    assert sanitize_notes(FORMATTING) == FORMATTING


@pytest.mark.parametrize("payload", PAYLOADS)
def test_no_payload_leaves_anything_active_behind(payload):
    cleaned = sanitize_notes(f"<p>before</p>{payload}<p>after</p>")
    assert not ACTIVE.search(cleaned), cleaned
    assert "before" in cleaned and "after" in cleaned  # the person's own text survives


@pytest.mark.parametrize("payload", PAYLOADS)
def test_cleaning_is_stable_a_second_pass_changes_nothing(payload):
    once = sanitize_notes(f"<p>x</p>{payload}")
    assert sanitize_notes(once) == once


def test_script_and_style_go_with_their_contents_but_other_removed_tags_keep_their_text():
    assert sanitize_notes("<p>a</p><script>alert(1)</script><p>b</p>") == "<p>a</p><p>b</p>"
    assert sanitize_notes("<style>p{color:red}</style><p>a</p>") == "<p>a</p>"
    assert sanitize_notes("<div>kept text</div>") == "kept text"  # not an allowed tag, but the words stay
    assert sanitize_notes('<span style="color:red">red</span>') == "red"


def test_links_keep_only_a_safe_address_and_always_get_a_safe_rel():
    web = sanitize_notes('<a href="https://example.com/a?b=1&c=2" target="_blank" onclick="x()" class="k">site</a>')
    assert web == '<a href="https://example.com/a?b=1&amp;c=2" rel="noopener noreferrer">site</a>'
    assert sanitize_notes('<a href="mailto:me@example.com">mail</a>') == (
        '<a href="mailto:me@example.com" rel="noopener noreferrer">mail</a>'
    )
    assert sanitize_notes('<a href="http://example.com">plain http</a>').startswith('<a href="http://example.com"')
    # A link whose address is not allowed keeps its text and loses the link.
    assert sanitize_notes('<a href="javascript:alert(1)">bad</a>') == '<a rel="noopener noreferrer">bad</a>'
    assert "href" not in sanitize_notes('<a href="ftp://example.com/x">ftp</a>')
    assert 'rel="noopener' in sanitize_notes(
        '<a href="https://x.example" rel="opener">x</a>'
    )  # a note cannot choose its rel


def test_an_editors_empty_leftovers_become_an_empty_note():
    for blank in ("", "<p></p>", "<p><br></p>", " <br> ", "<script>x</script>", "<div></div>", "<!-- nothing -->"):
        assert sanitize_notes(blank) == "", blank


def test_text_is_never_turned_into_markup_and_entities_survive():
    cleaned = sanitize_notes("<p>1 &lt; 2 &amp;&amp; 3 &gt; 2 &lt;script&gt;alert(1)&lt;/script&gt;</p>")
    assert cleaned == "<p>1 &lt; 2 &amp;&amp; 3 &gt; 2 &lt;script&gt;alert(1)&lt;/script&gt;</p>"
    assert "<script" not in cleaned  # shown as text, never a tag
    assert sanitize_notes("plain text, no tags") == "plain text, no tags"


def test_the_list_is_the_one_in_the_front_end():
    """`SafeHtml.tsx` and this module state the same allow-list; if one changes, this fails until the other does."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "SafeHtml.tsx").read_text(
        encoding="utf-8"
    )
    tags = set(re.findall(r"'([a-z0-9]+)',", source.split("ALLOWED_TAGS = [")[1].split("]")[0]))
    attributes = set(re.findall(r"'([a-z]+)'", source.split("ALLOWED_ATTR = [")[1].split("]")[0]))
    assert tags == ALLOWED_TAGS
    assert attributes == ALLOWED_ATTRIBUTES["a"]
