import argparse
import os

parser = argparse.ArgumentParser(prog="python -m tradejournal.ui", description="Start the Trading Journal interface.")
parser.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")
parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
parser.add_argument("--data-dir", help="folder holding journal.db (default: the project's data folder)")
args = parser.parse_args()
if args.data_dir:
    os.environ["TRADEJOURNAL_DATA_DIR"] = args.data_dir

from .app import run  # noqa: E402  (after the data folder is chosen)

run(port=args.port, open_browser=not args.no_browser)
