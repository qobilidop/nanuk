"""MatchActionProgram eDSL unit tests: IR shape for the L2-forward program,
byte-alignment guards, terminator discipline, table/header binding, and the
metadata-window primitives."""

import pytest

from nanuk.lang import CompileError, MatchActionProgram
from nanuk.testkit.load import load_example
_ex = load_example("l2l3l4/parse.py"); eth, ipv4 = _ex.eth, _ex.ipv4

from nanuk.ir.map_lower import to_map_asm
from nanuk.ir.map_validate import map_validate


def l2fwd() -> MatchActionProgram:
    mp = MatchActionProgram()
    l2 = mp.table("l2", key_width=48, action_width=8)
    flood_tbl = mp.table("flood", key_width=16, action_width=16, table_id=3)
    ethh = mp.header(eth, hdr_id=0)

    @mp.state(start=True)
    def forward(s):
        dmac = s.load(ethh.dst)
        act = s.lookup(l2, dmac, miss=flood)
        s.send(egress=act)

    @mp.state()
    def flood(s):
        ing = s.load_md(0)
        fl = s.lookup(flood_tbl, ing, miss=dark)
        s.send(egress=fl)

    @mp.state()
    def dark(s):
        s.drop()

    return mp


def test_l2fwd_ir_shape():
    program = l2fwd().build_ir()
    map_validate(program)
    assert [t.debug_name for t in program.tables] == ["l2", "flood"]
    assert [t.table_id for t in program.tables] == [0, 3]
    assert [st.name for st in program.states] == ["forward", "flood", "dark"]
    fw = program.states[0]
    assert fw.ops[0].WhichOneof("op") == "load"
    assert fw.ops[0].load.hdr_id == 0
    assert fw.ops[0].load.nbytes == 6
    assert fw.ops[0].load.debug_name == "eth.dst"
    assert fw.ops[1].lookup.miss_state == "flood"
    # send(egress=...) sugar: a store_md to slot 0, then a bare send.
    assert fw.ops[2].WhichOneof("op") == "store_md"
    assert fw.ops[2].store_md.slot == 0
    assert fw.terminator.WhichOneof("kind") == "send"


def test_l2fwd_compiles_to_nine_instructions():
    asm = l2fwd().compile()
    codelines = [
        line for line in asm.splitlines()
        if line.strip() and not line.strip().startswith(";")
        and not line.strip().endswith(":")
    ]
    assert len(codelines) == 9  # the flood-table form


def test_non_byte_aligned_field_rejected():
    mp = MatchActionProgram()
    ipv4h = mp.header(ipv4, hdr_id=2)
    with pytest.raises(CompileError, match="byte-aligned"):
        _ = ipv4h.version  # 4-bit field


def test_ttl_is_byte_aligned():
    mp = MatchActionProgram()
    ipv4h = mp.header(ipv4, hdr_id=2)
    f = ipv4h.ttl
    assert (f.byte_offset, f.nbytes) == (8, 1)


def test_statement_after_terminator_rejected():
    mp = MatchActionProgram()

    @mp.state(start=True)
    def s0(s):
        s.drop()
        s.load_md(0)

    with pytest.raises(CompileError, match="after the state was terminated"):
        mp.build_ir()


def test_lookup_requires_declared_table():
    mp = MatchActionProgram()

    @mp.state(start=True)
    def s0(s):
        s.lookup("not-a-table", s.load_md(0), miss=s0)

    with pytest.raises(CompileError, match="declared table"):
        mp.build_ir()


def test_explicit_table_id_collision_rejected():
    mp = MatchActionProgram()
    mp.table("a", key_width=8, action_width=8, table_id=3)
    with pytest.raises(CompileError, match="already declared"):
        mp.table("b", key_width=8, action_width=8, table_id=3)
    # Auto ids skip explicitly-placed ones.
    t = mp.table("c", key_width=8, action_width=8)
    assert t.table_id == 0


def test_csum_sequence():
    mp = MatchActionProgram()
    ipv4h = mp.header(ipv4, hdr_id=2)

    @mp.state(start=True)
    def fix(s):
        vihl = s.load(None, hdr=2, byte_offset=0, nbytes=1)
        ihl = s.and_imm(vihl, 0x000F)
        hlen = s.shift(ihl, 2)
        zero = s.const(0)
        s.store(zero, hdr=2, byte_offset=10, nbytes=2)
        ck = s.csum(hlen, ipv4h)
        s.store(ck, hdr=2, byte_offset=10, nbytes=2)
        s.drop()

    program = mp.build_ir()
    map_validate(program)
    asm = to_map_asm(program)
    assert "andi" in asm and "shli" in asm and "csum" in asm


def test_raw_headroom_store_and_dispatch():
    mp = MatchActionProgram()
    flood_tbl = mp.table("flood", key_width=16, action_width=16, table_id=3)

    @mp.state(start=True)
    def s0(s):
        tag = s.load_md(5)
        s.dispatch(tag, {0x4E4B: strip}, default=plain)

    @mp.state()
    def strip(s):
        v = s.const(0x88B5)
        s.store(v, hdr=15, byte_offset=-10, nbytes=2)
        ing = s.load_md(0)
        fl = s.lookup(flood_tbl, ing, miss=dark)
        s.send(egress=fl, delta=-22)

    @mp.state()
    def plain(s):
        ing = s.load_md(0)
        fl = s.lookup(flood_tbl, ing, miss=dark)
        s.send(egress=fl)

    @mp.state()
    def dark(s):
        s.drop()

    program = mp.build_ir()
    map_validate(program)
    asm = to_map_asm(program)
    assert "st      r0, 15, -10, 2" in asm
    assert "send    -22" in asm


def test_md_slot_bounds():
    mp = MatchActionProgram()

    @mp.state(start=True)
    def s0(s):
        s.load_md(8)

    with pytest.raises(CompileError, match="out of range"):
        mp.build_ir()
