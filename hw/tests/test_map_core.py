"""MapCore unit tests (pysim, no golden model needed): each case mirrors a
spec/map-test/*.sail assertion so the RTL and the Sail model are pinned to
the same behaviors even before the cosim rig runs."""

from dataclasses import dataclass

import pytest

from nanuk_hw.map_core import (
    ERR_HDR_ABSENT,
    ERR_ILLEGAL,
    ERR_PC_RANGE,
    ERR_SEND_RANGE,
    ERR_STEP_BUDGET,
    ERR_WINDOW_VIOLATION,
    STEP_BUDGET,
    VERDICT_DROP,
    VERDICT_ERROR,
    VERDICT_SENT,
)
from nanuk_hw.map_sim_util import run_map_one

from nanuk_spec import map_encoding as e


@dataclass(frozen=True)
class StubPP:
    """Minimal stand-in for ParseResult (hdr/smd fields only)."""

    hdr_present: list
    hdr_offset: list
    smd: list


def pp_none() -> StubPP:
    return StubPP([0] * 16, [0] * 16, [0] * 8)


def pp_h2(off=14, smd=None) -> StubPP:
    return StubPP(
        [0, 0, 1] + [0] * 13, [0, 0, off] + [0] * 13, smd or [0] * 8
    )


def words(*ws) -> bytes:
    return b"".join(w.to_bytes(4, "big") for w in ws)


def run(prog, packet=bytes(64), pp=None, tables=(), ingress=0):
    return run_map_one(prog, packet, pp or pp_none(), list(tables), ingress)


# --- Straight-line instructions (Task 1) ---------------------------------


def test_movi_addi_wrap():
    r = run(
        words(
            e.encode_addi("r2", "rz", -1),   # 0 - 1 wraps to all-ones
            e.encode_movi("r0", 0x8100),
            e.encode_addi("r1", "r0", 1),
            e.encode_send("rz", 0),
        )
    )
    assert r.verdict == VERDICT_SENT
    assert r.regs[2] == (1 << 64) - 1
    assert r.regs[0] == 0x8100
    assert r.regs[1] == 0x8101


@pytest.mark.parametrize("ingress,expected", [(0, 0xE), (1, 0xD), (2, 0xB), (3, 0x7)])
def test_ldmd_flood_mask(ingress, expected):
    r = run(
        words(e.encode_ldmd("r1", 9), e.encode_send("r1", 0)),
        ingress=ingress,
    )
    assert r.egress == expected


def test_ldmd_fields():
    pp = pp_h2(smd=[0x1234] + [0] * 7)
    r = run(
        words(
            e.encode_ldmd("r0", 0),   # smd slot
            e.encode_ldmd("r1", 8),   # ingress
            e.encode_ldmd("r2", 10),  # hdr_present bitmap
            e.encode_ldmd("r3", 14),  # reserved -> 0
            e.encode_send("rz", 0),
        ),
        pp=pp,
        ingress=2,
    )
    assert r.regs[0] == 0x1234
    assert r.regs[1] == 2
    assert r.regs[2] == 0x4
    assert r.regs[3] == 0


def test_branch_skip():
    r = run(
        words(
            e.encode_movi("r0", 5),
            e.encode_movi("r1", 5),
            e.encode_beq("r0", "r1", 4),
            e.encode_drop(),
            e.encode_bne("r0", "r1", 6),
            e.encode_send("rz", 0),
            e.encode_drop(),
        )
    )
    assert r.verdict == VERDICT_SENT
    assert r.steps == 5


def test_send_masks_and_delta():
    r = run(words(e.encode_movi("r0", 0xFF), e.encode_send("r0", 22)))
    assert r.verdict == VERDICT_SENT
    assert r.egress == 0xF
    assert r.delta == 22


def test_send_negative_delta():
    r = run(words(e.encode_send("rz", -22)))
    assert r.verdict == VERDICT_SENT
    assert r.delta == -22
    # frame = window[32+22 : 32+64) — stripped 22 zero-headroom... no:
    # negative delta strips FRAME bytes; packet is 64 zeros, so 42 remain.
    assert r.frame is not None and len(r.frame) == 42


