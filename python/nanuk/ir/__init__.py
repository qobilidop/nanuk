"""nanuk-ir: the nanuk IR (protobuf, package nanuk.ir.v0), its validation
pass, the IR -> assembly lowering (stage 3 of the nanuk project), and the
IR-level interpreter (differential chassis for the satellites)."""

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
    Op,
    Program,
    Shift,
    State,
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
    "Op",
    "Program",
    "Shift",
    "State",
    "Terminator",
    "MapInterpResult",
    "ValidationError",
    "interp",
    "interp_map",
    "to_asm",
    "to_map_asm",
    "validate",
    "validate_map",
]
