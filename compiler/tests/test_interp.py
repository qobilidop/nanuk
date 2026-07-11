"""IR interpreter semantics, mirrored from the frozen ISA v0 semantics
(stage-1 plan / spec/model). No emulator needed: these are pure-Python
unit tests; the emulator comparison lives in test_differential.py."""

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.interp import (
    STEP_BUDGET,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
    ERR_NONE,
    ERR_HDR_VIOLATION,
    ERR_STEP_BUDGET,
    interp,
)


# -- tiny builders -----------------------------------------------------------

def prog(*states: ir.State) -> ir.Program:
    return ir.Program(ir_version=1, states=list(states))


def halt(drop: bool = False) -> ir.Terminator:
    return ir.Terminator(halt=ir.Halt(drop=drop))


def goto(target: str) -> ir.Terminator:
    return ir.Terminator(goto=ir.Goto(target_state=target))


# -- halts, run loop, budget -------------------------------------------------

def test_halt_accept():
    r = interp(prog(ir.State(name="s", terminator=halt(drop=False))), b"\x00")
    assert r.verdict == VERDICT_ACCEPT
    assert r.error == ERR_NONE
    assert r.accepted
    assert r.payload_offset == 0
    assert r.steps == 1  # the HALT itself


def test_halt_drop():
    r = interp(prog(ir.State(name="s", terminator=halt(drop=True))), b"\x00")
    assert r.verdict == VERDICT_DROP
    assert not r.accepted


def test_goto_chains_states_and_costs_one_step_each():
    p = prog(
        ir.State(name="a", terminator=goto("b")),
        ir.State(name="b", terminator=goto("c")),
        ir.State(name="c", terminator=halt()),
    )
    r = interp(p, b"")
    assert r.accepted
    assert r.steps == 3  # jmp, jmp, halt


def test_goto_loop_exhausts_step_budget():
    p = prog(
        ir.State(name="a", terminator=goto("b")),
        ir.State(name="b", terminator=goto("a")),
    )
    r = interp(p, b"\x00" * 8)
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_STEP_BUDGET
    assert r.steps == STEP_BUDGET  # saturated: error on the 257th attempt


def test_empty_packet_is_fine():
    r = interp(prog(ir.State(name="s", terminator=halt())), b"")
    assert r.accepted


def test_start_state_is_states_zero_not_name():
    p = prog(
        ir.State(name="not_start", terminator=halt(drop=True)),
        ir.State(name="start", terminator=halt(drop=False)),
    )
    assert interp(p, b"").verdict == VERDICT_DROP


def test_outputs_are_fresh_per_run():
    p = prog(ir.State(name="s", terminator=halt()))
    a, b = interp(p, b""), interp(p, b"")
    assert a.hdr_present == [0] * 16 and a.smd == [0] * 8
    assert a.hdr_present is not b.hdr_present  # no shared mutable state


# -- linear ops (values mirror the stage-1 Sail test vectors) ----------------

def one_state(ops: list[ir.Op], term: ir.Terminator | None = None) -> ir.Program:
    return prog(ir.State(name="s", ops=ops, terminator=term or halt()))


def ext(vid: int, boff: int, width: int) -> ir.Op:
    return ir.Op(extract=ir.Extract(value_id=vid, bit_offset=boff, width=width))


def smd_op(vid: int, slot: int) -> ir.Op:
    return ir.Op(emit_smd=ir.EmitSmd(value_id=vid, slot=slot))


def test_extract_crossing_byte_boundary():
    # bits 4..11 of 0xAB,0xCD = 0xBC (network order, bit 0 = MSB)
    p = one_state([ext(1, 4, 8), smd_op(1, 0)])
    assert interp(p, b"\xab\xcd").smd[0] == 0xBC


def test_extract_sub_byte_ihl():
    # low nibble of 0x45 (IPv4 version/IHL byte) = 5
    p = one_state([ext(1, 4, 4), smd_op(1, 0)])
    assert interp(p, b"\x45").smd[0] == 5


def test_extract_past_hdr_limit_is_error_1_and_counted():
    p = one_state([ext(1, 0, 16)])
    r = interp(p, b"\xff")  # 16 bits wanted, 8 available
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_VIOLATION)
    assert r.steps == 1  # the failing EXT was fetched, hence counted


def test_advance_const_moves_cursor_into_payload_offset():
    p = one_state([ir.Op(advance=ir.Advance(const_bytes=3))])
    r = interp(p, b"\x00" * 8)
    assert r.accepted and r.payload_offset == 3


def test_advance_past_hdr_limit_is_error_1():
    p = one_state([ir.Op(advance=ir.Advance(const_bytes=9))])
    r = interp(p, b"\x00" * 8)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_VIOLATION)
    assert r.payload_offset == 0  # cursor unchanged by the failing ADVI


def test_advance_by_value_uses_low_16_bits():
    # 24-bit value 0x010002: ADVR must advance by 0x0002, not 0x10002.
    p = one_state([ext(1, 0, 24), ir.Op(advance=ir.Advance(value_id=1))])
    r = interp(p, b"\x01\x00\x02" + b"\x00" * 5)
    assert r.accepted and r.payload_offset == 2


def test_shift_widens_and_truncates_at_64():
    # 60-bit extract shifted by 8: width saturates at 64, value masked.
    body = [
        ext(1, 0, 60),
        ir.Op(shift=ir.Shift(value_id=2, src_value_id=1, amount=8)),
        smd_op(2, 0),  # 64-bit value -> 4 slots
    ]
    r = interp(one_state(body), b"\xff" * 8)
    assert r.smd[:4] == [0xFFFF, 0xFFFF, 0xFFFF, 0xFF00]


def test_mark_records_cursor_and_reanchor_is_free():
    body = [
        ir.Op(advance=ir.Advance(const_bytes=2)),
        ir.Op(mark=ir.Mark(hdr_id=3, emit_sethdr=True)),
        ir.Op(mark=ir.Mark(emit_sethdr=False)),  # re-anchor: no step, no record
    ]
    r = interp(one_state(body), b"\x00" * 4)
    assert r.hdr(3) == 2
    assert r.hdr_present == [0, 0, 0, 1] + [0] * 12
    assert r.steps == 3  # advi + sethdr + halt; the re-anchor cost nothing


def test_emit_smd_multi_slot_msb_first():
    # 48-bit DMAC aa:bb:cc:dd:ee:01 -> slots 0..2 MSB-first (stage-1 vector)
    p = one_state([ext(1, 0, 48), smd_op(1, 0)])
    r = interp(p, bytes.fromhex("aabbccddee01") + b"\x00" * 8)
    assert r.smd[:3] == [0xAABB, 0xCCDD, 0xEE01]