def test_send_range_errors():
    ok = run(words(e.encode_send("rz", 32)))
    assert ok.verdict == VERDICT_SENT
    bad_hi = run(words(e.encode_send("rz", 33)))
    assert bad_hi.verdict == VERDICT_ERROR and bad_hi.error == ERR_SEND_RANGE
    bad_lo = run(words(e.encode_send("rz", -64)))
    assert bad_lo.verdict == VERDICT_ERROR and bad_lo.error == ERR_SEND_RANGE
    edge = run(words(e.encode_send("rz", -63)))
    assert edge.verdict == VERDICT_SENT and edge.delta == -63


def test_totality_illegal_pc_budget():
    zeros = run(words(0x00000000))
    assert zeros.verdict == VERDICT_ERROR and zeros.error == ERR_ILLEGAL
    assert zeros.steps == 1
    far = run(words(e.encode_jmp(0xFFFF)))
    assert far.verdict == VERDICT_ERROR and far.error == ERR_PC_RANGE
    spin = run(words(e.encode_jmp(0)))
    assert spin.verdict == VERDICT_ERROR and spin.error == ERR_STEP_BUDGET
    assert spin.steps == STEP_BUDGET


def test_reserved_bits_are_illegal():
    r = run(words(e.encode_drop() | 1))
    assert r.verdict == VERDICT_ERROR and r.error == ERR_ILLEGAL


# --- LD/ST (Task 2) --------------------------------------------------------


def test_st_ld_roundtrip_and_headroom():
    packet = bytes(range(64))
    r = run(
        words(
            e.encode_movi("r0", 0x8100),
            e.encode_st("r0", e.H_FRAME, -22, 2),   # into headroom
            e.encode_ld("r1", e.H_FRAME, -22, 2),   # read it back
            e.encode_ld("r2", e.H_FRAME, 0, 6),     # first 6 frame bytes
            e.encode_send("rz", 0),
        ),
        packet=packet,
    )
    assert r.verdict == VERDICT_SENT
    assert r.regs[1] == 0x8100
    assert r.regs[2] == 0x000102030405


def test_hdr_relative_ld(pp=None):
    packet = bytes(range(64))
    r = run(
        words(e.encode_ld("r3", 2, 8, 1), e.encode_send("rz", 0)),
        packet=packet,
        pp=pp_h2(off=14),
    )
    assert r.regs[3] == 22  # frame[14 + 8]


def test_ld_st_totality():
    absent = run(words(e.encode_ld("r0", 3, 0, 1), e.encode_send("rz", 0)))
    assert absent.verdict == VERDICT_ERROR and absent.error == ERR_HDR_ABSENT
    past = run(words(e.encode_ld("r0", e.H_FRAME, 64, 1), e.encode_send("rz", 0)))
    assert past.error == ERR_WINDOW_VIOLATION
    straddle = run(words(e.encode_ld("r0", e.H_FRAME, 63, 2), e.encode_send("rz", 0)))
    assert straddle.error == ERR_WINDOW_VIOLATION
    last = run(words(e.encode_ld("r0", e.H_FRAME, 63, 1), e.encode_send("rz", 0)))
    assert last.verdict == VERDICT_SENT
    below = run(words(e.encode_st("r0", e.H_FRAME, -33, 1), e.encode_send("rz", 0)))
    assert below.error == ERR_WINDOW_VIOLATION


def test_st_edits_show_in_frame():
    packet = bytes(64)
    r = run(
        words(
            e.encode_movi("r0", 0xBEEF),
            e.encode_st("r0", e.H_FRAME, 10, 2),
            e.encode_send("rz", 0),
        ),
        packet=packet,
    )
    assert r.frame is not None
    assert r.frame[10:12] == b"\xbe\xef"
    assert r.frame[:10] == bytes(10) and r.frame[12:] == bytes(52)


# --- LOOKUP (Task 3) --------------------------------------------------------


def _l2_table():
    from nanuk_spec.map_harness import Table

    return Table(
        key_width=48,
        action_width=8,
        entries={0x02DEADBEEF01: 0x4, 0x0A0B0C0D0E0F: 0x8},
    )


