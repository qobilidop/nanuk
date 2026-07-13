"""IR interpreter semantics, mirrored from the frozen ISA v0 semantics
(stage-1 plan / spec/sail/model/pp). No emulator needed: these are pure-Python
unit tests; the emulator comparison lives in test_differential.py."""

import pytest

from nanuk.ir import nanuk_ir_pb2 as ir
from nanuk.ir.pp_interp import (
    STEP_BUDGET,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
    ERR_NONE,
    ERR_HDR_VIOLATION,
    ERR_STEP_BUDGET,
    pp_interp,
)


# -- tiny builders -----------------------------------------------------------

def prog(*states: ir.ParserState) -> ir.ParserProgram:
    return ir.ParserProgram(ir_version=1, states=list(states))


def halt(drop: bool = False) -> ir.Terminator:
    return ir.Terminator(halt=ir.Halt(drop=drop))


def goto(target: str) -> ir.Terminator:
    return ir.Terminator(goto=ir.Goto(target_state=target))


# -- halts, run loop, budget -------------------------------------------------

def test_halt_accept():
    r = pp_interp(prog(ir.ParserState(name="s", terminator=halt(drop=False))), b"\x00")
    assert r.verdict == VERDICT_ACCEPT
    assert r.error == ERR_NONE
    assert r.accepted
    assert r.payload_offset == 0
    assert r.steps == 1  # the HALT itself


def test_halt_drop():
    r = pp_interp(prog(ir.ParserState(name="s", terminator=halt(drop=True))), b"\x00")
    assert r.verdict == VERDICT_DROP
    assert not r.accepted


def test_goto_chains_states_and_costs_one_step_each():
    p = prog(
        ir.ParserState(name="a", terminator=goto("b")),
        ir.ParserState(name="b", terminator=goto("c")),
        ir.ParserState(name="c", terminator=halt()),
    )
    r = pp_interp(p, b"")
    assert r.accepted
    assert r.steps == 3  # jmp, jmp, halt


def test_goto_loop_exhausts_step_budget():
    p = prog(
        ir.ParserState(name="a", terminator=goto("b")),
        ir.ParserState(name="b", terminator=goto("a")),
    )
    r = pp_interp(p, b"\x00" * 8)
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_STEP_BUDGET
    assert r.steps == STEP_BUDGET  # saturated: error on the 257th attempt


def test_empty_packet_is_fine():
    r = pp_interp(prog(ir.ParserState(name="s", terminator=halt())), b"")
    assert r.accepted


def test_start_state_is_states_zero_not_name():
    p = prog(
        ir.ParserState(name="not_start", terminator=halt(drop=True)),
        ir.ParserState(name="start", terminator=halt(drop=False)),
    )
    assert pp_interp(p, b"").verdict == VERDICT_DROP


def test_outputs_are_fresh_per_run():
    p = prog(ir.ParserState(name="s", terminator=halt()))
    a, b = pp_interp(p, b""), pp_interp(p, b"")
    assert a.hdr_present == [0] * 16 and a.md == [0] * 8
    assert a.hdr_present is not b.hdr_present  # no shared mutable state


# -- linear ops (values mirror the stage-1 Sail test vectors) ----------------

def one_state(ops: list[ir.ParserOp], term: ir.Terminator | None = None) -> ir.ParserProgram:
    return prog(ir.ParserState(name="s", ops=ops, terminator=term or halt()))


def ext(vid: int, boff: int, width: int) -> ir.ParserOp:
    return ir.ParserOp(extract=ir.Extract(value_id=vid, bit_offset=boff, width=width))


def md_op(vid: int, slot: int, nunits: int = 1) -> ir.ParserOp:
    return ir.ParserOp(emit_md=ir.MdStore(value_id=vid, slot=slot, nunits=nunits))


def test_extract_crossing_byte_boundary():
    # bits 4..11 of 0xAB,0xCD = 0xBC (network order, bit 0 = MSB)
    p = one_state([ext(1, 4, 8), md_op(1, 0)])
    assert pp_interp(p, b"\xab\xcd").md[0] == 0xBC


def test_extract_sub_byte_ihl():
    # low nibble of 0x45 (IPv4 version/IHL byte) = 5
    p = one_state([ext(1, 4, 4), md_op(1, 0)])
    assert pp_interp(p, b"\x45").md[0] == 5


def test_extract_past_hdr_limit_is_error_1_and_counted():
    p = one_state([ext(1, 0, 16)])
    r = pp_interp(p, b"\xff")  # 16 bits wanted, 8 available
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_VIOLATION)
    assert r.steps == 1  # the failing EXT was fetched, hence counted


def test_advance_const_moves_cursor_into_payload_offset():
    p = one_state([ir.ParserOp(advance=ir.Advance(const_bytes=3))])
    r = pp_interp(p, b"\x00" * 8)
    assert r.accepted and r.payload_offset == 3


def test_advance_past_hdr_limit_is_error_1():
    p = one_state([ir.ParserOp(advance=ir.Advance(const_bytes=9))])
    r = pp_interp(p, b"\x00" * 8)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_VIOLATION)
    assert r.payload_offset == 0  # cursor unchanged by the failing ADVI


def test_advance_by_value_uses_low_16_bits():
    # 24-bit value 0x010002: ADVR must advance by 0x0002, not 0x10002.
    p = one_state([ext(1, 0, 24), ir.ParserOp(advance=ir.Advance(value_id=1))])
    r = pp_interp(p, b"\x01\x00\x02" + b"\x00" * 5)
    assert r.accepted and r.payload_offset == 2


