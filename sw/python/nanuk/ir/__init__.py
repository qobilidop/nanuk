"""nanuk.ir: the nanuk IR (protobuf, package nanuk.ir.v0; schema at
spec/proto/, gencode vendored here via scripts/gen.py), its validation
pass, the IR -> assembly lowering (stage 3 of the nanuk project), and the
IR-level interpreter (differential chassis for the satellites).

The symbolic executor (`nanuk.ir.pp_symex`) is deliberately not imported
here: it needs z3-solver, a dev-group-only dependency."""

from .pp_interp import ParserInterpResult, pp_interp
from .map_interp import MatchActionInterpResult, map_interp
from .pp_lower import LowerError, to_pp_asm
from .map_lower import to_map_asm
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
from .pp_validate import IR_VERSION, ValidationError, pp_validate
from .map_validate import map_validate

__all__ = [
    "IR_VERSION",
    "Advance",
    "Case",
    "Dispatch",
    "EmitSmd",
    "Extract",
    "Goto",
    "Halt",
    "ParserInterpResult",
    "LowerError",
    "Mark",
    "ParserOp",
    "ParserProgram",
    "ParserState",
    "Shift",
    "Terminator",
    "MatchActionInterpResult",
    "ValidationError",
    "pp_interp",
    "map_interp",
    "to_pp_asm",
    "to_map_asm",
    "pp_validate",
    "map_validate",
    # public submodules (also what pdoc documents; symex excluded — z3)
    "pp_lower",
    "map_lower",
]
