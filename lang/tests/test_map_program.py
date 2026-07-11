"""MapProgram eDSL unit tests: IR shape for the L2-forward program,
byte-alignment guards, terminator discipline, table/header binding."""

import pytest

from nanuk_lang import CompileError, Header, MapProgram, MD_FLOOD
from nanuk_lang.programs.l2l3l4 import eth, ipv4

from nanuk_ir.lower_map import to_map_asm
from nanuk_ir.validate_map import validate_map


def l2fwd() -> MapProgram:
    mp = MapProgram()
    l2 = mp.table("l2", key_width=48, action_width=8)
    ethh = mp.header(eth, hdr_id=0)

    @mp.state(start=True)
    def forward(s):
        dmac = s.load(ethh.dst)
        act = s.lookup(l2, dmac, miss=flood)
        s.send(act)

    @mp.state()
    def flood(s):
        s.send(s.load_md(MD_FLOOD))

    return mp


def test_l2fwd_ir_shape():
    program = l2fwd().build_ir()
    validate_map(program)
    assert [t.debug_name for t in program.tables] == ["l2"]
    assert [st.name for st in program.states] == ["forward", "flood"]
    fw = program.states[0]
    assert fw.ops[0].WhichOneof("op") == "load"
    assert fw.ops[0].load.hdr_id == 0
    assert fw.ops[0].load.nbytes == 6
    assert fw.ops[0].load.debug_name == "eth.dst"
    assert fw.ops[1].lookup.miss_state == "flood"
    assert fw.terminator.WhichOneof("kind") == "send"


def test_l2fwd_compiles_to_five_instructions():
    asm = l2fwd().compile()
    codelines = [
        line for line in asm.splitlines()
        if line.strip() and not line.strip().startswith(";")
        and not line.strip().endswith(":")
    ]
    assert len(codelines) == 5


def test_non_byte_aligned_field_rejected():
    mp = MapProgram()
    ipv4h = mp.header(ipv4, hdr_id=2)
    with pytest.raises(CompileError, match="byte-aligned"):
        _ = ipv4h.version  # 4-bit field


def test_ttl_is_byte_aligned():
    mp = MapProgram()
    ipv4h = mp.header(ipv4, hdr_id=2)
    f = ipv4h.ttl
    assert (f.byte_offset, f.nbytes) == (8, 1)


def test_statement_after_terminator_rejected():
    mp = MapProgram()

    @mp.state(start=True)
    def s0(s):
        s.drop()
        s.load_md(MD_FLOOD)

    with pytest.raises(CompileError, match="after the state was terminated"):
        mp.build_ir()


def test_lookup_requires_declared_table():
    mp = MapProgram()

    @mp.state(start=True)
    def s0(s):
        s.lookup("not-a-table", s.load_md(MD_FLOOD), miss=s0)

    with pytest.raises(CompileError, match="declared table"):
        mp.build_ir()


def test_raw_headroom_store_and_dispatch():
    mp = MapProgram()

    @mp.state(start=True)
    def s0(s):
        tag = s.load_md(5)
        s.dispatch(tag, {0x4E4B: strip}, default=plain)

    @mp.state()
    def strip(s):
        v = s.const(0x88B5)
        s.store(v, hdr=15, byte_offset=-10, nbytes=2)
        s.send(s.load_md(MD_FLOOD), delta=-22)

    @mp.state()
    def plain(s):
        s.send(s.load_md(MD_FLOOD))

    program = mp.build_ir()
    validate_map(program)
    asm = to_map_asm(program)
    assert "st      r0, 15, -10, 2" in asm
    assert "send    r1, -22" in asm
