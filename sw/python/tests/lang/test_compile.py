"""Compilation unit tests: each eDSL construct emits the expected assembly
patterns (asserted on parsed lines, not exact register numbers), plus the
documented compile errors."""

import pytest

from nanuk.lang import CompileError, Header, Parser
from nanuk.testkit.load import load_example

# -- helpers -----------------------------------------------------------------


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


def labels(asm: str) -> list[str]:
    out = []
    for raw in asm.splitlines():
        line = raw.split(";", 1)[0].strip()
        while ":" in line:
            label, line = line.split(":", 1)
            out.append(label.strip())
            line = line.strip()
    return out


def compile_single_state(body) -> str:
    """Compile a one-state parser whose body is `body(s)` + a trailing accept."""
    p = Parser()

    @p.state(start=True)
    def start(s):
        body(s)
        if s._terminator is None:
            s.accept()

    return p.compile()


ETH = Header("eth", dst=48, src=48, ethertype=16)
VLAN = Header("vlan", tci=16, ethertype=16)
IPV4 = Header("ipv4", version=4, ihl=4, tos=8, total_len=16, ident=16,
              flags_frag=16, ttl=8, proto=8, csum=16, src=32, dst=32)


# -- emission patterns ---------------------------------------------------------


def test_mark_with_hdr_id_emits_sethdr():
    asm = compile_single_state(lambda s: s.mark(ETH, hdr_id=5))
    assert instrs(asm)[0] == ("sethdr", ["5"])


def test_mark_without_hdr_id_emits_nothing():
    asm = compile_single_state(lambda s: s.mark(ETH))
    assert instrs(asm) == [("halt", ["accept"])]


def test_extract_emits_ext_with_field_offset_and_width():
    def body(s):
        s.mark(ETH)
        s.extract(ETH.ethertype)

    op, ops = instrs(compile_single_state(body))[0]
    assert op == "ext"
    assert ops[1:] == ["96", "16"]


def test_static_advance_adjusts_subsequent_extract_offsets():
    def body(s):
        s.mark(VLAN)
        s.advance(2)
        s.extract(VLAN.ethertype)  # bit 16 in the header, cursor moved 2 bytes

    ins = instrs(compile_single_state(body))
    assert ins[0] == ("advi", ["2"])
    assert ins[1][0] == "ext" and ins[1][1][1:] == ["0", "16"]


def test_extract_field_still_ahead_of_cursor_after_partial_advance():
    def body(s):
        s.mark(ETH)
        s.advance(6)  # past dst, src/ethertype still ahead
        s.extract(ETH.src)

    ins = instrs(compile_single_state(body))
    assert ins[1][0] == "ext" and ins[1][1][1:] == ["0", "48"]


def test_smd_unit_count_follows_width():
    def body(s):
        s.mark(ETH)
        s.smd(s.extract(ETH.dst), slot=0)        # 48 bits -> 3 units
        s.smd(s.extract(ETH.ethertype), slot=3)  # 16 bits -> 1 unit

    stmds = [i for i in instrs(compile_single_state(body)) if i[0] == "stmd"]
    assert stmds[0][1][0] == "0" and stmds[0][1][2] == "3"
    assert stmds[1][1][0] == "3" and stmds[1][1][2] == "1"


# -- const primitive (MOVI value) ---------------------------------------------


def test_const_emits_movi_and_is_storable_to_md():
    """s.const materializes a 16-bit literal as a value; a following smd stores
    that value's register (the SIIT twin uses this for the md[1] bitmap)."""
    asm = compile_single_state(lambda s: s.smd(s.const(0x0B), slot=1))
    ins = instrs(asm)
    movi = next(i for i in ins if i[0] == "movi")
    stmd = next(i for i in ins if i[0] == "stmd")
    assert int(movi[1][1], 0) == 0x0B
    assert stmd[1][0] == "1" and stmd[1][2] == "1"   # slot 1, one unit
    assert stmd[1][1] == movi[1][0]                  # stores the const's register


@pytest.mark.parametrize("bad", [-1, 0x10000, True])
def test_const_out_of_range_rejected(bad):
    with pytest.raises(CompileError, match="const"):
        compile_single_state(lambda s: s.const(bad))


