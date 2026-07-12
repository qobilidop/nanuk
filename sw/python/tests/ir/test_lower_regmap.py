"""Annotated lowering: per-instruction value→register bindings, with the
asm text pinned byte-identical to the plain lowering."""

from nanuk.ir import nanuk_ir_pb2 as ir
from nanuk.ir.lower import to_asm, to_asm_annotated
from nanuk.ir.lower_map import to_map_asm, to_map_asm_annotated


def parser_prog() -> ir.ParserProgram:
    return ir.ParserProgram(ir_version=1, states=[
        ir.ParserState(
            name="start",
            ops=[ir.ParserOp(extract=ir.Extract(value_id=1, bit_offset=96, width=16,
                                          debug_name="eth.type"))],
            terminator=ir.Terminator(dispatch=ir.Dispatch(
                value_id=1,
                cases=[ir.Case(match=0x0800, target_state="done")],
                default=ir.Terminator(halt=ir.Halt(drop=True)),
            )),
        ),
        ir.ParserState(name="done", ops=[],
                 terminator=ir.Terminator(halt=ir.Halt(drop=False))),
    ])


def test_parser_bindings_and_identical_text():
    text, bindings = to_asm_annotated(parser_prog())
    assert text == to_asm(parser_prog())
    instr_lines = [ln for ln in text.splitlines() if ln.startswith("    ")]
    assert len(bindings) == len(instr_lines)
    # ext binds eth.type to r0; the dispatch movi/beq pair keeps it live;
    # r3 (scratch) never appears in a binding.
    assert bindings[0] == {"r0": "eth.type"}
    assert bindings[1] == {"r0": "eth.type"}  # movi r3, ...
    assert bindings[2] == {"r0": "eth.type"}  # beq r0, r3, ...
    assert all("r3" not in b for b in bindings)
    assert bindings[-1] == {}  # done: halt with nothing live


def map_reuse_prog() -> ir.MatchActionProgram:
    ops = []
    for i in range(1, 5):  # four short-lived constants, stored immediately
        ops.append(ir.MatchActionOp(const=ir.MapConst(value_id=i, imm=i,
                                              debug_name=f"c{i}")))
        ops.append(ir.MatchActionOp(store=ir.MapStore(value_id=i, hdr_id=15,
                                              byte_offset=i, nbytes=1)))
    ops.append(ir.MatchActionOp(load_md=ir.MapLoadMd(value_id=9, field=9,
                                             debug_name="flood")))
    return ir.MatchActionProgram(ir_version=1, states=[
        ir.MatchActionState(name="start", ops=ops,
                    terminator=ir.Terminator(send=ir.MapSend(bitmap_value_id=9))),
    ])


def test_map_bindings_show_register_reuse():
    text, bindings = to_map_asm_annotated(map_reuse_prog())
    assert text == to_map_asm(map_reuse_prog())
    instr_lines = [ln for ln in text.splitlines() if ln.startswith("    ")]
    assert len(bindings) == len(instr_lines)
    # last-use liveness: every constant reuses r0 after its store.
    assert bindings[0] == {"r0": "c1"}
    assert bindings[2] == {"r0": "c2"}
    assert bindings[4] == {"r0": "c3"}
    # the store keeps its operand's binding visible at the store itself
    assert bindings[1] == {"r0": "c1"}
