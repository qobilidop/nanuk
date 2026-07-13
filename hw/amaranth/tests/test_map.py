"""MatchActionProcessor unit tests (pysim, no golden model needed): each case mirrors a
spec/sail/test/map/*.sail assertion so the RTL and the Sail model are pinned
to the same behaviors even before the cosim rig runs."""

from dataclasses import dataclass

from nanuk_amaranth.map import (
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
from nanuk_amaranth.map_sim_util import run_map_one

from nanuk.isa import map_encoding as e


@dataclass(frozen=True)
class StubPP:
    """Minimal stand-in for ParserResult (hdr map only)."""

    hdr_present: list
    hdr_offset: list


def pp_none() -> StubPP:
    return StubPP([0] * 16, [0] * 16)


def pp_h2(off=14) -> StubPP:
    return StubPP([0, 0, 1] + [0] * 13, [0, 0, off] + [0] * 13)


def words(*ws) -> bytes:
    return b"".join(w.to_bytes(4, "big") for w in ws)


NO_MD = [0] * 8


def run(prog, packet=bytes(64), pp=None, tables=(), md_in=NO_MD):
    return run_map_one(prog, packet, pp or pp_none(), list(tables), md_in)


# --- Straight-line instructions ---------------------------------------------


def test_movi_addi_wrap():
    r = run(
        words(
            e.encode_addi("r2", "rz", -1),   # 0 - 1 wraps to all-ones
            e.encode_movi("r0", 0x8100),
            e.encode_addi("r1", "r0", 1),
            e.encode_send(0),
        )
    )
    assert r.verdict == VERDICT_SENT
    assert r.regs[2] == (1 << 64) - 1
    assert r.regs[0] == 0x8100
    assert r.regs[1] == 0x8101


def test_andi_and_shli():
    r = run(
        words(
            e.encode_movi("r0", 0xFF45),
            e.encode_andi("r1", "r0", 0x000F),
            e.encode_shli("r2", "r1", 2),
            e.encode_movi("r3", 1),
            e.encode_shli("r3", "r3", 63),
            e.encode_drop(),
        )
    )
    assert r.regs[1] == 0x5
    assert r.regs[2] == 0x14
    assert r.regs[3] == 1 << 63


def test_shli_truncates_at_64():
    r = run(
        words(
            e.encode_movi("r0", 1),
            e.encode_shli("r0", "r0", 63),
            e.encode_shli("r0", "r0", 1),
            e.encode_drop(),
        )
    )
    assert r.regs[0] == 0


def test_md_window_pass_through_and_rw():
    md_in = [1, 0, 0, 0xBEEF, 0, 0, 0, 0x7777]
    r = run(
        words(
            e.encode_ldmd("r0", 3),
            e.encode_movi("r1", 0xC0DE),
            e.encode_stmd("r1", 1, 2),
            e.encode_send(0),
        ),
        md_in=md_in,
    )
    assert r.regs[0] == 0xBEEF
    assert r.md == (1, 0, 0xC0DE, 0xBEEF, 0, 0, 0, 0x7777)


def test_stmd_multi_unit_msb_first():
    r = run(
        words(
            e.encode_movi("r0", 0x1122),
            e.encode_stmd("r0", 2, 4),
            e.encode_drop(),
        )
    )
    assert r.md[4:6] == (0x0000, 0x1122)  # high unit first


def test_md_slot_bounds_illegal():
    high = run(words(e.encode_ldmd("r0", 8), e.encode_drop()))
    assert (high.verdict, high.error) == (VERDICT_ERROR, ERR_ILLEGAL)
    over = run(words(e.encode_stmd("r0", 4, 6), e.encode_drop()))
    assert (over.verdict, over.error) == (VERDICT_ERROR, ERR_ILLEGAL)


def test_md_delivered_on_drop_and_error():
    md_in = [7] + [0] * 7
    dropped = run(words(e.encode_drop()), md_in=md_in)
    assert dropped.md[0] == 7  # pass-through even without SEND
    errored = run(words(e.encode_ldmd("r0", 9)), md_in=md_in)
    assert errored.verdict == VERDICT_ERROR and errored.md[0] == 7


def test_branch_skip():
    r = run(
        words(
            e.encode_movi("r0", 5),
            e.encode_movi("r1", 5),
            e.encode_beq("r0", "r1", 4),
            e.encode_drop(),
            e.encode_bne("r0", "r1", 6),
            e.encode_send(0),
            e.encode_drop(),
        )
    )
    assert r.verdict == VERDICT_SENT
    assert r.steps == 5


def test_send_delta():
    r = run(words(e.encode_send(22)))
    assert r.verdict == VERDICT_SENT
    assert r.delta == 22


def test_send_negative_delta():
    r = run(words(e.encode_send(-22)))
    assert r.verdict == VERDICT_SENT
    assert r.delta == -22
    # negative delta strips FRAME bytes; packet is 64 zeros, so 42 remain.
    assert r.frame is not None and len(r.frame) == 42


def test_send_range_errors():
    ok = run(words(e.encode_send(32)))
    assert ok.verdict == VERDICT_SENT
    bad_hi = run(words(e.encode_send(33)))
    assert bad_hi.verdict == VERDICT_ERROR and bad_hi.error == ERR_SEND_RANGE
    bad_lo = run(words(e.encode_send(-64)))
    assert bad_lo.verdict == VERDICT_ERROR and bad_lo.error == ERR_SEND_RANGE
    edge = run(words(e.encode_send(-63)))
    assert edge.verdict == VERDICT_SENT and edge.delta == -63


def test_old_register_send_is_illegal():
    # rs = r1 in the retired encoding: bits [25:23] nonzero -> ILLEGAL.
    r = run(words(0x2C82C000))
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_ILLEGAL)


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


