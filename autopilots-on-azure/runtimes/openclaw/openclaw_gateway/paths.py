from __future__ import annotations

import os
from pathlib import Path


DEFAULT_DATA_DIR = "~/.autopilots-on-azure/openclaw/data"
DEFAULT_WORKSPACE_DIR = "~/.autopilots-on-azure/openclaw/workspace"


def data_dir() -> Path:
    return Path(os.getenv("OPENCLAW_DATA_DIR", DEFAULT_DATA_DIR)).expanduser()


def workspace_dir() -> Path:
    path = Path(os.getenv("OPENCLAW_WORKSPACE_DIR", DEFAULT_WORKSPACE_DIR)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path
