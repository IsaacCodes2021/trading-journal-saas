"""Colors and shared styles for both color schemes.

Chart colors follow a validated data-viz palette: one blue for single-series marks,
and fixed status green/red for profit/loss. Each scheme has its own steps, chosen
for its surface, rather than an automatic inversion.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Palette:
    surface: str  # cards and chart backgrounds
    page: str  # page background
    ink: str  # primary text
    ink_2: str  # secondary text
    muted: str  # axis labels, hints
    grid: str  # gridlines (hairline)
    axis: str  # baselines
    border: str  # card outlines
    accent: str  # single-series marks (categorical slot 1)
    good: str  # profitable marks (status good)
    critical: str  # losing marks (status critical)
    good_text: str  # profit figures in text
    bad_text: str  # loss figures in text


LIGHT = Palette(
    surface="#fcfcfb",
    page="#f9f9f7",
    ink="#0b0b0b",
    ink_2="#52514e",
    muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    border="rgba(11,11,11,0.10)",
    accent="#2a78d6",
    good="#0ca30c",
    critical="#d03b3b",
    good_text="#006300",
    bad_text="#d03b3b",
)

DARK = Palette(
    surface="#1a1a19",
    page="#0d0d0d",
    ink="#ffffff",
    ink_2="#c3c2b7",
    muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    border="rgba(255,255,255,0.10)",
    accent="#3987e5",
    good="#0ca30c",
    critical="#d03b3b",
    good_text="#0ca30c",
    bad_text="#e66767",
)


def palette(dark: bool) -> Palette:
    return DARK if dark else LIGHT


# A tag pill is its color as a light tint behind text in a darker (light mode) or
# lighter (dark mode) step of the same color.
TAG_TINT = {"light": 0.14, "dark": 0.24}
MIN_TEXT_CONTRAST = 4.5  # WCAG AA for small text
# Hand-picked text steps for the preset colors (journal.TAG_COLOR_PRESETS): (light, dark).
_PRESET_TEXT = {
    "#2a78d6": ("#1c5cab", "#86b6ef"),
    "#6e5adc": ("#4a3aa7", "#b1a8f0"),
    "#0ca30c": ("#0f6b0f", "#72d172"),
    "#eb6834": ("#a3441a", "#f2a27f"),
    "#e87ba4": ("#a33562", "#f0a3c0"),
    "#1baf7a": ("#0c6e4d", "#5fd3a9"),
    "#d03b3b": ("#b02a2a", "#f08a8a"),
    "#eda100": ("#7a5200", "#e8b64c"),
    "#898781": ("#52514e", "#c3c2b7"),
}

RGB = tuple[int, int, int]


def _rgb(color: str) -> RGB:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _hex(rgb: RGB) -> str:
    return "#" + "".join(f"{round(v):02x}" for v in rgb)


def _mix(a: RGB, b: RGB, amount: float) -> RGB:
    """a moved `amount` (0-1) of the way to b."""
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))  # type: ignore[return-value]


def _luminance(rgb: RGB) -> float:
    def channel(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def pill_backgrounds(color: str, scheme: str) -> list[RGB]:
    """The pill's tint as it looks on cards and on the page."""
    p = DARK if scheme == "dark" else LIGHT
    return [_mix(_rgb(surface), _rgb(color), TAG_TINT[scheme]) for surface in (p.surface, p.page)]


def tag_text_colors(color: str) -> tuple[str, str]:
    """Readable text for a tag pill of this color: (light mode, dark mode). Presets use
    their hand-picked steps; other colors are darkened or lightened until the text has
    at least 4.5:1 contrast on the pill."""
    if color in _PRESET_TEXT:
        return _PRESET_TEXT[color]
    result = []
    for scheme, toward in (("light", (0, 0, 0)), ("dark", (255, 255, 255))):
        backgrounds = pill_backgrounds(color, scheme)
        for step in range(21):
            text = _mix(_rgb(color), toward, step / 20)
            if all(contrast(text, bg) >= MIN_TEXT_CONTRAST for bg in backgrounds):
                break
        result.append(_hex(text))
    return result[0], result[1]


def tag_style(color: str) -> str:
    """Inline CSS variables that color a .tj-tag pill in both schemes."""
    light, dark = tag_text_colors(color)
    r, g, b = _rgb(color)
    return (
        f"--tj-tag-fg: {light}; --tj-tag-fg-dark: {dark}; "
        f"--tj-tag-bg: rgba({r}, {g}, {b}, {TAG_TINT['light']}); --tj-tag-bg-dark: rgba({r}, {g}, {b}, {TAG_TINT['dark']})"
    )


def on_color(color: str) -> str:
    """Black or white, whichever reads better on a swatch of this color (for its check mark)."""
    rgb = _rgb(color)
    return "#000000" if contrast(rgb, (0, 0, 0)) >= contrast(rgb, (255, 255, 255)) else "#ffffff"