# --- LD/ST -------------------------------------------------------------------


def test_st_ld_roundtrip_and_headroom():
    packet = bytes(range(64))
    r = run(
        words(
            e.encode_movi("r0", 0x8100),
            e.encode_st("r0", e.H_FRAME, -22, 2),   # into headroom
            e.encode_ld("r1", e.H_FRAME, -22, 2),   # read it back
            e.encode_ld("r2", e.H_FRAME, 0, 6),     # first 6 frame bytes
            e.encode_send(0),
        ),
        packet=packet,
    )
    assert r.verdict == VERDICT_SENT
    assert r.regs[1] == 0x8100
    assert r.regs[2] == 0x000102030405


def test_hdr_relative_ld():
    packet = bytes(range(64))
    r = run(
        words(e.encode_ld("r3", 2, 8, 1), e.encode_send(0)),
        packet=packet,
        pp=pp_h2(off=14),
    )
    assert r.regs[3] == 22  # frame[14 + 8]


def test_ld_st_totality():
    absent = run(words(e.encode_ld("r0", 3, 0, 1), e.encode_send(0)))
    assert absent.verdict == VERDICT_ERROR and absent.error == ERR_HDR_ABSENT
    past = run(words(e.encode_ld("r0", e.H_FRAME, 64, 1), e.encode_send(0)))
    assert past.error == ERR_WINDOW_VIOLATION
    straddle = run(words(e.encode_ld("r0", e.H_FRAME, 63, 2), e.encode_send(0)))
    assert straddle.error == ERR_WINDOW_VIOLATION
    last = run(words(e.encode_ld("r0", e.H_FRAME, 63, 1), e.encode_send(0)))
    assert last.verdict == VERDICT_SENT
    below = run(words(e.encode_st("r0", e.H_FRAME, -33, 1), e.encode_send(0)))
    assert below.error == ERR_WINDOW_VIOLATION


def test_st_edits_show_in_frame():
    packet = bytes(64)
    r = run(
        words(
            e.encode_movi("r0", 0xBEEF),
            e.encode_st("r0", e.H_FRAME, 10, 2),
            e.encode_send(0),
        ),
        packet=packet,
    )
    assert r.frame is not None
    assert r.frame[10:12] == b"\xbe\xef"
    assert r.frame[:10] == bytes(10) and r.frame[12:] == bytes(52)


# --- LOOKUP ------------------------------------------------------------------


def _l2_table():
    from nanuk.testkit.map_harness import Table

    return Table(
        key_width=48,
        action_width=8,
        entries={0x02DEADBEEF01: 0x4, 0x0A0B0C0D0E0F: 0x8},
    )


def test_lookup_hit_and_miss():
    prog = words(
        e.encode_ld("r0", e.H_FRAME, 0, 6),
        e.encode_lookup("r1", 0, "r0", 4),
        e.encode_stmd("r1", 1, 0),
        e.encode_send(0),
        e.encode_drop(),
    )
    hit = run(
        prog,
        packet=bytes.fromhex("02deadbeef01") + bytes(58),
        tables=[_l2_table()],
    )
    assert hit.verdict == VERDICT_SENT and hit.md[0] == 0x4
    assert hit.steps == 4
    miss = run(prog, packet=bytes(64), tables=[_l2_table()])
    assert miss.verdict == VERDICT_DROP
    assert miss.steps == 3  # LD, LOOKUP(miss->4), DROP
    assert miss.regs[1] == 0


