"""Start the interface: a local web server on 127.0.0.1, opened in your browser."""

from __future__ import annotations

from nicegui import app, ui

from .. import config, journal
from .data import open_db
from .theme import CSS

# Pasting an image (Ctrl+V) while a trade is open adds it as a screenshot: the image is
# handed to that trade's uploader, the same path as dropping a file. Runs in the
# capture phase so an image never lands inside the notes editor as inline data.
PASTE_SCRIPT = """
<script>
document.addEventListener("paste", (event) => {
  const target = window.tjPasteUploader;
  if (target === undefined) return;
  const images = [...(event.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (!images.length) return;
  event.preventDefault();
  event.stopPropagation();
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const files = images.map((f, i) =>
    new File([f], `pasted-${stamp}-${i + 1}.${f.type.split("/")[1] || "png"}`, {type: f.type}));
  getElement(target)?.$refs?.qRef?.addFiles(files);
}, true);
</script>
"""


def run(port: int, open_browser: bool = True) -> None:
    from . import pages  # noqa: F401  (registers the page routes)

    screenshots = config.screenshots_dir()
    screenshots.mkdir(parents=True, exist_ok=True)
    with open_db() as conn:
        journal.remove_orphan_files(conn, screenshots)
    app.add_static_files("/screenshots", screenshots)

    ui.add_css(CSS, shared=True)
    ui.add_head_html(PASTE_SCRIPT, shared=True)
    print(f"Trading Journal: data in {config.data_dir()}")
    ui.run(
        host="127.0.0.1",  # this computer only; never reachable from the network
        port=port,
        title="Trading Journal",
        favicon="📈",
        reload=False,
        show=open_browser,
        show_welcome_message=True,
    )