def test_shift_widens_and_truncates_at_64():
    # 60-bit extract shifted by 8: width saturates at 64, value masked.
    body = [
        ext(1, 0, 60),
        ir.ParserOp(shift=ir.Shift(value_id=2, src_value_id=1, amount=8)),
        md_op(2, 0, 4),  # 64-bit value -> 4 slots
    ]
    r = pp_interp(one_state(body), b"\xff" * 8)
    assert r.md[:4] == [0xFFFF, 0xFFFF, 0xFFFF, 0xFF00]


def test_mark_records_cursor_and_reanchor_is_free():
    body = [
        ir.ParserOp(advance=ir.Advance(const_bytes=2)),
        ir.ParserOp(mark=ir.Mark(hdr_id=3, emit_sethdr=True)),
        ir.ParserOp(mark=ir.Mark(emit_sethdr=False)),  # re-anchor: no step, no record
    ]
    r = pp_interp(one_state(body), b"\x00" * 4)
    assert r.hdr(3) == 2
    assert r.hdr_present == [0, 0, 0, 1] + [0] * 12
    assert r.steps == 3  # advi + sethdr + halt; the re-anchor cost nothing


def test_emit_md_multi_slot_msb_first():
    # 48-bit DMAC aa:bb:cc:dd:ee:01 -> slots 0..2 MSB-first (stage-1 vector)
    p = one_state([ext(1, 0, 48), md_op(1, 0, 3)])
    r = pp_interp(p, bytes.fromhex("aabbccddee01") + b"\x00" * 8)
    assert r.md[:3] == [0xAABB, 0xCCDD, 0xEE01]


def test_load_md_reads_seeded_window_and_passes_through():
    p = one_state([
        ir.ParserOp(load_md=ir.MdLoad(value_id=1, slot=0)),
        md_op(1, 4),
    ])
    r = pp_interp(p, b"\x00" * 8, md_in=[0xCAFE])
    assert r.md == [0xCAFE, 0, 0, 0, 0xCAFE, 0, 0, 0]


# -- dispatch and the cost model ---------------------------------------------

def dispatch(vid: int, cases: list[tuple[int, str]], default: ir.Terminator) -> ir.Terminator:
    return ir.Terminator(dispatch=ir.Dispatch(
        value_id=vid,
        cases=[ir.Case(match=m, target_state=t) for m, t in cases],
        default=default,
    ))


def two_way(cases, default=None) -> ir.ParserProgram:
    """start extracts byte 0 and dispatches; 'acc' accepts, 'drp' drops."""
    return prog(
        ir.ParserState(name="start", ops=[ext(1, 0, 8)],
                 terminator=dispatch(1, cases, default or halt(drop=True))),
        ir.ParserState(name="acc", terminator=halt(drop=False)),
        ir.ParserState(name="drp", terminator=halt(drop=True)),
    )


def test_dispatch_first_match_wins():
    p = two_way([(0x42, "drp"), (0x42, "acc")])
    assert pp_interp(p, b"\x42").verdict == VERDICT_DROP


def test_dispatch_falls_through_to_default():
    p = two_way([(0x01, "acc")])
    assert pp_interp(p, b"\x42").verdict == VERDICT_DROP


def test_dispatch_compares_full_value_not_low_16_bits():
    # 24-bit value 0x01BEEF must NOT match case 0xBEEF.
    p = prog(
        ir.ParserState(name="start", ops=[ext(1, 0, 24)],
                 terminator=dispatch(1, [(0xBEEF, "acc")], halt(drop=True))),
        ir.ParserState(name="acc", terminator=halt(drop=False)),
    )
    assert pp_interp(p, b"\x01\xbe\xef").verdict == VERDICT_DROP


def test_dispatch_cost_is_two_per_case_tried():
    # match on 2nd case: ext(1) + [movi+beq](2) + [movi+beq](2) + halt(1) = 6
    p = two_way([(0x01, "drp"), (0x42, "acc")])
    r = pp_interp(p, b"\x42")
    assert r.accepted and r.steps == 6
    # no match: ext(1) + 2*2 tried + default halt(1) = 6
    r = pp_interp(p, b"\x99")
    assert r.verdict == VERDICT_DROP and r.steps == 6


def test_dispatch_default_goto_costs_a_jmp():
    p = two_way([(0x01, "acc")], default=goto("drp"))
    # ext(1) + movi+beq(2) + jmp(1) + halt(1) = 5
    assert pp_interp(p, b"\x42").steps == 5


def test_budget_can_exhaust_mid_dispatch():
    # A 1-state ext+self-goto loop; each lap is 2 steps (ext, jmp), so the
    # 128th lap's ext is step 255, jmp is 256, and the next lap's ext
    # attempt is #257 -> budget error, steps saturated.
    p = prog(ir.ParserState(name="a", ops=[ext(1, 0, 8)], terminator=goto("a")))
    r = pp_interp(p, b"\xff")
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_STEP_BUDGET)
    assert r.steps == STEP_BUDGET


# -- validation and exports ---------------------------------------------------

def test_invalid_program_rejected_by_default():
    from nanuk.ir.pp_validate import ValidationError
    bad = prog(ir.ParserState(name="s", terminator=goto("nowhere")))
    with pytest.raises(ValidationError):
        pp_interp(bad, b"")


def test_check_false_skips_validation():
    ok = prog(ir.ParserState(name="s", terminator=halt()))
    assert pp_interp(ok, b"", check=False).accepted


def test_package_exports():
    import nanuk.ir
    assert nanuk.ir.pp_interp is pp_interp
    from nanuk.ir import ParserInterpResult  # noqa: F401