def _variables(p: Palette) -> str:
    return " ".join(f"--tj-{f.name.replace('_', '-')}: {getattr(p, f.name)};" for f in fields(p))


CSS = f"""
body {{ {_variables(LIGHT)} }}
body.body--dark {{ {_variables(DARK)} }}
body {{
  background: var(--tj-page);
  color: var(--tj-ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}}
.q-card.tj-card {{
  background: var(--tj-surface);
  border: 1px solid var(--tj-border);
  border-radius: 12px;
  box-shadow: none;
  padding: 16px 20px;
  scroll-margin-top: 72px;  /* clear the fixed header when scrolled to */
}}
.q-header.tj-header {{
  background: var(--tj-surface);
  color: var(--tj-ink);
  border-bottom: 1px solid var(--tj-border);
}}
.tj-nav {{
  color: var(--tj-ink-2);
  text-decoration: none;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 14px;
}}
.tj-nav:hover {{ background: var(--tj-border); }}
.tj-nav-active {{ color: var(--tj-ink); font-weight: 600; background: var(--tj-border); }}
.tj-title {{ font-size: 15px; font-weight: 600; color: var(--tj-ink); }}
.tj-label {{ font-size: 12px; color: var(--tj-ink-2); }}
.tj-muted {{ color: var(--tj-muted); }}
.tj-secondary {{ color: var(--tj-ink-2); }}
.tj-value {{ font-size: 22px; font-weight: 600; line-height: 1.25; color: var(--tj-ink); }}
.tj-hero {{ font-size: 28px; }}
.tj-pos {{ color: var(--tj-good-text) !important; }}
.tj-neg {{ color: var(--tj-bad-text) !important; }}
.tj-num, .tj-table td {{ font-variant-numeric: tabular-nums; }}
.tj-table .q-table__card, .tj-table.q-table__card {{ background: transparent; box-shadow: none; }}
.tj-table thead tr th {{ color: var(--tj-ink-2); font-weight: 600; }}
.tj-grid {{
  --ag-background-color: var(--tj-surface);
  --ag-header-background-color: var(--tj-surface);
  --ag-odd-row-background-color: var(--tj-surface);
  --ag-foreground-color: var(--tj-ink);
  --ag-border-color: var(--tj-border);
  --ag-font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  --ag-font-size: 13px;
}}
.tj-grid .ag-cell {{ font-variant-numeric: tabular-nums; }}
.tj-grid .ag-row {{ cursor: pointer; }}
.q-uploader.tj-upload {{
  width: 100%;
  max-height: none;
  background: transparent;
  border: 1px dashed var(--tj-axis);
  border-radius: 12px;
  box-shadow: none;
}}
.tj-upload .q-uploader__header {{ background: transparent !important; color: var(--tj-ink) !important; }}
.tj-upload .q-uploader__subtitle {{ display: none; }}
.tj-upload .q-uploader__list {{ min-height: 56px; }}
.q-uploader--dnd.tj-upload {{ border-color: var(--tj-accent); }}
/* Colors come from inline variables set per tag (theme.tag_style). */
.tj-tag {{
  --tj-tag-tint: var(--tj-tag-bg);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border: 0;
  border-radius: 999px;
  color: var(--tj-tag-fg);
  background: var(--tj-tag-tint);
  font: 600 12px/18px system-ui, -apple-system, "Segoe UI", sans-serif;
  white-space: nowrap;
}}
body.body--dark .tj-tag {{ --tj-tag-tint: var(--tj-tag-bg-dark); color: var(--tj-tag-fg-dark); }}
.tj-tag .q-icon {{ font-size: 14px; opacity: 0.75; }}
button.tj-tag {{ cursor: pointer; }}
button.tj-tag:hover {{ box-shadow: inset 0 0 0 1px currentColor; }}
button.tj-tag:focus-visible {{ outline: 2px solid currentColor; outline-offset: 1px; }}
/* A tag you could add (not on this trade yet): outlined instead of filled. */
.tj-tag.tj-tag-option {{ background: transparent; box-shadow: inset 0 0 0 1px currentColor; opacity: 0.8; }}
.tj-tag.tj-tag-option:hover {{ background: var(--tj-tag-tint); opacity: 1; }}
.tj-tag-count {{ font-weight: 400; opacity: 0.75; }}

/* Tag editor (Settings) */
.q-btn.tj-category {{ min-width: 9rem; padding: 2px 6px; font-weight: 600; color: var(--tj-ink); }}
.q-btn.tj-category .q-icon {{ font-size: 15px; opacity: 0.6; }}
.tj-swatch {{
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}}
.tj-swatch .q-icon {{ font-size: 16px; }}
.tj-swatch:hover {{ transform: scale(1.12); }}
.tj-swatch:focus-visible {{ outline: 2px solid var(--tj-accent); outline-offset: 3px; }}
.tj-swatch-on {{ box-shadow: 0 0 0 2px var(--tj-surface), 0 0 0 4px var(--tj-ink); }}
.tj-swatch-custom {{
  background: conic-gradient(#d03b3b, #eda100, #0ca30c, #1baf7a, #2a78d6, #6e5adc, #e87ba4, #d03b3b);
  color: #ffffff;
}}
.tj-swatch-custom .q-icon {{ filter: drop-shadow(0 0 1px rgba(0, 0, 0, 0.8)); }}
.tj-confirm {{ background: var(--tj-border); border-radius: 8px; padding: 8px 12px; font-size: 14px; }}

/* Trade detail panel */
.tj-section {{
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--tj-ink-2);
}}
.tj-detail-side {{ border-right: 1px solid var(--tj-border); }}
@media (max-width: 767px) {{ .tj-detail-side {{ border-right: 0; border-bottom: 1px solid var(--tj-border); }} }}
.tj-stat-row {{
  justify-content: space-between;
  align-items: baseline;
  padding: 7px 0;
  border-bottom: 1px solid var(--tj-border);
}}
.tj-stat-row:last-child {{ border-bottom: 0; }}
.q-editor.tj-notes {{
  background: var(--tj-surface);
  border: 1px solid var(--tj-border);
  border-radius: 10px;
}}
.tj-notes .q-editor__toolbar {{ border-bottom: 1px solid var(--tj-border); }}
.tj-notes .q-editor__content {{ font-size: 14px; line-height: 1.55; }}
.tj-notes .q-editor__content p {{ margin: 0 0 6px; }}
.tj-thumb {{
  position: relative;
  padding: 0;
  border: 1px solid var(--tj-border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--tj-page);
  cursor: zoom-in;
}}
.tj-thumb:hover {{ border-color: var(--tj-accent); }}
.tj-thumb:focus-visible {{ outline: 2px solid var(--tj-accent); outline-offset: 2px; }}
.tj-thumb-name {{
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 4px 8px;
  font-size: 11px;
  text-align: left;
  color: #ffffff;
  background: linear-gradient(transparent, rgba(0, 0, 0, 0.65));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.q-uploader.tj-dropzone {{
  width: 100%;
  max-height: none;
  background: transparent;
  border: 1px dashed var(--tj-axis);
  border-radius: 10px;
  box-shadow: none;
}}
.tj-dropzone .q-uploader__header {{ background: transparent !important; color: var(--tj-ink-2) !important; }}
.tj-dropzone .q-uploader__subtitle, .tj-dropzone .q-uploader__list {{ display: none; }}
.q-uploader--dnd.tj-dropzone {{ border-color: var(--tj-accent); }}
.tj-viewer-image {{ height: 76vh; border-radius: 8px; background: var(--tj-page); }}

.tj-business {{ padding: 4px 0 12px; border-bottom: 1px solid var(--tj-border); margin-bottom: 4px; }}
.tj-ledger-row {{ padding: 4px 0; border-bottom: 1px solid var(--tj-border); }}
/* Archived accounts: a colored bar, a faint tint and a matching icon by outcome. */
.q-expansion-item.tj-outcome {{ border-left: 3px solid var(--tj-axis); border-radius: 8px; margin-bottom: 4px; }}
.q-expansion-item.tj-outcome-passed {{ border-left-color: var(--tj-good); }}
.q-expansion-item.tj-outcome-failed {{ border-left-color: var(--tj-critical); }}
/* Only the header row is tinted; the form inside an open account stays plain. */
.tj-outcome-passed > .q-expansion-item__container > .q-item {{
  background: color-mix(in srgb, var(--tj-good) 9%, transparent);
}}
.tj-outcome-failed > .q-expansion-item__container > .q-item {{
  background: color-mix(in srgb, var(--tj-critical) 9%, transparent);
}}
body.body--dark .tj-outcome-passed > .q-expansion-item__container > .q-item {{
  background: color-mix(in srgb, var(--tj-good) 16%, transparent);
}}
body.body--dark .tj-outcome-failed > .q-expansion-item__container > .q-item {{
  background: color-mix(in srgb, var(--tj-critical) 16%, transparent);
}}
.tj-outcome > .q-expansion-item__container > .q-item {{ border-radius: 0 8px 8px 0; }}
.tj-outcome-passed > .q-expansion-item__container > .q-item .q-item__section--avatar .q-icon {{ color: var(--tj-good-text); }}
.tj-outcome-failed > .q-expansion-item__container > .q-item .q-item__section--avatar .q-icon {{ color: var(--tj-bad-text); }}
.tj-archived-note {{ background: var(--tj-border); border-radius: 8px; padding: 4px 4px 4px 12px; margin-top: 8px; }}

/* Trade log journal column */
.tj-journal-icon {{ font-size: 16px; color: var(--tj-ink-2); vertical-align: middle; }}
.tj-journal-shots {{ margin-left: 6px; color: var(--tj-ink-2); font-size: 12px; }}
"""