def test_lookup_hit_and_miss():
    prog = words(
        e.encode_ld("r0", e.H_FRAME, 0, 6),
        e.encode_lookup("r1", 0, "r0", 3),
        e.encode_send("r1", 0),
        e.encode_drop(),
    )
    hit = run(
        prog,
        packet=bytes.fromhex("02deadbeef01") + bytes(58),
        tables=[_l2_table()],
    )
    assert hit.verdict == VERDICT_SENT and hit.egress == 0x4
    assert hit.steps == 3
    miss = run(prog, packet=bytes(64), tables=[_l2_table()])
    assert miss.verdict == VERDICT_DROP
    assert miss.steps == 3  # LD, LOOKUP(miss->3), DROP
    assert miss.regs[1] == 0


def test_lookup_key_masking():
    # Garbage above 48 bits in the key register must not affect the match.
    prog = words(
        e.encode_ld("r0", e.H_FRAME, 0, 8),  # 8 bytes: 2 garbage + 6 DMAC
        e.encode_lookup("r1", 0, "r0", 3),
        e.encode_send("r1", 0),
        e.encode_drop(),
    )
    # r0 = 0xbeef02deadbeef01; the 48-bit mask keeps the low 6 bytes,
    # which equal the table entry 0x02deadbeef01.
    packet = bytes.fromhex("beef02deadbeef01") + bytes(56)
    r = run(prog, packet=packet, tables=[_l2_table()])
    assert r.verdict == VERDICT_SENT and r.egress == 0x4


def test_lookup_always_miss_tables():
    from nanuk_spec.map_harness import Table

    prog = words(
        e.encode_lookup("r1", 1, "rz", 2),
        e.encode_drop(),                      # hit path (should not happen)
        e.encode_send("rz", 0),               # miss target
    )
    empty_cfg = run(prog, tables=[_l2_table(), Table(48, 8, {})])
    assert empty_cfg.verdict == VERDICT_SENT
    unconfigured = run(prog, tables=[_l2_table()])
    assert unconfigured.verdict == VERDICT_SENT
    out_of_plane = run(
        words(
            e.encode_lookup("r1", 12, "rz", 2),
            e.encode_drop(),
            e.encode_send("rz", 0),
        ),
        tables=[_l2_table()],
    )
    assert out_of_plane.verdict == VERDICT_SENT


# --- CSUMUPD (Task 4) --------------------------------------------------------

# The classic worked example: checksum 0xB861.
IPV4_HDR = bytes.fromhex("450000730000400040110000c0a80001c0a800c7")


def _ipv4_packet(ck: bytes = b"\x00\x00", ttl: int = 0x40) -> bytes:
    hdr = bytearray(IPV4_HDR)
    hdr[8] = ttl
    hdr[10:12] = ck
    return bytes(14) + bytes(hdr) + bytes(30)  # 14B fake eth + header + pad


def test_csumupd_golden():
    prog = words(e.encode_csumupd(2, 0), e.encode_send("rz", 0))
    r = run(prog, packet=_ipv4_packet(), pp=pp_h2(off=14))
    assert r.verdict == VERDICT_SENT
    assert r.frame is not None and r.frame[24:26] == b"\xb8\x61"
    stale = run(prog, packet=_ipv4_packet(ck=b"\xde\xad"), pp=pp_h2(off=14))
    assert stale.frame is not None and stale.frame[24:26] == b"\xb8\x61"


def test_csumupd_after_ttl_dec():
    prog = words(
        e.encode_ld("r0", 2, 8, 1),
        e.encode_addi("r0", "r0", -1),
        e.encode_st("r0", 2, 8, 1),
        e.encode_csumupd(2, 0),
        e.encode_send("rz", 0),
    )
    r = run(prog, packet=_ipv4_packet(ck=b"\xb8\x61"), pp=pp_h2(off=14))
    assert r.frame is not None
    assert r.frame[22] == 0x3F
    assert r.frame[24:26] == b"\xb9\x61"


def test_csumupd_totality():
    prog = words(e.encode_csumupd(2, 0), e.encode_send("rz", 0))
    bad_ihl = bytearray(_ipv4_packet())
    bad_ihl[14] = 0x40  # version 4, IHL 0
    r = run(prog, packet=bytes(bad_ihl), pp=pp_h2(off=14))
    assert r.verdict == VERDICT_ERROR and r.error == ERR_WINDOW_VIOLATION
    truncated = run(prog, packet=_ipv4_packet()[:24], pp=pp_h2(off=14))
    assert truncated.error == ERR_WINDOW_VIOLATION
    absent = run(prog, packet=_ipv4_packet(), pp=pp_none())
    assert absent.error == ERR_HDR_ABSENT
