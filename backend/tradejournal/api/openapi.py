"""Write (or check) `backend/openapi.json`, the input to `pnpm gen:api` in the front end. No server is started.

python -m tradejournal.api.openapi           # write the file
python -m tradejournal.api.openapi --check   # exit 1 if the committed file is out of date (CI)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .app import create_app
from .config import ApiConfig

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi.json"


def render() -> str:
    # A fixed config, so the output never depends on the machine or the environment it is generated on.
    app = create_app(ApiConfig(app_version="openapi", web_dist=Path("/nonexistent")))
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = SPEC_PATH.read_text(encoding="utf-8") if SPEC_PATH.exists() else ""
        if current != text:
            print(f"{SPEC_PATH} is out of date. Run: python -m tradejournal.api.openapi", file=sys.stderr)
            return 1
        return 0
    SPEC_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