def test_const_builds_movi_ir_and_interp_iss_agree():
    """The whole mini-vertical: eDSL -> IR Movi -> lowered `movi` -> and
    pp_interp agrees with the ISS on the assembled words (steps included)."""
    from nanuk.ir.pp_interp import pp_interp
    from nanuk.ir.pp_lower import to_pp_asm
    from nanuk.isa.pp_asm import assemble_with_lines
    from nanuk.isa.pp_iss import run_pp_iss

    p = Parser()

    @p.state(start=True)
    def start(s):
        s.smd(s.const(0x0B), slot=1)
        s.smd(s.const(0xFFFF), slot=2)
        s.accept()

    prog = p.build_ir()
    movis = [op.movi.imm for st in prog.states for op in st.ops
             if op.WhichOneof("op") == "movi"]
    assert movis == [0x0B, 0xFFFF]              # IR carries the Movi ops

    asm = to_pp_asm(prog)
    assert any(i[0] == "movi" for i in instrs(asm))   # lowered to movi words

    pkt = bytes(20)
    ri = pp_interp(prog, pkt)
    assert (ri.md[1], ri.md[2]) == (0x0B, 0xFFFF)
    binary, lines = assemble_with_lines(asm)
    rs = run_pp_iss(binary, pkt, line_map=lines)
    assert (ri.verdict, ri.error, ri.md, ri.steps) == (
        rs.verdict, rs.error, rs.md, rs.steps
    )


def test_static_advance_emits_advi():
    asm = compile_single_state(lambda s: s.advance(14))
    assert instrs(asm)[0] == ("advi", ["14"])


def test_dynamic_advance_materializes_shift_then_advr():
    def body(s):
        s.mark(IPV4)
        ihl = s.extract(IPV4.ihl)
        s.advance(ihl << 2)

    ins = instrs(compile_single_state(body))
    assert [i[0] for i in ins[:3]] == ["ext", "shl", "advr"]
    ext_rd = ins[0][1][0]
    shl_rd, shl_rs, shamt = ins[1][1]
    assert shl_rs == ext_rd and shamt == "2"
    assert ins[2][1] == [shl_rd]  # advr uses the shifted register


def test_dynamic_advance_of_plain_value_emits_advr_directly():
    def body(s):
        s.mark(IPV4)
        s.advance(s.extract(IPV4.ihl))

    ins = instrs(compile_single_state(body))
    assert [i[0] for i in ins[:2]] == ["ext", "advr"]
    assert ins[1][1] == [ins[0][1][0]]


def test_multiply_by_power_of_two_is_shift_sugar():
    def body(s):
        s.mark(IPV4)
        s.advance(s.extract(IPV4.ihl) * 4)

    shl = next(i for i in instrs(compile_single_state(body)) if i[0] == "shl")
    assert shl[1][2] == "2"


def test_dispatch_emits_movi_beq_chain_and_default_halt():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(ETH)
        ety = s.extract(ETH.ethertype)
        s.advance(14)
        s.dispatch(ety, {0x8100: other, 0x0800: another}, default=s.accept)

    @p.state()
    def other(s):
        s.drop()

    @p.state()
    def another(s):
        s.accept()

    ins = instrs(p.compile())
    ext_rd = ins[0][1][0]
    assert [i[0] for i in ins[1:6]] == ["advi", "movi", "beq", "movi", "beq"]
    movi1, beq1, movi2, beq2 = ins[2], ins[3], ins[4], ins[5]
    assert int(movi1[1][1], 0) == 0x8100 and int(movi2[1][1], 0) == 0x0800
    assert beq1[1] == [ext_rd, movi1[1][0], "other"]
    assert beq2[1] == [ext_rd, movi2[1][0], "another"]
    assert ins[6] == ("halt", ["accept"])


def test_dispatch_default_drop_and_default_state():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(ETH)
        s.dispatch(s.extract(ETH.ethertype), {1: fin}, default=s.drop)

    @p.state()
    def fin(s):
        s.mark(ETH)
        s.dispatch(s.extract(ETH.ethertype), {}, default=start)  # bare jmp

    asm = p.compile()
    ins = instrs(asm)
    assert ("halt", ["drop"]) in ins
    assert ins[-1] == ("jmp", ["start"])


def test_goto_emits_jmp():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.goto(tail)

    @p.state()
    def tail(s):
        s.accept()

    assert instrs(p.compile())[0] == ("jmp", ["tail"])


def test_accept_and_drop_emit_halt():
    assert instrs(compile_single_state(lambda s: s.accept()))[-1] == ("halt", ["accept"])
    assert instrs(compile_single_state(lambda s: s.drop()))[-1] == ("halt", ["drop"])


def test_states_emit_in_definition_order_start_first():
    p = Parser()

    @p.state()
    def beta(s):
        s.accept()

    @p.state(start=True)
    def alpha(s):
        s.goto(beta)

    @p.state()
    def gamma(s):
        s.accept()

    assert labels(p.compile()) == ["alpha", "beta", "gamma"]


