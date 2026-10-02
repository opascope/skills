"""Load the vendored migration engine once (engine/migrate.py next to docs.py).

The engine registers itself as the module `migrate`, which is also how plugins import it
(engine/INTERFACE.md section 3).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def engine():
    mod = sys.modules.get("migrate")
    if mod is not None and hasattr(mod, "glob_match"):
        return mod
    path = ROOT / "engine" / "migrate.py"
    spec = importlib.util.spec_from_file_location("migrate", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migrate"] = mod
    spec.loader.exec_module(mod)
    return mod
