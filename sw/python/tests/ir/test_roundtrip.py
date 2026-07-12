"""IR round-trip: serialize -> deserialize -> lower is byte-identical to
lowering the original in-memory program."""

from nanuk.ir import nanuk_ir_pb2 as ir
from nanuk.ir.lower import to_asm


def rich_program() -> ir.ParserProgram:
    """Exercises every op kind and terminator kind."""
    return ir.ParserProgram(
        ir_version=1,
        states=[
            ir.ParserState(
                name="start",
                ops=[
                    ir.ParserOp(mark=ir.Mark(hdr_id=0, emit_sethdr=True, debug_name="outer")),
                    ir.ParserOp(extract=ir.Extract(
                        value_id=1, bit_offset=0, width=48, debug_name="outer.key")),
                    ir.ParserOp(emit_smd=ir.EmitSmd(value_id=1, slot=0)),
                    ir.ParserOp(extract=ir.Extract(
                        value_id=2, bit_offset=48, width=4, debug_name="outer.len")),
                    ir.ParserOp(shift=ir.Shift(value_id=3, src_value_id=2, amount=1)),
                    ir.ParserOp(advance=ir.Advance(const_bytes=7)),
                    ir.ParserOp(advance=ir.Advance(value_id=3)),
                    ir.ParserOp(mark=ir.Mark(emit_sethdr=False, debug_name="inner")),
                ],
                terminator=ir.Terminator(dispatch=ir.Dispatch(
                    value_id=1,
                    cases=[
                        ir.Case(match=0xBEEF, target_state="take"),
                        ir.Case(match=0x0800, target_state="start"),
                    ],
                    default=ir.Terminator(halt=ir.Halt(drop=True)),
                )),
            ),
            ir.ParserState(
                name="take",
                ops=[ir.ParserOp(mark=ir.Mark(hdr_id=1, emit_sethdr=True, debug_name="inner"))],
                terminator=ir.Terminator(goto=ir.Goto(target_state="fin")),
            ),
            ir.ParserState(name="fin", terminator=ir.Terminator(halt=ir.Halt(drop=False))),
        ],
    )


def test_round_trip_lowered_asm_is_byte_identical():
    program = rich_program()
    direct = to_asm(program)

    wire = program.SerializeToString()
    reparsed = ir.ParserProgram()
    reparsed.ParseFromString(wire)

    assert reparsed == program
    assert to_asm(reparsed) == direct


def test_lowering_is_deterministic():
    assert to_asm(rich_program()) == to_asm(rich_program())
