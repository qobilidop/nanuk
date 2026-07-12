"""nanuk_amaranth: the Nanuk core's two processors in Amaranth — PP
(parser processor) and MAP (match-action processor) — plus their
simulation utilities. Lives in hw/amaranth as its own project; the cosim
tests validate it against the Nanuk package's ISS oracle."""

from . import export, map, map_sim_util, pp_sim_util  # noqa: F401  (public submodules)
from .map import MatchActionProcessor
from .pp import ParserProcessor

__all__ = [
    "ParserProcessor",
    "MatchActionProcessor",
    # public submodules (also what pdoc documents)
    "pp",
    "map",
    "export",
    "map_sim_util",
    "pp_sim_util",
]
