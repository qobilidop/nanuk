"""nanuk.ir: the nanuk IR (protobuf, package nanuk.ir.v0; schema at
spec/proto/, gencode vendored here via scripts/gen.py), its validation
pass, the IR -> assembly lowering (stage 3 of the nanuk project), and the
IR-level interpreter (differential chassis for the satellites).

The symbolic executor (`nanuk.ir.symex`) is deliberately not imported
here: it needs z3-solver, a dev-group-only dependency."""

from .interp import InterpResult, interp
from .interp_map import MapInterpResult, interp_map
from .lower import LowerError, to_asm
from .lower_map import to_map_asm
from .nanuk_ir_pb2 import (
    Advance,
    Case,
    Dispatch,
    EmitSmd,
    Extract,
    Goto,
    Halt,
    Mark,
    ParserOp,
    ParserProgram,
    ParserState,
    Shift,
    Terminator,
)
from .validate import IR_VERSION, ValidationError, validate
from .validate_map import validate_map

__all__ = [
    "IR_VERSION",
    "Advance",
    "Case",
    "Dispatch",
    "EmitSmd",
    "Extract",
    "Goto",
    "Halt",
    "InterpResult",
    "LowerError",
    "Mark",
    "ParserOp",
    "ParserProgram",
    "ParserState",
    "Shift",
    "Terminator",
    "MapInterpResult",
    "ValidationError",
    "interp",
    "interp_map",
    "to_asm",
    "to_map_asm",
    "validate",
    "validate_map",
    # public submodules (also what pdoc documents; symex excluded — z3)
    "lower",
    "lower_map",
]
