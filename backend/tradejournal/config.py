"""Filesystem locations. Everything lives under one data folder on this machine."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """TRADEJOURNAL_DATA_DIR overrides the default <project>/data folder."""
    return Path(os.environ.get("TRADEJOURNAL_DATA_DIR", PROJECT_ROOT / "data"))


def db_path() -> Path:
    return data_dir() / "journal.db"


def screenshots_dir() -> Path:
    return data_dir() / "screenshots"
