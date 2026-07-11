"""Make nanuk_hw importable when pytest runs from the repo root (hw/ is a
uv project; when its venv is not active, fall back to the source tree)."""

import sys
from pathlib import Path

_HW_DIR = str(Path(__file__).resolve().parents[1])
if _HW_DIR not in sys.path:
    sys.path.insert(0, _HW_DIR)
