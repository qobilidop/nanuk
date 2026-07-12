"""nanuk.lang: a Python eDSL compiling protocol-level parser descriptions
to nanuk parser ISA v0 assembly (stage 2 of the nanuk project)."""

from . import compile  # noqa: F401  (public submodule)
from .header import CompileError, Header
from .map_program import MD_FLOOD, MD_HDRS, MD_INGRESS, MapProgram
from .parser import Parser

__all__ = [
    "MD_FLOOD",
    "MD_HDRS",
    "MD_INGRESS",
    "CompileError",
    "Header",
    "MapProgram",
    "Parser",
    # public submodules (also what pdoc documents)
    "compile",
    "header",
    "map_program",
    "parser",
]
