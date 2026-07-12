"""nanuk.rtl: the Amaranth RTL below the ISA — parser core, MAP core,
and their simulation utilities. Needs the `rtl` extra (amaranth)."""

from . import map_core, map_sim_util, sim_util  # noqa: F401  (public submodules)
from .core import NanukCore

__all__ = [
    "NanukCore",
    # public submodules (also what pdoc documents)
    "core",
    "map_core",
    "map_sim_util",
    "sim_util",
]
