"""MAP ISS unit tests: hdr-relative addressing, headroom, tables,
checksum, metadata window, SEND/DROP, every error code, and trace events.

The differential leg (vs the MAP C emulator over the demo programs)
lives in tests/golden/test_map_iss_differential.py.
"""

import struct
from dataclasses import dataclass, field

from nanuk.isa import map_encoding as e
from nanuk.isa.map_asm import assemble
from nanuk.isa.map_iss import (
    ERR_HDR_ABSENT,
    ERR_ILLEGAL,
    ERR_SEND_RANGE,
    ERR_STEP_BUDGET,
    ERR_WINDOW_VIOLATION,
    STEP_BUDGET,
    VERDICT_DROP,
    VERDICT_ERROR,
    VERDICT_SENT,
    run_map_iss,
)


@dataclass
class Pp:
    hdr_present: list[int]
    hdr_offset: list[int]


@dataclass
class Tbl:
    key_width: int
    action_width: int
    entries: dict = field(default_factory=dict)


def pp_eth_ip() -> Pp:
    present = [0] * 16
    offset = [0] * 16
    present[0] = 1  # eth at 0
    present[1] = 1
    offset[1] = 14  # "ip" base at 14 for addressing tests
    return Pp(present, offset)


def raw(*words: int) -> bytes:
    return b"".join(struct.pack(">I", w) for w in words)


PKT = bytes(range(64))
NO_MD = [0] * 8


def test_ld_st_roundtrip_and_write_event():
    src = "    ld r0, 0, 0, 6\n    st r0, 0, 6, 6\n    send 0\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.verdict == VERDICT_SENT
    assert r.frame[6:12] == PKT[0:6]
    assert r.trace[1].writes == ((32 + 6, PKT[0:6]),)
    assert r.trace[0].writes == ()


def test_h_frame_base():
    src = "    ld r0, h_frame, 0, 1\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.trace[0].regs[0] == PKT[0]


def test_headroom_store_and_positive_delta():
    src = (
        "    movi r0, 0xAABB\n"
        "    st r0, h_frame, -4, 2\n"
        "    send 4\n"
    )
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.verdict == VERDICT_SENT and r.delta == 4
    assert r.trace[1].writes == ((28, b"\xaa\xbb"),)
    assert r.frame[:2] == b"\xaa\xbb"
    assert len(r.frame) == 4 + len(PKT)
    assert r.frame[4:] == PKT


def test_negative_delta_strips():
    src = "    send -14\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.delta == -14
    assert r.frame == PKT[14:]


def test_hdr_absent():
    src = "    ld r0, 5, 0, 1\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_ABSENT)


def test_window_violations():
    r = run_map_iss(assemble("    ld r0, 0, 500, 1\n    drop\n"), PKT, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_WINDOW_VIOLATION)
    r = run_map_iss(
        assemble("    movi r0, 1\n    st r0, h_frame, -33, 1\n    drop\n"),
        PKT, pp_eth_ip(), [], NO_MD,
    )
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_WINDOW_VIOLATION)


def test_md_window_pass_through_and_rw():
    md_in = [1, 0, 0, 0xBEEF, 0, 0, 0, 0x7777]
    src = (
        "    ldmd r0, 3\n"
        "    movi r1, 0xC0DE\n"
        "    stmd r1, 1, 2\n"
        "    send 0\n"
    )
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], md_in)
    assert r.trace[0].regs[0] == 0xBEEF
    # Written slot updated; untouched slots pass through.
    assert r.md == (1, 0, 0xC0DE, 0xBEEF, 0, 0, 0, 0x7777)


def test_stmd_multi_unit_msb_first():
    src = "    movi r0, 0x1122\n    stmd r0, 2, 4\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.md[4:6] == (0x0000, 0x1122)  # high unit first


def test_md_slot_bounds_illegal():
    r = run_map_iss(assemble("    ldmd r0, 8\n    drop\n"), PKT, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_ILLEGAL)
    r = run_map_iss(assemble("    stmd r0, 4, 6\n    drop\n"), PKT, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_ILLEGAL)


def test_addi_sign_extension_and_wrap():
    src = "    movi r0, 0\n    addi r0, r0, -1\n    addi r1, r0, 1\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.trace[1].regs[0] == (1 << 64) - 1
    assert r.trace[2].regs[1] == 0


def test_andi_and_shli():
    src = (
        "    movi r0, 0xFF45\n"
        "    andi r1, r0, 0x000F\n"
        "    shli r2, r1, 2\n"
        "    drop\n"
    )
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.trace[1].regs[1] == 0x5
    assert r.trace[2].regs[2] == 0x14


def test_shli_truncates_at_64():
    src = "    movi r0, 1\n    shli r0, r0, 63\n    shli r0, r0, 1\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.trace[1].regs[0] == 1 << 63
    assert r.trace[2].regs[0] == 0


