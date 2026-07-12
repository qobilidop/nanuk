"""IR validation: well-formed programs pass; each invariant violation is
rejected with a clear error."""

import pytest

from nanuk.ir import nanuk_ir_pb2 as ir
from nanuk.ir.validate import ValidationError, validate


def halt(drop: bool = False) -> ir.Terminator:
    return ir.Terminator(halt=ir.Halt(drop=drop))


def goto(name: str) -> ir.Terminator:
    return ir.Terminator(goto=ir.Goto(target_state=name))


def extract(vid: int, boff: int = 0, width: int = 16) -> ir.ParserOp:
    return ir.ParserOp(extract=ir.Extract(value_id=vid, bit_offset=boff, width=width))


def valid_program() -> ir.ParserProgram:
    return ir.ParserProgram(
        ir_version=1,
        states=[
            ir.ParserState(
                name="start",
                ops=[
                    ir.ParserOp(mark=ir.Mark(hdr_id=0, emit_sethdr=True)),
                    extract(1, 0, 16),
                    ir.ParserOp(emit_smd=ir.EmitSmd(value_id=1, slot=0)),
                    ir.ParserOp(shift=ir.Shift(value_id=2, src_value_id=1, amount=2)),
                    ir.ParserOp(advance=ir.Advance(value_id=2)),
                ],
                terminator=ir.Terminator(
                    dispatch=ir.Dispatch(
                        value_id=1,
                        cases=[ir.Case(match=7, target_state="fin")],
                        default=halt(drop=True),
                    )
                ),
            ),
            ir.ParserState(name="fin", terminator=halt()),
        ],
    )


def test_valid_program_passes():
    validate(valid_program())  # no exception


def test_wrong_ir_version_rejected():
    p = valid_program()
    p.ir_version = 2
    with pytest.raises(ValidationError, match="ir_version"):
        validate(p)


def test_empty_program_rejected():
    with pytest.raises(ValidationError, match="no states"):
        validate(ir.ParserProgram(ir_version=1))


def test_duplicate_state_names_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(name="a", terminator=halt()),
                ir.ParserState(name="a", terminator=halt())],
    )
    with pytest.raises(ValidationError, match="duplicate state name"):
        validate(p)


def test_unknown_goto_target_rejected():
    p = ir.ParserProgram(ir_version=1, states=[ir.ParserState(name="a", terminator=goto("ghost"))])
    with pytest.raises(ValidationError, match="ghost"):
        validate(p)


def test_unknown_dispatch_case_target_rejected():
    p = valid_program()
    p.states[0].terminator.dispatch.cases[0].target_state = "ghost"
    with pytest.raises(ValidationError, match="ghost"):
        validate(p)


def test_missing_terminator_rejected():
    p = ir.ParserProgram(ir_version=1, states=[ir.ParserState(name="a")])
    with pytest.raises(ValidationError, match="missing terminator"):
        validate(p)


def test_nested_dispatch_default_rejected():
    p = valid_program()
    inner = ir.Dispatch(value_id=1, default=halt())
    p.states[0].terminator.dispatch.default.CopyFrom(ir.Terminator(dispatch=inner))
    with pytest.raises(ValidationError, match="nested Dispatch"):
        validate(p)


@pytest.mark.parametrize("width", [0, 65])
def test_extract_width_out_of_range_rejected(width):
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(name="a", ops=[extract(1, 0, width)], terminator=halt())],
    )
    with pytest.raises(ValidationError, match="width"):
        validate(p)


def test_hdr_id_out_of_range_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(
            name="a",
            ops=[ir.ParserOp(mark=ir.Mark(hdr_id=16, emit_sethdr=True))],
            terminator=halt(),
        )],
    )
    with pytest.raises(ValidationError, match="hdr_id 16"):
        validate(p)


def test_reanchor_mark_hdr_id_is_ignored():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(
            name="a",
            ops=[ir.ParserOp(mark=ir.Mark(hdr_id=99, emit_sethdr=False))],
            terminator=halt(),
        )],
    )
    validate(p)  # no exception: re-anchor marks lower to nothing


def test_smd_slot_overflow_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(
            name="a",
            ops=[extract(1, 0, 48), ir.ParserOp(emit_smd=ir.EmitSmd(value_id=1, slot=6))],
            terminator=halt(),
        )],
    )
    with pytest.raises(ValidationError, match="slots 6..8"):
        validate(p)


def test_shift_amount_out_of_range_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(
            name="a",
            ops=[extract(1), ir.ParserOp(shift=ir.Shift(value_id=2, src_value_id=1, amount=64))],
            terminator=halt(),
        )],
    )
    with pytest.raises(ValidationError, match="shift amount 64"):
        validate(p)


def test_value_id_zero_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(name="a", ops=[extract(0)], terminator=halt())],
    )
    with pytest.raises(ValidationError, match="value_id 0"):
        validate(p)


def test_value_id_reuse_across_states_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[
            ir.ParserState(name="a", ops=[extract(1)], terminator=halt()),
            ir.ParserState(name="b", ops=[extract(1)], terminator=halt()),
        ],
    )
    with pytest.raises(ValidationError, match="reused"):
        validate(p)


def test_use_before_def_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(
            name="a",
            ops=[ir.ParserOp(advance=ir.Advance(value_id=9))],
            terminator=halt(),
        )],
    )
    with pytest.raises(ValidationError, match="before it is defined"):
        validate(p)


def test_cross_state_value_use_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[
            ir.ParserState(name="a", ops=[extract(1)], terminator=goto("b")),
            ir.ParserState(
                name="b",
                ops=[ir.ParserOp(emit_smd=ir.EmitSmd(value_id=1, slot=0))],
                terminator=halt(),
            ),
        ],
    )
    with pytest.raises(ValidationError, match="do not cross states"):
        validate(p)


def test_empty_op_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(name="a", ops=[ir.ParserOp()], terminator=halt())],
    )
    with pytest.raises(ValidationError, match="empty Op"):
        validate(p)


def test_advance_with_no_amount_rejected():
    p = ir.ParserProgram(
        ir_version=1,
        states=[ir.ParserState(name="a", ops=[ir.ParserOp(advance=ir.Advance())], terminator=halt())],
    )
    with pytest.raises(ValidationError, match="no amount"):
        validate(p)