def test_lookup_key_masking():
    # Garbage above 48 bits in the key register must not affect the match.
    prog = words(
        e.encode_ld("r0", e.H_FRAME, 0, 8),  # 8 bytes: 2 garbage + 6 DMAC
        e.encode_lookup("r1", 0, "r0", 4),
        e.encode_stmd("r1", 1, 0),
        e.encode_send(0),
        e.encode_drop(),
    )
    packet = bytes.fromhex("beef02deadbeef01") + bytes(56)
    r = run(prog, packet=packet, tables=[_l2_table()])
    assert r.verdict == VERDICT_SENT and r.md[0] == 0x4


def test_lookup_always_miss_tables():
    from nanuk.testkit.map_harness import Table

    prog = words(
        e.encode_lookup("r1", 1, "rz", 2),
        e.encode_drop(),                      # hit path (should not happen)
        e.encode_send(0),                     # miss target
    )
    empty_cfg = run(prog, tables=[_l2_table(), Table(48, 8, {})])
    assert empty_cfg.verdict == VERDICT_SENT
    unconfigured = run(prog, tables=[_l2_table()])
    assert unconfigured.verdict == VERDICT_SENT
    out_of_plane = run(
        words(
            e.encode_lookup("r1", 12, "rz", 2),
            e.encode_drop(),
            e.encode_send(0),
        ),
        tables=[_l2_table()],
    )
    assert out_of_plane.verdict == VERDICT_SENT


# --- CSUM --------------------------------------------------------------------

# The classic worked example: checksum 0xB861 over a zeroed-field header.
IPV4_HDR = bytes.fromhex("450000730000400040110000c0a80001c0a800c7")


def _ipv4_packet(ck: bytes = b"\x00\x00", ttl: int = 0x40) -> bytes:
    hdr = bytearray(IPV4_HDR)
    hdr[8] = ttl
    hdr[10:12] = ck
    return bytes(14) + bytes(hdr) + bytes(30)  # 14B fake eth + header + pad


def test_csum_golden():
    prog = words(
        e.encode_movi("r2", 20),
        e.encode_csum("r1", 2, 0, "r2"),
        e.encode_send(0),
    )
    r = run(prog, packet=_ipv4_packet(), pp=pp_h2(off=14))
    assert r.verdict == VERDICT_SENT
    assert r.regs[1] == 0xB861
    # No write-back: the frame's checksum field is untouched.
    assert r.frame is not None and r.frame[24:26] == b"\x00\x00"
    # The field is NOT skipped: garbage there changes the sum.
    stale = run(prog, packet=_ipv4_packet(ck=b"\xde\xad"), pp=pp_h2(off=14))
    assert stale.regs[1] != 0xB861


def test_csum_full_recompute_sequence():
    # The spec's canonical IPv4 recompute after a TTL decrement.
    prog = words(
        e.encode_ld("r0", 2, 8, 1),
        e.encode_addi("r0", "r0", -1),
        e.encode_st("r0", 2, 8, 1),
        e.encode_ld("r2", 2, 0, 1),
        e.encode_andi("r2", "r2", 0x000F),
        e.encode_shli("r2", "r2", 2),
        e.encode_st("rz", 2, 10, 2),
        e.encode_csum("r3", 2, 0, "r2"),
        e.encode_st("r3", 2, 10, 2),
        e.encode_send(0),
    )
    r = run(prog, packet=_ipv4_packet(ck=b"\xb8\x61"), pp=pp_h2(off=14))
    assert r.frame is not None
    assert r.frame[22] == 0x3F
    assert r.frame[24:26] == b"\xb9\x61"  # RFC 1624 predicts +0x0100


def test_csum_zero_and_odd_len():
    zero = run(
        words(e.encode_csum("r0", e.H_FRAME, 0, "rz"), e.encode_drop()),
        packet=bytes(range(64)),
    )
    assert zero.regs[0] == 0xFFFF
    # len 3 over 45 00 00: sum 0x4500 -> ~ = 0xBAFF (odd tail high-weighted).
    odd = run(
        words(
            e.encode_movi("r1", 3),
            e.encode_csum("r0", e.H_FRAME, 0, "r1"),
            e.encode_drop(),
        ),
        packet=b"\x45\x00\x00" + bytes(61),
    )
    assert odd.regs[0] == 0xBAFF


def test_csum_totality():
    prog = words(
        e.encode_movi("r2", 100),
        e.encode_csum("r1", 2, 0, "r2"),
        e.encode_send(0),
    )
    truncated = run(prog, packet=_ipv4_packet()[:40], pp=pp_h2(off=14))
    assert truncated.error == ERR_WINDOW_VIOLATION
    absent = run(
        words(e.encode_movi("r2", 20), e.encode_csum("r1", 2, 0, "r2"), e.encode_send(0)),
        packet=_ipv4_packet(),
        pp=pp_none(),
    )
    assert absent.error == ERR_HDR_ABSENT
