"""Parser ISS unit tests: semantics against hand-computed results, every
error code, reserved-bit enforcement, and trace snapshots.

The differential leg (ISS vs the generated C emulator over the demo
corpus) lives in tests/golden/test_iss_differential.py, next to the
harness that drives the emulator.
"""

import struct

from nanuk.isa import encoding
from nanuk.isa.asm import assemble, assemble_with_lines
from nanuk.isa.iss import (
    ERR_HDR_VIOLATION,
    ERR_ILLEGAL,
    ERR_PC_RANGE,
    ERR_SMD_RANGE,
    ERR_STEP_BUDGET,
    STEP_BUDGET,
    VERDICT_ACCEPT,
    VERDICT_ERROR,
    _Machine,
    run_iss,
)


def raw(*words: int) -> bytes:
    return b"".join(struct.pack(">I", w) for w in words)


def test_movi_advr_halt():
    src = "    movi r0, 5\n    advr r0\n    halt accept\n"
    binary, lines = assemble_with_lines(src)
    r = run_iss(binary, b"\x00" * 64, line_map=lines)
    assert (r.verdict, r.error) == (VERDICT_ACCEPT, 0)
    assert r.payload_offset == 5
    assert r.steps == 3
    assert [s.line for s in r.trace] == [1, 2, 3]
    assert r.trace[0].regs == (5, 0, 0, 0)
    assert r.trace[0].pc == 0 and r.trace[2].pc == 2


def test_ext_bit_addressing():
    binary = assemble("    ext r1, 4, 8\n    halt accept\n")
    r = run_iss(binary, b"\xab\xcd")
    assert r.trace[0].regs[1] == 0xBC


def test_hdr_violation_partial_trace():
    binary = assemble("    movi r0, 1\n    advi 300\n    halt accept\n")
    r = run_iss(binary, b"\x00" * 64)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_VIOLATION)
    assert r.steps == 2 and len(r.trace) == 2
    assert r.trace[1].cursor == 0  # violation leaves the cursor unmoved


def test_step_budget():
    binary = assemble("start:\n    jmp start\n")
    r = run_iss(binary, b"")
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_STEP_BUDGET)
    assert r.steps == STEP_BUDGET
    assert len(r.trace) == STEP_BUDGET


def test_illegal_all_zeros():
    r = run_iss(raw(0), b"")
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_ILLEGAL)
    assert r.steps == 1


def test_run_off_end_is_illegal():
    binary = assemble("    movi r0, 0\n")
    r = run_iss(binary, b"")
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_ILLEGAL)
    assert r.steps == 2 and r.trace[1].pc == 1


def test_pc_range_step_order():
    # Unreachable from a real program within the budget (256 < 1024), so
    # exercise step() directly; the budget check must still come first.
    m = _Machine([], b"")
    m.pc = 1024
    m.step()
    assert (m.verdict, m.err) == (VERDICT_ERROR, ERR_PC_RANGE)
    assert m.steps == 0  # pc-range errors are not counted as steps

    m2 = _Machine([], b"")
    m2.pc = 1024
    m2.steps = STEP_BUDGET
    m2.step()
    assert m2.err == ERR_STEP_BUDGET


def test_smd_range():
    # slot 7, 2 units: the encoder rejects it, so build the raw word.
    word = (0x0A << 26) | (0 << 23) | (1 << 21) | (7 << 17)
    r = run_iss(raw(encoding.encode_movi("r0", 1), word), b"")
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_SMD_RANGE)


def test_reserved_bits_and_bad_reg_are_illegal():
    r = run_iss(raw(encoding.encode_movi("r0", 1) | (1 << 22)), b"")
    assert r.error == ERR_ILLEGAL
    r = run_iss(raw((0x04 << 26) | (5 << 23)), b"")  # reg code 5
    assert r.error == ERR_ILLEGAL


def test_shl_truncates_to_64_bits():
    src = (
        "    movi r0, 3\n"
        "    shl r0, r0, 63\n"
        "    shl r0, r0, 1\n"
        "    halt accept\n"
    )
    r = run_iss(assemble(src), b"")
    assert r.trace[1].regs[0] == 1 << 63  # top bit of 3 shifted out
    assert r.trace[2].regs[0] == 0


def test_stmd_msb_first():
    src = (
        "    movi r0, 0x1234\n"
        "    shl r0, r0, 16\n"
        "    stmd 0, r0, 2\n"
        "    halt accept\n"
    )
    r = run_iss(assemble(src), b"")
    assert r.smd == [0x1234, 0, 0, 0, 0, 0, 0, 0]


def test_rz_reads_zero_discards_writes():
    src = "    movi rz, 7\n    advr rz\n    halt accept\n"
    r = run_iss(assemble(src), b"\x00" * 8)
    assert r.payload_offset == 0


def test_trace_snapshots_hdr():
    src = "    advi 14\n    sethdr 3\n    halt accept\n"
    r = run_iss(assemble(src), b"\x00" * 64)
    step = r.trace[1]
    assert step.hdr_present[3] == 1 and step.hdr_offset[3] == 14
    assert r.trace[0].hdr_present[3] == 0


def test_branches_absolute():
    src = (
        "    movi r0, 1\n"
        "    beq r0, rz, drop_it\n"
        "    bne r0, rz, done\n"
        "drop_it:\n"
        "    halt drop\n"
        "done:\n"
        "    halt accept\n"
    )
    r = run_iss(assemble(src), b"")
    assert r.verdict == VERDICT_ACCEPT
    assert r.steps == 4  # movi, beq (not taken), bne (taken), halt
    assert [s.pc for s in r.trace] == [0, 1, 2, 4]
