"""pysim unit tests for NanukCore.

These mirror the Sail test suites (spec/parser-test/test_state.sail,
test_decode.sail, test_exec_linear.sail, test_exec_control.sail): same
inputs, same expected values. Where a Sail test pokes machine state
directly, the RTL test reaches the same state through architecturally
visible means (short programs and packet contents).

Instruction words are built with nanuk.isa.encoding (the assembler's
encoder, itself pinned to the Sail encdec golden words).
"""

from nanuk.isa import encoding as enc

from nanuk.rtl.core import (
    ERR_HDR_VIOLATION,
    ERR_ILLEGAL,
    ERR_PC_RANGE,
    ERR_SMD_RANGE,
    ERR_STEP_BUDGET,
    IMEM_WORDS,
    STEP_BUDGET,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
)
from nanuk.rtl.sim_util import run_core, run_one

# Ethernet-ish prefix used by the Sail exec tests (test_exec_linear.sail):
# 0x45 = IPv4 version 4, IHL 5; plen 4.
TEST_PKT = bytes.fromhex("4500ABCD")

HALT_ACCEPT = enc.encode_halt(False)
HALT_DROP = enc.encode_halt(True)


# --- EXT (mirrors test_exec_linear.sail + test_read_pkt_bits) --------------

def test_ext_basic():
    r = run_one([enc.encode_ext("r0", 16, 16), HALT_ACCEPT], TEST_PKT)
    assert r.regs[0] == 0x0000_0000_0000_ABCD  # 16 bits at bit 16
    assert r.verdict == VERDICT_ACCEPT  # EXT in range does not error-halt


def test_ext_subbyte():
    r = run_one(
        [
            enc.encode_ext("r1", 0, 4),  # IPv4 version nibble
            enc.encode_ext("r2", 4, 4),  # IHL nibble
            HALT_ACCEPT,
        ],
        TEST_PKT,
    )
    assert r.regs[1] == 4  # version nibble = 4
    assert r.regs[2] == 5  # IHL nibble = 5


def test_ext_respects_cursor():
    r = run_one(
        [
            enc.encode_advi(2),
            enc.encode_ext("r0", 0, 8),  # 8 bits at cursor 2
            HALT_ACCEPT,
        ],
        TEST_PKT,
    )
    assert r.regs[0] == 0xAB  # EXT is cursor-relative


def test_ext_violation():
    # plen = 4: bits 29..32 cross plen*8 = 32.
    r = run_one([enc.encode_ext("r0", 29, 4), HALT_ACCEPT], TEST_PKT)
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_HDR_VIOLATION
    assert r.steps == 1  # the faulting instruction is counted


def test_ext_boundary_is_legal():
    # pos + sz exactly == hdr_limit * 8 is LEGAL: bits 28..31 with plen 4.
    r = run_one([enc.encode_ext("r0", 28, 4), HALT_ACCEPT], TEST_PKT)
    assert r.verdict == VERDICT_ACCEPT
    assert r.regs[0] == 0xD  # low nibble of 0xCD


def test_ext_bit_ordering():
    # Pins the bit 0 = MSB mapping (mirrors test_read_pkt_bits): AB CD EF.
    pkt = bytes.fromhex("ABCDEF")
    r = run_one(
        [
            enc.encode_ext("r0", 0, 8),
            enc.encode_ext("r1", 0, 16),
            enc.encode_ext("r2", 0, 4),
            enc.encode_ext("r3", 4, 4),
            HALT_ACCEPT,
        ],
        pkt,
    )
    assert r.regs[0] == 0xAB
    assert r.regs[1] == 0xABCD
    assert r.regs[2] == 0xA  # high nibble of byte 0 (bit 0 = MSB)
    assert r.regs[3] == 0xB  # low nibble of byte 0
    r = run_one(
        [
            enc.encode_ext("r0", 4, 8),  # byte-boundary crossing
            enc.encode_ext("r1", 0, 24),
            HALT_ACCEPT,
        ],
        pkt,
    )
    assert r.regs[0] == 0xBC
    assert r.regs[1] == 0xABCDEF


# --- ADVI / ADVR ------------------------------------------------------------

def test_advi_ok_then_violation_boundary():
    # Mirrors test_advi: 3 -> ok, +1 to exactly hdr_limit -> legal, +1 -> err.
    r = run_one(
        [
            enc.encode_advi(3),
            enc.encode_advi(1),  # cursor == plen == 4: legal
            enc.encode_advi(1),  # past the end: violation
            HALT_ACCEPT,
        ],
        TEST_PKT,
    )
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_HDR_VIOLATION
    assert r.payload_offset == 4  # cursor unchanged by the failed advance
    assert r.steps == 3


def test_advi_to_exact_limit_is_legal():
    r = run_one([enc.encode_advi(4), HALT_ACCEPT], TEST_PKT)
    assert r.verdict == VERDICT_ACCEPT
    assert r.payload_offset == 4


