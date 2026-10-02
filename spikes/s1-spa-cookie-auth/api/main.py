"""Entry point: `python -m uvicorn main:app --port 8000` from this folder (after `pnpm build` in ../web)."""

import os
from pathlib import Path

from app import Settings, create_app

app = create_app(
    Settings(
        web_dist=Path(__file__).resolve().parent.parent / "web" / "dist",
        app_origin=os.environ.get("APP_ORIGIN", "http://localhost:8000"),
    )
)
