"""nanuk-ir: the nanuk IR (protobuf, package nanuk.ir.v0), its validation
pass, and the IR -> assembly lowering (stage 3 of the nanuk project)."""

from .lower import LowerError, to_asm
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

__all__ = [
    "IR_VERSION",
    "Advance",
    "Case",
    "Dispatch",
    "EmitSmd",
    "Extract",
    "Goto",
    "Halt",
    "LowerError",
    "Mark",
    "Op",
    "Program",
    "Shift",
    "State",
    "Terminator",
    "ValidationError",
    "to_asm",
    "validate",
]