def test_advr_uses_low16():
    # Upper 48 register bits must be ignored (defined semantics).
    pkt = bytes.fromhex("FFFF000000000002")
    r = run_one(
        [
            enc.encode_ext("r1", 0, 64),  # r1 = 0xFFFF_0000_0000_0002
            enc.encode_advr("r1"),
            HALT_ACCEPT,
        ],
        pkt,
    )
    assert r.regs[1] == 0xFFFF_0000_0000_0002
    assert r.verdict == VERDICT_ACCEPT
    assert r.payload_offset == 2  # ADVR used rs[15:0] only


def test_advr_violation():
    r = run_one(
        [
            enc.encode_movi("r0", 5),
            enc.encode_advr("r0"),  # 5 > plen 4
            HALT_ACCEPT,
        ],
        TEST_PKT,
    )
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_HDR_VIOLATION


# --- MOVI / SHL -------------------------------------------------------------

def test_movi_zero_extends():
    # Dirty all 64 bits of r0 first, then MOVI must clear the upper 48.
    pkt = b"\xff" * 8
    r = run_one(
        [
            enc.encode_ext("r0", 0, 64),  # r0 = all-ones
            enc.encode_movi("r0", 0x8100),
            HALT_ACCEPT,
        ],
        pkt,
    )
    assert r.regs[0] == 0x0000_0000_0000_8100


def test_shl():
    pkt = bytes.fromhex("8000000000000001")
    r = run_one(
        [
            enc.encode_movi("r1", 5),
            enc.encode_shl("r1", "r1", 2),   # 5 << 2 = 20 (IHL*4)
            enc.encode_ext("r2", 0, 64),     # r2 = 0x8000_0000_0000_0001
            enc.encode_shl("r2", "r2", 1),   # truncates at 64 bits
            HALT_ACCEPT,
        ],
        pkt,
    )
    assert r.regs[1] == 20
    assert r.regs[2] == 2


# --- RZ semantics (mirrors test_rz_semantics) -------------------------------

def test_rz_reads_zero_discards_writes():
    r = run_one(
        [
            enc.encode_movi("rz", 0x1234),        # write discarded
            enc.encode_beq("rz", "r0", 3),        # rz reads 0 == r0 (0)
            HALT_DROP,
            HALT_ACCEPT,
        ],
        b"",
    )
    assert r.verdict == VERDICT_ACCEPT
    assert r.regs == [0, 0, 0, 0]


# --- Branches (mirrors test_branches through the run loop) ------------------

def test_beq_taken():
    r = run_one(
        [
            enc.encode_movi("r0", 7),
            enc.encode_movi("r1", 7),
            enc.encode_beq("r0", "r1", 4),
            HALT_DROP,
            HALT_ACCEPT,
        ],
        b"",
    )
    assert r.verdict == VERDICT_ACCEPT
    assert r.steps == 4


def test_beq_not_taken():
    r = run_one(
        [
            enc.encode_movi("r0", 5),
            enc.encode_movi("r1", 7),
            enc.encode_beq("r0", "r1", 4),
            HALT_DROP,
            HALT_ACCEPT,
        ],
        b"",
    )
    assert r.verdict == VERDICT_DROP
    assert r.steps == 4


def test_bne_taken():
    r = run_one(
        [
            enc.encode_movi("r0", 5),
            enc.encode_movi("r2", 7),
            enc.encode_bne("r0", "r2", 4),
            HALT_DROP,
            HALT_ACCEPT,
        ],
        b"",
    )
    assert r.verdict == VERDICT_ACCEPT


def test_bne_not_taken():
    r = run_one(
        [
            enc.encode_movi("r0", 7),
            enc.encode_movi("r2", 7),
            enc.encode_bne("r0", "r2", 4),
            HALT_DROP,
            HALT_ACCEPT,
        ],
        b"",
    )
    assert r.verdict == VERDICT_DROP


def test_jmp():
    r = run_one([enc.encode_jmp(2), HALT_DROP, HALT_ACCEPT], b"")
    assert r.verdict == VERDICT_ACCEPT
    assert r.steps == 2


# --- Run loop, watchdog, illegal (mirrors test_exec_control.sail) -----------

def test_run_loop_program():
    # Exact mirror of test_run_loop_program: MOVI + BEQ (taken) skips the
    # HALT drop; 4 instructions executed.
    r = run_one(
        [
            enc.encode_movi("r0", 0x0007),
            enc.encode_movi("r1", 0x0007),
            enc.encode_beq("r0", "r1", 0x0004),
            HALT_DROP,  # skipped if branch taken
            HALT_ACCEPT,
        ],
        b"",
    )
    assert r.verdict == VERDICT_ACCEPT  # branch skipped the drop
    assert r.steps == 4


def test_step_budget_watchdog():
    # JMP-to-self: exactly step_budget instructions run, then error 2.
    r = run_one([enc.encode_jmp(0)], b"")
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_STEP_BUDGET
    assert r.steps == STEP_BUDGET  # exactly 256


