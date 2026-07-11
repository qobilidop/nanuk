"""IR -> assembly lowering: emitted mnemonic patterns (register-number
agnostic), per-state register allocation, and ISA-encoding-limit errors."""

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.lower import LowerError, to_asm


def instrs(asm: str) -> list[tuple[str, list[str]]]:
    """Parse asm text into (mnemonic, operands) pairs, dropping labels/comments."""
    out = []
    for raw in asm.splitlines():
        line = raw.split(";", 1)[0].strip()
        while ":" in line:
            line = line.split(":", 1)[1].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        ops = [o.strip() for o in parts[1].split(",")] if len(parts) > 1 else []
        out.append((parts[0].lower(), ops))
    return out


def halt(drop: bool = False) -> ir.Terminator:
    return ir.Terminator(halt=ir.Halt(drop=drop))


def extract(vid: int, boff: int = 0, width: int = 16, name: str = "") -> ir.Op:
    return ir.Op(
        extract=ir.Extract(value_id=vid, bit_offset=boff, width=width, debug_name=name)
    )


def one_state(ops, terminator=None, name="start") -> ir.Program:
    return ir.Program(
        ir_version=1,
        states=[ir.State(name=name, ops=ops, terminator=terminator or halt())],
    )


def test_extract_lowers_to_ext_with_offset_and_width():
    asm = to_asm(one_state([extract(1, 96, 16, "eth.ethertype")]))
    op, ops = instrs(asm)[0]
    assert op == "ext" and ops[1:] == ["96", "16"]
    assert "; eth.ethertype" in asm


def test_extract_without_debug_name_gets_vid_comment():
    asm = to_asm(one_state([extract(7)]))
    assert "; v7" in asm


def test_mark_lowers_to_sethdr_only_when_emit_sethdr():
    asm = to_asm(one_state([
        ir.Op(mark=ir.Mark(hdr_id=3, emit_sethdr=True, debug_name="udp")),
        ir.Op(mark=ir.Mark(emit_sethdr=False, debug_name="ghost")),
    ]))
    assert instrs(asm) == [("sethdr", ["3"]), ("halt", ["accept"])]
    assert "ghost" not in asm


def test_smd_units_follow_value_width():
    asm = to_asm(one_state([
        extract(1, 0, 48),
        ir.Op(emit_smd=ir.EmitSmd(value_id=1, slot=0)),
        extract(2, 48, 16),
        ir.Op(emit_smd=ir.EmitSmd(value_id=2, slot=3)),
    ]))
    stmds = [i for i in instrs(asm) if i[0] == "stmd"]
    assert stmds[0][1][0] == "0" and stmds[0][1][2] == "3"
    assert stmds[1][1][0] == "3" and stmds[1][1][2] == "1"


def test_shift_then_advance_lowers_to_shl_advr():
    asm = to_asm(one_state([
        extract(1, 4, 4, "ipv4.ihl"),
        ir.Op(shift=ir.Shift(value_id=2, src_value_id=1, amount=2)),
        ir.Op(advance=ir.Advance(value_id=2)),
    ]))
    ins = instrs(asm)
    assert [i[0] for i in ins[:3]] == ["ext", "shl", "advr"]
    ext_rd = ins[0][1][0]
    shl_rd, shl_rs, shamt = ins[1][1]
    assert shl_rs == ext_rd and shamt == "2"
    assert ins[2][1] == [shl_rd]
    assert "; ipv4.ihl << 2" in asm  # derived debug name


def test_const_advance_lowers_to_advi():
    asm = to_asm(one_state([ir.Op(advance=ir.Advance(const_bytes=14))]))
    assert instrs(asm)[0] == ("advi", ["14"])


def test_dispatch_lowers_to_movi_beq_chain():
    prog = ir.Program(
        ir_version=1,
        states=[
            ir.State(
                name="start",
                ops=[extract(1, 0, 16, "ety")],
                terminator=ir.Terminator(dispatch=ir.Dispatch(
                    value_id=1,
                    cases=[ir.Case(match=0x8100, target_state="other")],
                    default=ir.Terminator(goto=ir.Goto(target_state="start")),
                )),
            ),
            ir.State(name="other", terminator=halt(drop=True)),
        ],
    )
    ins = instrs(to_asm(prog))
    ext_rd = ins[0][1][0]
    movi, beq = ins[1], ins[2]
    assert movi[0] == "movi" and int(movi[1][1], 0) == 0x8100
    assert beq == ("beq", [ext_rd, movi[1][0], "other"])
    assert ins[3] == ("jmp", ["start"])
    assert ins[4] == ("halt", ["drop"])


def test_goto_lowers_to_jmp():
    prog = ir.Program(
        ir_version=1,
        states=[
            ir.State(name="a", terminator=ir.Terminator(goto=ir.Goto(target_state="b"))),
            ir.State(name="b", terminator=halt()),
        ],
    )
    assert instrs(to_asm(prog))[0] == ("jmp", ["b"])


def test_labels_emitted_per_state_in_order():
    prog = ir.Program(
        ir_version=1,
        states=[ir.State(name=n, terminator=halt()) for n in ("start", "mid", "end")],
    )
    asm = to_asm(prog)
    labels = [l.rstrip(":") for l in asm.splitlines() if l.endswith(":")]
    assert labels == ["start", "mid", "end"]


def test_registers_are_reallocated_per_state():
    prog = ir.Program(
        ir_version=1,
        states=[
            ir.State(name="a", ops=[extract(1)], terminator=halt()),
            ir.State(name="b", ops=[extract(2)], terminator=halt()),
        ],
    )
    exts = [i for i in instrs(to_asm(prog)) if i[0] == "ext"]
    assert exts[0][1][0] == exts[1][1][0]  # both states start from the same reg


def test_out_of_registers_lists_live_values():
    ops = [extract(i, name=f"f{i}") for i in (1, 2, 3, 4)]
    with pytest.raises(LowerError, match="out of registers") as exc:
        to_asm(one_state(ops))
    assert "f1" in str(exc.value) and "f3" in str(exc.value)


def test_dispatch_constant_over_16_bits_is_a_lower_error():
    prog = ir.Program(
        ir_version=1,
        states=[ir.State(
            name="start",
            ops=[extract(1)],
            terminator=ir.Terminator(dispatch=ir.Dispatch(
                value_id=1,
                cases=[ir.Case(match=0x10000, target_state="start")],
                default=halt(),
            )),
        )],
    )
    with pytest.raises(LowerError, match="16 bits"):
        to_asm(prog)


def test_advance_over_16_bits_is_a_lower_error():
    with pytest.raises(LowerError, match="ADVI"):
        to_asm(one_state([ir.Op(advance=ir.Advance(const_bytes=0x10000))]))


def test_ext_offset_over_11_bits_is_a_lower_error():
    with pytest.raises(LowerError, match="EXT"):
        to_asm(one_state([extract(1, boff=2048)]))
