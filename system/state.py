"""App-owned data paths, resolved only on explicit startup."""

import os
import sys
from pathlib import Path


def default_state_path() -> Path:
    if sys.platform == "win32":
        location = Path(
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        )
    elif sys.platform == "darwin":
        location = Path.home() / "Library" / "Application Support"
    else:
        configured = os.environ.get("XDG_STATE_HOME")
        location = (
            Path(configured)
            if configured and Path(configured).is_absolute()
            else Path.home() / ".local" / "state"
        )
    if not location.is_absolute():
        raise ValueError("application state requires an absolute OS data location")
    return location / "DevSetup" / "state.sqlite3"