def test_illegal_all_zeros():
    # Empty program: pc=0 holds 0x00000000, illegal by design.
    r = run_one([], b"")
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_ILLEGAL
    assert r.steps == 1  # halted on the first instruction


def test_illegal_words():
    # Mirrors test_decode.sail test_illegal_words: unassigned opcodes,
    # nonzero reserved bits, bad register codes.
    bad_words = [
        0x30000000,  # opcode 0x0C unassigned
        0xFFFFFFFF,  # all-ones
        0x2C000002,  # HALT with reserved bit set
        0x0801000E,  # ADVI with reserved bit [16] set
        0x0E800000,  # ADVR with register code 5
    ]
    for w in bad_words:
        r = run_one([w, HALT_ACCEPT], b"")
        assert r.verdict == VERDICT_ERROR, hex(w)
        assert r.error == ERR_ILLEGAL, hex(w)
        assert r.steps == 1, hex(w)


def test_pc_range_error():
    # Fall off the end of imem: pc >= 1024 at fetch is error 4.
    words = [enc.encode_jmp(IMEM_WORDS - 1)] + [0] * (IMEM_WORDS - 2)
    words.append(enc.encode_movi("r0", 1))  # at word 1023
    r = run_one(words, b"")
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_PC_RANGE
    assert r.regs[0] == 1
    assert r.steps == 2  # jmp + movi executed; the failed fetch is not


# --- SETHDR (mirrors test_sethdr_program) -----------------------------------

def test_sethdr_program():
    r = run_one(
        [
            enc.encode_sethdr(0x0),
            enc.encode_advi(0x000E),
            enc.encode_sethdr(0x2),
            HALT_ACCEPT,
        ],
        b"",
        plen=0x0020,
    )
    assert r.hdr_present[0] == 1
    assert r.hdr_offset[0] == 0
    assert r.hdr_present[2] == 1
    assert r.hdr_offset[2] == 14
    assert r.hdr_present[1] == 0  # untouched


# --- STMD (mirrors test_stmd_multislot / test_stmd_range_error) -------------

def test_stmd_multislot():
    # 48-bit DMAC-like value into slots 0..2, MSB-first.
    pkt = bytes.fromhex("010203040506")
    r = run_one(
        [
            enc.encode_ext("r0", 0, 48),      # r0 = 0x0000_0102_0304_0506
            enc.encode_stmd(0, "r0", 3),      # 3 units at slot 0
            enc.encode_movi("r1", 0xBEEF),
            enc.encode_stmd(4, "r1", 1),      # single slot elsewhere
            HALT_ACCEPT,
        ],
        pkt,
    )
    assert r.smd[0] == 0x0102  # bits [47:32]
    assert r.smd[1] == 0x0304  # bits [31:16]
    assert r.smd[2] == 0x0506  # bits [15:0]
    assert r.smd[4] == 0xBEEF


def test_stmd_range_error():
    # 2 units at slot 7 -> slots 7,8: range error. The Python encoder
    # (rightly) refuses to emit this, so compose the word from its pieces.
    word = (
        (enc.OP_STMD << 26)
        | (enc.REGS["r0"] << 23)
        | (1 << 21)   # nunits-1 = 1 -> 2 units
        | (7 << 17)   # slot 7
    )
    r = run_one([word, HALT_ACCEPT], b"")
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_SMD_RANGE
    assert r.steps == 1


# --- HALT (mirrors test_halt_verdicts) --------------------------------------

def test_halt_accept_with_payload_offset():
    r = run_one([enc.encode_advi(0x22), HALT_ACCEPT], bytes(64))
    assert r.verdict == VERDICT_ACCEPT
    assert r.error == 0
    assert r.payload_offset == 0x22  # payload offset = cursor at halt
    assert r.steps == 2


def test_halt_drop():
    r = run_one([HALT_DROP], b"")
    assert r.verdict == VERDICT_DROP
    assert r.error == 0
    assert r.payload_offset == 0


# --- start clears architectural state but not imem/packet buffer ------------

def test_start_clears_arch_state_not_imem():
    prog = [
        enc.encode_sethdr(0x5),
        enc.encode_ext("r0", 0, 8),
        enc.encode_stmd(2, "r0", 1),
        enc.encode_advi(1),
        HALT_ACCEPT,
    ]
    # Program loaded once; two packets through the same core instance.
    r1, r2 = run_core(prog, [b"\xaa\xbb", b"\x55"])
    assert (r1.regs[0], r1.smd[2], r1.payload_offset) == (0xAA, 0xAA, 1)
    assert r1.hdr_present[5] == 1
    # Second run reproduces from a clean slate (regs/smd/hdr/steps cleared,
    # program persisted in imem).
    assert (r2.regs[0], r2.smd[2], r2.payload_offset) == (0x55, 0x55, 1)
    assert r2.steps == r1.steps == 5
    assert r2.smd[0] == 0  # no leakage from run 1
    assert r2.hdr_present == r1.hdr_present
