"""A short, safe label for a browser, from its User-Agent header: "Chrome on Windows".

The header is chosen by the client and can be anything, so the raw text is never shown or returned; only a label made
from a fixed list of words is. Order matters (Edge and Opera say "Chrome" too; every Chromium browser says "Safari").
"""

from __future__ import annotations

# (a marker in the header, the name to show); the first marker found wins.
_BROWSERS = (
    ("Edg/", "Edge"),
    ("EdgA/", "Edge"),
    ("EdgiOS/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Chromium/", "Chromium"),
    ("Safari/", "Safari"),
)
# Android before Linux (Android UAs contain "Linux"); iPhone and iPad before Mac (they contain "like Mac OS X").
_SYSTEMS = (
    ("Windows", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("CrOS", "ChromeOS"),
    ("Macintosh", "Mac"),
    ("Mac OS X", "Mac"),
    ("Linux", "Linux"),
)


def describe_device(user_agent: str | None) -> str:
    text = user_agent or ""
    browser = next((name for marker, name in _BROWSERS if marker in text), None)
    system = next((name for marker, name in _SYSTEMS if marker in text), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Unknown device"