def test_lookup_hit_miss_and_event():
    tbl = Tbl(key_width=48, action_width=8, entries={0xAABBCCDDEE01: 0x4})
    src = (
        "    ld r0, h_frame, 0, 6\n"
        "    lookup r1, 0, r0, miss\n"
        "    stmd r1, 1, 0\n"
        "    send 0\n"
        "miss:\n"
        "    drop\n"
    )
    pkt = bytes.fromhex("aabbccddee01") + bytes(58)
    r = run_map_iss(assemble(src), pkt, pp_eth_ip(), [tbl], NO_MD)
    assert r.verdict == VERDICT_SENT and r.md[0] == 0x4
    assert r.trace[1].lookup == (0, 0xAABBCCDDEE01, True, 0x4)

    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [tbl], NO_MD)
    assert r.verdict == VERDICT_DROP  # miss branched
    assert r.trace[1].lookup[2] is False and r.trace[1].regs[1] == 0


def test_lookup_masks_stored_keys():
    tbl = Tbl(key_width=8, action_width=8, entries={0x1FF: 0x2})  # stored wide
    src = "    movi r0, 0xFF\n    lookup r1, 0, r0, miss\n    send 0\nmiss:\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [tbl], NO_MD)
    assert r.verdict == VERDICT_SENT  # 0x1FF masked to 0xFF matches


def test_lookup_unconfigured_table_misses():
    src = "    movi r0, 1\n    lookup r1, 3, r0, miss\n    drop\nmiss:\n    send 0\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.verdict == VERDICT_SENT


def _ref_checksum(data: bytes) -> int:
    """RFC 1071 over the byte range as-is (no field skipping)."""
    total = 0
    for i in range(0, len(data), 2):
        hi = data[i]
        lo = data[i + 1] if i + 1 < len(data) else 0
        total += (hi << 8) | lo
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return total ^ 0xFFFF


def test_csum_ipv4_recompute_sequence():
    ip = bytearray(20)
    ip[0] = 0x45
    ip[8] = 64  # ttl
    ip[9] = 17  # udp
    ip[12:16] = bytes([10, 0, 0, 1])
    ip[16:20] = bytes([10, 0, 0, 2])
    pkt = bytes(14) + bytes(ip) + bytes(30)
    src = (
        "    ld r2, 1, 0, 1\n"
        "    andi r2, r2, 0x000F\n"
        "    shli r2, r2, 2\n"
        "    st rz, 1, 10, 2\n"
        "    csum r3, 1, 0, r2\n"
        "    st r3, 1, 10, 2\n"
        "    send 0\n"
    )
    r = run_map_iss(assemble(src), pkt, pp_eth_ip(), [], NO_MD)
    want = _ref_checksum(bytes(ip))  # field already zero in the fixture
    assert r.frame[24:26] == bytes([want >> 8, want & 0xFF])


def test_csum_zero_len_and_odd_len():
    src = "    csum r0, h_frame, 0, rz\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.trace[0].regs[0] == 0xFFFF
    src = "    movi r1, 3\n    csum r0, h_frame, 0, r1\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], NO_MD)
    assert r.trace[1].regs[0] == _ref_checksum(PKT[:3])


def test_csum_range_violation():
    src = "    movi r1, 100\n    csum r0, 1, 0, r1\n    drop\n"
    pkt = PKT[:40]  # 14 + 100 > 40
    r = run_map_iss(assemble(src), pkt, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_WINDOW_VIOLATION)


def test_send_range():
    r = run_map_iss(assemble("    send 33\n"), PKT, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_SEND_RANGE)
    r = run_map_iss(assemble(f"    send -{len(PKT)}\n"), PKT, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_SEND_RANGE)
    r = run_map_iss(assemble(f"    send -{len(PKT) - 1}\n"), PKT, pp_eth_ip(), [], NO_MD)
    assert r.verdict == VERDICT_SENT


def test_drop_and_budget():
    r = run_map_iss(assemble("    drop\n"), PKT, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.frame) == (VERDICT_DROP, None)
    r = run_map_iss(assemble("start:\n    jmp start\n"), PKT, pp_eth_ip(), [], NO_MD)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_STEP_BUDGET)
    assert r.steps == STEP_BUDGET and len(r.trace) == STEP_BUDGET


def test_md_delivered_on_drop_and_error():
    md_in = [7, 0, 0, 0, 0, 0, 0, 0]
    r = run_map_iss(assemble("    drop\n"), PKT, pp_eth_ip(), [], md_in)
    assert r.md[0] == 7  # pass-through even without SEND
    r = run_map_iss(assemble("    ldmd r0, 9\n"), PKT, pp_eth_ip(), [], md_in)
    assert r.verdict == VERDICT_ERROR and r.md[0] == 7


def test_reserved_bits_and_bad_reg_are_illegal():
    r = run_map_iss(raw(e.encode_movi("r0", 1) | (1 << 22)), PKT, pp_eth_ip(), [], NO_MD)
    assert r.error == ERR_ILLEGAL
    word = (0x09 << 26) | (0 << 23) | (0 << 19) | (5 << 16)  # LOOKUP rs code 5
    r = run_map_iss(raw(word), PKT, pp_eth_ip(), [], NO_MD)
    assert r.error == ERR_ILLEGAL
    # Old register-carrying SEND (rs = r1) no longer decodes.
    r = run_map_iss(raw(0x2C82C000), PKT, pp_eth_ip(), [], NO_MD)
    assert r.error == ERR_ILLEGAL
