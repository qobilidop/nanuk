"""MAP IR validation: well-formed programs pass; each invariant violation
is rejected with a clear error. Also: MAP-only terminators are rejected in
parser programs (and vice versa)."""

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.validate import ValidationError, validate
from nanuk_ir.validate_map import validate_map

from tests.irbuild import drop, l2_table, l2fwd_program, load


def test_valid_program_passes():
    validate_map(l2fwd_program())


def test_ttl_shaped_program_passes():
    p = ir.MapProgram(
        ir_version=1,
        tables=[l2_table()],
        states=[
            ir.MapState(
                name="ttl",
                ops=[
                    load(1, hdr=2, off=8, n=1),
                    ir.MapOp(add=ir.MapAdd(value_id=2, src_value_id=1, imm=-1)),
                    ir.MapOp(
                        store=ir.MapStore(
                            value_id=2, hdr_id=2, byte_offset=8, nbytes=1
                        )
                    ),
                    ir.MapOp(csum=ir.CsumUpdate(hdr_id=2, byte_offset=0)),
                ],
                terminator=drop(),
            ),
        ],
    )
    validate_map(p)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda p: setattr(p, "ir_version", 2), "ir_version"),
        (lambda p: p.ClearField("states"), "no states"),
        (lambda p: setattr(p.tables[0], "table_id", 4), "out of range"),
        (lambda p: setattr(p.tables[0], "key_width", 65), "key_width"),
        (lambda p: setattr(p.states[0].ops[0].load, "nbytes", 9), "nbytes"),
        (lambda p: setattr(p.states[0].ops[0].load, "byte_offset", -513), "offset"),
        (lambda p: setattr(p.states[0].ops[1].lookup, "miss_state", "nope"), "miss"),
        (lambda p: setattr(p.states[0].ops[1].lookup, "table_id", 2), "undeclared"),
        (lambda p: setattr(p.states[0].ops[1].lookup, "key_value_id", 9), "before"),
        (lambda p: setattr(p.states[1].ops[0].load_md, "field", 16), "field"),
        (lambda p: setattr(p.states[0].terminator.send, "delta", 999), "delta"),
        (lambda p: setattr(p.states[1], "name", "forward"), "duplicate"),
        # Cross-state value use: flood's send uses forward's value 2.
        (
            lambda p: setattr(p.states[1].terminator.send, "bitmap_value_id", 2),
            "before it is defined",
        ),
        # Value id reuse across states.
        (lambda p: setattr(p.states[1].ops[0].load_md, "value_id", 1), "reused"),
    ],
)
def test_violations_rejected(mutate, message):
    p = l2fwd_program()
    mutate(p)
    with pytest.raises(ValidationError, match=message):
        validate_map(p)


def test_parser_terminators_rejected_in_map():
    p = l2fwd_program()
    p.states[1].terminator.CopyFrom(ir.Terminator(halt=ir.Halt(drop=False)))
    with pytest.raises(ValidationError, match="not allowed in"):
        validate_map(p)


def test_map_terminators_rejected_in_parser():
    p = ir.Program(
        ir_version=1,
        states=[ir.State(name="start", terminator=drop())],
    )
    with pytest.raises(ValidationError, match="not allowed in"):
        validate(p)