def test_remark_after_dynamic_advance_reanchors():
    def body(s):
        s.mark(IPV4)
        s.advance(s.extract(IPV4.ihl) << 2)
        s.mark(VLAN)              # re-anchor at the new (unknown) cursor
        s.extract(VLAN.tci)       # legal again, offset relative to new mark

    ins = instrs(compile_single_state(body))
    assert ins[-2][0] == "ext" and ins[-2][1][1:] == ["0", "16"]


def test_demo_program_compiles_and_assembles():
    from nanuk.isa.pp_asm import assemble

    build = load_example("l2l3l4/parse.py").build

    asm = build()
    assert labels(asm)[0] == "start"
    binary = assemble(asm)  # register pressure fits: no compile/assemble error
    assert len(binary) % 4 == 0 and len(binary) > 0


def test_demo_output_matches_pre_ir_golden():
    """Stage-3 refactor guard: eDSL -> IR -> lower must reproduce the asm the
    direct (pre-IR) compiler produced, byte for byte."""
    from pathlib import Path

    build = load_example("l2l3l4/parse.py").build

    golden = (Path(__file__).parent / "golden" / "l2l3l4.asm").read_text()
    assert build() == golden


# -- error cases ---------------------------------------------------------------


def test_out_of_registers_lists_live_values():
    def body(s):
        s.mark(IPV4)
        s.extract(IPV4.total_len)
        s.extract(IPV4.ident)
        s.extract(IPV4.flags_frag)
        s.extract(IPV4.csum)  # fourth concurrent value: r3 is reserved

    with pytest.raises(CompileError, match=r"out of registers") as exc:
        compile_single_state(body)
    assert "ipv4.total_len" in str(exc.value)
    assert "ipv4.flags_frag" in str(exc.value)


def test_extract_behind_cursor_is_an_error():
    def body(s):
        s.mark(ETH)
        s.advance(14)
        s.extract(ETH.ethertype)  # header fully consumed

    with pytest.raises(CompileError, match="behind the cursor"):
        compile_single_state(body)


def test_extract_across_dynamic_advance_is_an_error():
    def body(s):
        s.mark(IPV4)
        ihl = s.extract(IPV4.ihl)
        s.advance(ihl << 2)
        s.extract(IPV4.proto)  # anchor is from before the register advance

    with pytest.raises(CompileError, match="no longer statically known"):
        compile_single_state(body)


def test_extract_without_mark_is_an_error():
    with pytest.raises(CompileError, match="not marked"):
        compile_single_state(lambda s: s.extract(ETH.dst))


def test_dispatch_constant_wider_than_16_bits_is_an_error():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(ETH)
        s.dispatch(s.extract(ETH.ethertype), {0x10000: start}, default=s.accept)

    with pytest.raises(CompileError, match="16 bits"):
        p.compile()


def test_multiply_by_non_power_of_two_is_an_error():
    def body(s):
        s.mark(IPV4)
        s.extract(IPV4.ihl) * 3

    with pytest.raises(CompileError, match="powers of two"):
        compile_single_state(body)


def test_shift_amount_out_of_range_is_an_error():
    def body(s):
        s.mark(IPV4)
        s.extract(IPV4.ihl) << 64

    with pytest.raises(CompileError, match="0..63"):
        compile_single_state(body)


def test_statement_after_terminator_is_an_error():
    def body(s):
        s.accept()
        s.advance(1)

    with pytest.raises(CompileError, match="after the state was terminated"):
        compile_single_state(body)


def test_unterminated_state_is_an_error():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.advance(1)

    with pytest.raises(CompileError, match="does not end with a terminator"):
        p.compile()


def test_missing_or_multiple_start_states_are_errors():
    p = Parser()

    @p.state()
    def lonely(s):
        s.accept()

    with pytest.raises(CompileError, match="exactly one start state"):
        p.compile()

    q = Parser()

    @q.state(start=True)
    def a(s):
        s.accept()

    @q.state(start=True)
    def b(s):
        s.accept()

    with pytest.raises(CompileError, match="exactly one start state"):
        q.compile()


def test_dispatch_target_must_be_a_state():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(ETH)
        s.dispatch(s.extract(ETH.ethertype), {1: "nowhere"}, default=s.accept)

    with pytest.raises(CompileError, match="not a.*state of this parser"):
        p.compile()


def test_hdr_id_out_of_range_is_an_error():
    with pytest.raises(CompileError, match="0..15"):
        compile_single_state(lambda s: s.mark(ETH, hdr_id=16))


def test_duplicate_state_name_is_an_error():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.accept()

    with pytest.raises(CompileError, match="duplicate state name"):

        @p.state()
        def start(s):  # noqa: F811
            s.accept()
