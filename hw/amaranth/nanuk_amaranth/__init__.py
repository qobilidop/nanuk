"""nanuk_amaranth: the Amaranth RTL below the ISA — parser core, MAP core,
and their simulation utilities. Lives in hw/amaranth as its own project;
the cosim tests validate it against the nanuk package's ISS oracle."""

from . import export, map_core, map_sim_util, sim_util  # noqa: F401  (public submodules)
from .core import NanukCore

__all__ = [
    "NanukCore",
    # public submodules (also what pdoc documents)
    "core",
    "export",
    "map_core",
    "map_sim_util",
    "sim_util",
]
