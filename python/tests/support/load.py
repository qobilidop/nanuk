"""Load an example's eDSL twin as a module.

examples/ at the repo root is content, not a package — nothing importable
ships there. Tests treat the twins as fixtures and load them by path.
"""

import importlib.util
from pathlib import Path

_EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


def load_example(relpath: str):
    """examples/<relpath> -> executed module (e.g. load_example("map_ttl/fwd.py"))."""
    path = _EXAMPLES / relpath
    name = "example_" + relpath.replace("/", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
