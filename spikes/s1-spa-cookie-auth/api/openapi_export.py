"""Write openapi.json without starting a server (the input to `pnpm gen:api`)."""

import json
from pathlib import Path

from app import Settings, create_app

here = Path(__file__).resolve().parent
spec = create_app(Settings(web_dist=here / "nonexistent")).openapi()
(here / "openapi.json").write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"wrote {here / 'openapi.json'}")
