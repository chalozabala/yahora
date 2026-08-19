"""Local configuration: reads a .env file sitting next to the project.

Lets a non-technical user drop their Databento key into a file (written by
datos-reales.bat / datos-reales.sh) instead of setting environment
variables by hand. Real environment variables always win, so hosting
platforms that inject secrets are unaffected.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
