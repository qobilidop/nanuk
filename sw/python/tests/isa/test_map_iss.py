"""MAP ISS unit tests: hdr-relative addressing, headroom, tables,
checksum, SEND/DROP, every error code, and trace events.

The differential leg (vs the MAP C emulator over the demo programs)
lives in tests/golden/test_iss_map_differential.py.
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
    smd: list[int]


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
    smd = [0] * 8
    smd[3] = 0xBEEF
    return Pp(present, offset, smd)


def raw(*words: int) -> bytes:
    return b"".join(struct.pack(">I", w) for w in words)


PKT = bytes(range(64))


def test_ld_st_roundtrip_and_write_event():
    src = "    ld r0, 0, 0, 6\n    st r0, 0, 6, 6\n    movi r1, 1\n    send r1, 0\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], 0)
    assert r.verdict == VERDICT_SENT
    assert r.frame[6:12] == PKT[0:6]
    assert r.trace[1].writes == ((32 + 6, PKT[0:6]),)
    assert r.trace[0].writes == ()


def test_h_frame_base():
    src = "    ld r0, h_frame, 0, 1\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], 0)
    assert r.trace[0].regs[0] == PKT[0]


def test_headroom_store_and_positive_delta():
    src = (
        "    movi r0, 0xAABB\n"
        "    st r0, h_frame, -4, 2\n"
        "    movi r1, 1\n"
        "    send r1, 4\n"
    )
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], 0)
    assert r.verdict == VERDICT_SENT and r.delta == 4
    assert r.trace[1].writes == ((28, b"\xaa\xbb"),)
    assert r.frame[:2] == b"\xaa\xbb"
    assert len(r.frame) == 4 + len(PKT)
    assert r.frame[4:] == PKT


def test_negative_delta_strips():
    src = "    movi r0, 1\n    send r0, -14\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], 0)
    assert r.delta == -14
    assert r.frame == PKT[14:]


def test_hdr_absent():
    src = "    ld r0, 5, 0, 1\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], 0)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_ABSENT)


def test_window_violations():
    r = run_map_iss(assemble("    ld r0, 0, 500, 1\n    drop\n"), PKT, pp_eth_ip(), [], 0)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_WINDOW_VIOLATION)
    r = run_map_iss(
        assemble("    movi r0, 1\n    st r0, h_frame, -33, 1\n    drop\n"),
        PKT, pp_eth_ip(), [], 0,
    )
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_WINDOW_VIOLATION)


def test_ldmd_fields():
    src = (
        "    ldmd r0, 3\n"    # smd slot 3
        "    ldmd r1, 8\n"    # ingress
        "    ldmd r2, 9\n"    # flood mask
        "    ldmd r3, 10\n"   # hdr_present bitmap
        "    drop\n"
    )
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], 1)
    assert r.trace[0].regs[0] == 0xBEEF
    assert r.trace[1].regs[1] == 1
    assert r.trace[2].regs[2] == 0b1101  # 4 ports minus ingress 1
    assert r.trace[3].regs[3] == 0b11  # hdr 0 and 1 present
    r = run_map_iss(assemble("    ldmd r0, 12\n    drop\n"), PKT, pp_eth_ip(), [], 0)
    assert r.trace[0].regs[0] == 0  # fields 11-15 defined zero


def test_addi_sign_extension_and_wrap():
    src = "    movi r0, 0\n    addi r0, r0, -1\n    addi r1, r0, 1\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], 0)
    assert r.trace[1].regs[0] == (1 << 64) - 1
    assert r.trace[2].regs[1] == 0


def test_lookup_hit_miss_and_event():
    tbl = Tbl(key_width=48, action_width=8, entries={0xAABBCCDDEE01: 0x4})
    src = (
        "    movi r0, 0xEE01\n"
        "    ld r0, h_frame, 0, 6\n"
        "    lookup r1, 0, r0, miss\n"
        "    send r1, 0\n"
        "miss:\n"
        "    drop\n"
    )
    pkt = bytes.fromhex("aabbccddee01") + bytes(58)
    r = run_map_iss(assemble(src), pkt, pp_eth_ip(), [tbl], 0)
    assert r.verdict == VERDICT_SENT and r.egress == 0x4
    assert r.trace[2].lookup == (0, 0xAABBCCDDEE01, True, 0x4)

    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [tbl], 0)
    assert r.verdict == VERDICT_DROP  # miss branched
    assert r.trace[2].lookup[2] is False and r.trace[2].regs[1] == 0


def test_lookup_masks_stored_keys():
    tbl = Tbl(key_width=8, action_width=8, entries={0x1FF: 0x2})  # stored wide
    src = "    movi r0, 0xFF\n    lookup r1, 0, r0, miss\n    send r1, 0\nmiss:\n    drop\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [tbl], 0)
    assert r.verdict == VERDICT_SENT  # 0x1FF masked to 0xFF matches


def test_lookup_unconfigured_table_misses():
    src = "    movi r0, 1\n    lookup r1, 3, r0, miss\n    drop\nmiss:\n    send r0, 0\n"
    r = run_map_iss(assemble(src), PKT, pp_eth_ip(), [], 0)
    assert r.verdict == VERDICT_SENT


def _ref_ipv4_checksum(hdr: bytes) -> int:
    total = 0
    for i in range(0, len(hdr), 2):
        word = (hdr[i] << 8) | hdr[i + 1]
        if i == 10:
            word = 0
        total += word
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return total ^ 0xFFFF


def test_csumupd():
    ip = bytearray(20)
    ip[0] = 0x45
    ip[8] = 64  # ttl
    ip[9] = 17  # udp
    ip[12:16] = bytes([10, 0, 0, 1])
    ip[16:20] = bytes([10, 0, 0, 2])
    pkt = bytes(14) + bytes(ip) + bytes(30)
    pp = pp_eth_ip()
    r = run_map_iss(assemble("    csumupd 1, 0\n    movi r0, 1\n    send r0, 0\n"), pkt, pp, [], 0)
    want = _ref_ipv4_checksum(bytes(ip))
    assert r.frame[24:26] == bytes([want >> 8, want & 0xFF])
    assert r.trace[0].writes == ((32 + 14 + 10, bytes([want >> 8, want & 0xFF])),)


def test_csumupd_bad_ihl():
    pkt = bytes(14) + b"\x41" + bytes(49)  # IHL 1
    r = run_map_iss(assemble("    csumupd 1, 0\n    drop\n"), pkt, pp_eth_ip(), [], 0)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_WINDOW_VIOLATION)


def test_send_range():
    r = run_map_iss(assemble("    movi r0, 1\n    send r0, 33\n"), PKT, pp_eth_ip(), [], 0)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_SEND_RANGE)
    r = run_map_iss(assemble(f"    movi r0, 1\n    send r0, -{len(PKT)}\n"), PKT, pp_eth_ip(), [], 0)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_SEND_RANGE)
    r = run_map_iss(assemble(f"    movi r0, 1\n    send r0, -{len(PKT) - 1}\n"), PKT, pp_eth_ip(), [], 0)
    assert r.verdict == VERDICT_SENT


def test_send_masks_egress():
    r = run_map_iss(assemble("    movi r0, 0xFF\n    send r0, 0\n"), PKT, pp_eth_ip(), [], 0)
    assert r.egress == 0xF


def test_drop_and_budget():
    r = run_map_iss(assemble("    drop\n"), PKT, pp_eth_ip(), [], 0)
    assert (r.verdict, r.frame) == (VERDICT_DROP, None)
    r = run_map_iss(assemble("start:\n    jmp start\n"), PKT, pp_eth_ip(), [], 0)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_STEP_BUDGET)
    assert r.steps == STEP_BUDGET and len(r.trace) == STEP_BUDGET


def test_reserved_bits_and_bad_reg_are_illegal():
    r = run_map_iss(raw(e.encode_movi("r0", 1) | (1 << 22)), PKT, pp_eth_ip(), [], 0)
    assert r.error == ERR_ILLEGAL
    word = (0x09 << 26) | (0 << 23) | (0 << 19) | (5 << 16)  # LOOKUP rs code 5
    r = run_map_iss(raw(word), PKT, pp_eth_ip(), [], 0)
    assert r.error == ERR_ILLEGAL
