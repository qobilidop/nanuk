"""Parser expressiveness benchmarks (track `pp`) against the golden model.

Each test is one benchmark on the ladder: a program, the single capability it
forces, and acceptance vectors. The ladder's claim is that PP ISA v0 parses
every graph in Gibb et al., ANCS 2013 (Fig. 3) and forces no new instruction.
These tests are the evidence for that claim.

See docs/superpowers/specs/2026-07-13-benchmark-suite-design.md.
"""

import struct
from pathlib import Path

import pytest

from nanuk.isa import pp_asm
from nanuk.testkit import pp_harness

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"

VERDICT_ACCEPT = 0
VERDICT_DROP = 1

STEP_BUDGET = 256
IMEM_WORDS = 1024

MAC = b"\xaa\xbb\xcc\xdd\xee\x01" + b"\xaa\xbb\xcc\xdd\xee\x02"


def prog(name: str) -> bytes:
    return pp_asm.assemble((EXAMPLES / name / "parse.asm").read_text())


def eth(ethertype: int) -> bytes:
    return MAC + struct.pack("!H", ethertype)


def tags(n: int, inner: int) -> bytes:
    """Ethernet with n VLAN tags, innermost carrying `inner`."""
    out = MAC + struct.pack("!H", 0x8100)
    for i in range(n):
        nxt = 0x8100 if i < n - 1 else inner
        out += struct.pack("!HH", 100 + i, nxt)
    return out


def mpls(label: int, bos: int, ttl: int = 64) -> bytes:
    return struct.pack("!I", (label << 12) | (bos << 8) | ttl)


def ipv4(proto: int, ihl: int = 5) -> bytes:
    b = bytearray(ihl * 4)
    b[0] = 0x40 | ihl
    b[9] = proto
    return bytes(b)


def ipv6(next_header: int) -> bytes:
    b = bytearray(40)
    b[0] = 0x60
    b[6] = next_header
    return bytes(b)


def ah(next_header: int, paylen: int = 4) -> bytes:
    """IPsec AH; total length is (paylen + 2) * 4 bytes."""
    return bytes([next_header, paylen]) + b"\x00" * ((paylen + 2) * 4 - 2)


def gre(proto: int, key: int | None = None) -> bytes:
    b = struct.pack("!HH", 0x2000 if key is not None else 0, proto)
    if key is not None:
        b += struct.pack("!I", key << 8)
    return b


def udp(dport: int) -> bytes:
    return struct.pack("!HHHH", 1234, dport, 0, 0)


def vxlan(vni: int) -> bytes:
    return struct.pack("!I", 0x08000000) + struct.pack("!I", vni << 8)


def tcp(doff: int = 5) -> bytes:
    b = bytearray(doff * 4)
    b[12] = doff << 4
    return bytes(b)


# --------------------------------------------------------------------------
# P5 — incomplete information (lookahead).
#
# MPLS names no successor: `bos` says the label stack ended, not what follows.
# The type is the nibble PAST the label, read with a non-consuming EXT.
# --------------------------------------------------------------------------

MPLS_SP = "mpls_sp"
H_ETH, H_MPLS, H_IPV4, H_IPV6, H_ETH2 = range(5)


@pytest.mark.parametrize(
    "name,packet,verdict,tag,hdrs",
    [
        (
            "1 label -> IPv4",
            eth(0x8847) + mpls(100, 1) + ipv4(6),
            VERDICT_ACCEPT,
            4,
            {H_ETH: 0, H_MPLS: 14, H_IPV4: 18},
        ),
        (
            "3 labels -> IPv6",
            eth(0x8847) + mpls(1, 0) + mpls(2, 0) + mpls(3, 1) + ipv6(6),
            VERDICT_ACCEPT,
            6,
            {H_ETH: 0, H_MPLS: 22, H_IPV6: 26},
        ),
        (
            "5 labels -> IPv4 (at the bound)",
            eth(0x8847) + b"".join(mpls(i, 0) for i in range(4)) + mpls(9, 1) + ipv4(6),
            VERDICT_ACCEPT,
            4,
            {H_ETH: 0, H_MPLS: 30, H_IPV4: 34},
        ),
        (
            "2 labels -> EoMPLS -> inner Ethernet",
            eth(0x8847) + mpls(1, 0) + mpls(2, 1) + b"\x00" * 4 + eth(0x0800) + ipv4(6),
            VERDICT_ACCEPT,
            0,
            {H_ETH: 0, H_MPLS: 18, H_ETH2: 26},
        ),
        (
            "IPv4 options (IHL=8): computed advance",
            eth(0x8847) + mpls(7, 1) + ipv4(6, ihl=8) + b"tcp",
            VERDICT_ACCEPT,
            4,
            {H_ETH: 0, H_MPLS: 14, H_IPV4: 18},
        ),
    ],
)
def test_p5_lookahead(name, packet, verdict, tag, hdrs):
    r = pp_harness.run_program(prog(MPLS_SP), packet)
    assert r.verdict == verdict, name
    assert r.error == 0, name
    assert r.md[3] == tag, f"{name}: payload tag from the lookahead nibble"
    for hid, off in hdrs.items():
        assert r.hdr_present[hid], f"{name}: header {hid} missing"
        assert r.hdr_offset[hid] == off, f"{name}: header {hid} offset"


def test_p5_refuses_a_sixth_label():
    """The one-hot counter bounds the stack in the program text, not merely by
    the step budget. A sixth label is refused, not run out of budget."""
    packet = eth(0x8847) + b"".join(mpls(i, 0) for i in range(5)) + mpls(9, 1) + ipv4(6)
    r = pp_harness.run_program(prog(MPLS_SP), packet)
    assert r.verdict == VERDICT_DROP
    assert r.error == 0, "a refusal, not a budget exhaustion or an error"
    assert r.steps < STEP_BUDGET


def test_p5_non_mpls_is_dropped():
    r = pp_harness.run_program(prog(MPLS_SP), eth(0x0800) + ipv4(6))
    assert r.verdict == VERDICT_DROP
    assert r.error == 0


# --------------------------------------------------------------------------
# P6 — nesting: the same header type twice.
# --------------------------------------------------------------------------

OVERLAY = "overlay_dc"
O_ETH, O_VLAN, O_IPV4, O_UDP, O_VXLAN, O_GRE, O_ETH2, O_IPV4_2 = range(8)


def test_p6_vxlan_holds_two_ethernets_and_two_ipv4s():
    packet = (
        eth(0x0800) + ipv4(17) + udp(4789) + vxlan(0xABCD) + eth(0x0800) + ipv4(6)
    )
    r = pp_harness.run_program(prog(OVERLAY), packet)
    assert (r.verdict, r.error) == (VERDICT_ACCEPT, 0)
    for hid in (O_ETH, O_IPV4, O_UDP, O_VXLAN, O_ETH2, O_IPV4_2):
        assert r.hdr_present[hid], f"header {hid} missing"
    # Outer and inner cannot share a slot: distinct bases, both live.
    assert r.hdr_offset[O_ETH] == 0
    assert r.hdr_offset[O_ETH2] == 50
    assert r.hdr_offset[O_IPV4] == 14
    assert r.hdr_offset[O_IPV4_2] == 64
    assert r.md[1] == 0xABCD, "VNI"
    assert r.md[3] == 1, "inner IPv4 seen"


def test_p6_nvgre_carries_the_vsid_like_vxlan_carries_the_vni():
    packet = eth(0x0800) + ipv4(47) + gre(0x6558, key=0xABCDE0) + eth(0x0800) + ipv4(6)
    r = pp_harness.run_program(prog(OVERLAY), packet)
    assert (r.verdict, r.error) == (VERDICT_ACCEPT, 0)
    assert r.hdr_present[O_GRE] and r.hdr_present[O_ETH2] and r.hdr_present[O_IPV4_2]
    assert r.md[1] == 0xABCDE0 & 0xFFFF, "VSID, low 16b"


@pytest.mark.parametrize(
    "name,packet,inner",
    [
        ("plain IPv4, no overlay", eth(0x0800) + ipv4(6), False),
        ("plain UDP, not VXLAN", eth(0x0800) + ipv4(17) + udp(53), False),
        ("QinQ + VXLAN", tags(2, 0x0800) + ipv4(17) + udp(4789) + vxlan(1) + eth(0x0800) + ipv4(6), True),
        (
            "outer IPv4 options (IHL=7) + VXLAN",
            eth(0x0800) + ipv4(17, ihl=7) + udp(4789) + vxlan(9) + eth(0x0800) + ipv4(6),
            True,
        ),
    ],
)
def test_p6_overlay_variants(name, packet, inner):
    r = pp_harness.run_program(prog(OVERLAY), packet)
    assert (r.verdict, r.error) == (VERDICT_ACCEPT, 0), name
    assert bool(r.md[3]) == inner, name


@pytest.mark.parametrize(
    "name,flags",
    [
        ("checksum-present GRE is out of scope", 0x8000),  # C bit
        ("TEB without a key is not NVGRE", 0x0000),  # K bit clear
    ],
)
def test_p6_gre_refusals_are_total(name, flags):
    """Both refusals must be *decisions*, not window violations. A parser that
    ran off the end of a short header would report an error instead."""
    packet = (
        eth(0x0800)
        + ipv4(47)
        + struct.pack("!HH", flags, 0x6558)
        + b"\x00" * 4
        + eth(0x0800)
    )
    r = pp_harness.run_program(prog(OVERLAY), packet)
    assert r.verdict == VERDICT_DROP, name
    assert r.error == 0, f"{name}: refused, not errored"


# --------------------------------------------------------------------------
# P7 — scale: Gibb's big-union graph (21 header types, 16 header slots).
#
# Forces no new instruction. Measures capacity: imem, step budget, and the
# header-id squeeze that makes aliasing mandatory.
# --------------------------------------------------------------------------

UNION = "union"
U_ETH, U_VLAN, U_MPLS, U_IPV4, U_IPV6, U_ARP, U_L4, U_IPSEC = range(8)
U_GRE, U_VXLAN, U_EOMPLS, U_ETH2, U_IPV4_2, U_IPV6_2, U_L4_2 = range(8, 15)

WORST_PATH = (
    tags(2, 0x8847)
    + mpls(1, 0) + mpls(2, 0) + mpls(3, 0) + mpls(4, 0) + mpls(5, 1)
    + ipv4(51)                    # AH
    + ah(47)                      # -> GRE
    + gre(0x6558, key=0xABCDE0)   # NVGRE
    + eth(0x0800) + ipv4(6) + tcp()
)


@pytest.mark.parametrize(
    "name,packet,verdict,live",
    [
        ("eth/ipv4/tcp", eth(0x0800) + ipv4(6) + tcp(), VERDICT_ACCEPT, {U_ETH, U_IPV4, U_L4}),
        ("eth/ipv6/icmpv6", eth(0x86DD) + ipv6(58) + b"\x80" * 8, VERDICT_ACCEPT, {U_ETH, U_IPV6, U_L4}),
        ("eth/arp", eth(0x0806) + b"\x00" * 28, VERDICT_ACCEPT, {U_ETH, U_ARP}),
        ("qinq/ipv4/udp", tags(2, 0x0800) + ipv4(17) + udp(53), VERDICT_ACCEPT, {U_ETH, U_VLAN, U_IPV4, U_L4}),
        (
            "mpls x3/ipv6/sctp",
            eth(0x8847) + mpls(1, 0) + mpls(2, 0) + mpls(3, 1) + ipv6(132) + b"\x00" * 12,
            VERDICT_ACCEPT,
            {U_ETH, U_MPLS, U_IPV6, U_L4},
        ),
        (
            "eompls -> inner eth/ipv4/tcp",
            eth(0x8847) + mpls(9, 1) + b"\x00" * 4 + eth(0x0800) + ipv4(6) + tcp(),
            VERDICT_ACCEPT,
            {U_ETH, U_MPLS, U_EOMPLS, U_ETH2, U_IPV4_2, U_L4_2},
        ),
        (
            "vxlan -> inner ipv4/tcp",
            eth(0x0800) + ipv4(17) + udp(4789) + vxlan(0x123456) + eth(0x0800) + ipv4(6) + tcp(),
            VERDICT_ACCEPT,
            {U_ETH, U_IPV4, U_L4, U_VXLAN, U_ETH2, U_IPV4_2, U_L4_2},
        ),
        (
            "ipv4/esp is terminal",
            eth(0x0800) + ipv4(50) + b"\x00" * 8,
            VERDICT_ACCEPT,
            {U_ETH, U_IPV4, U_IPSEC},
        ),
        (
            "ipv4/ah names its successor",
            eth(0x0800) + ipv4(51) + ah(6) + tcp(),
            VERDICT_ACCEPT,
            {U_ETH, U_IPV4, U_IPSEC, U_L4},
        ),
        (
            "gre-encapsulated ipv4",
            eth(0x0800) + ipv4(47) + gre(0x0800) + ipv4(6) + tcp(),
            VERDICT_ACCEPT,
            {U_ETH, U_IPV4, U_GRE, U_L4},
        ),
        (
            "ipv4 options + tcp options",
            eth(0x0800) + ipv4(6, ihl=9) + tcp(doff=8),
            VERDICT_ACCEPT,
            {U_ETH, U_IPV4, U_L4},
        ),
        ("3 vlan tags: refused", tags(3, 0x0800) + ipv4(6) + tcp(), VERDICT_DROP, {U_ETH, U_VLAN}),
        (
            "6 mpls labels: refused",
            eth(0x8847) + b"".join(mpls(i, 0) for i in range(5)) + mpls(9, 1) + ipv4(6),
            VERDICT_DROP,
            {U_ETH, U_MPLS},
        ),
    ],
)
def test_p7_union_paths(name, packet, verdict, live):
    r = pp_harness.run_program(prog(UNION), packet)
    assert r.verdict == verdict, name
    assert r.error == 0, name
    seen = {i for i, p in enumerate(r.hdr_present) if p}
    assert seen == live, name


def test_p7_worst_path_fits_the_machine():
    """14 header instances: QinQ -> 5x MPLS -> IPv4 -> AH -> NVGRE -> inner
    Ethernet -> inner IPv4 -> inner TCP. The union's worst case is the whole
    point of the benchmark: it is where the PP's sizes either hold or don't."""
    r = pp_harness.run_program(prog(UNION), WORST_PATH)
    assert (r.verdict, r.error) == (VERDICT_ACCEPT, 0)
    seen = {i for i, p in enumerate(r.hdr_present) if p}
    assert seen == {U_ETH, U_VLAN, U_MPLS, U_IPV4, U_IPSEC, U_GRE, U_ETH2, U_IPV4_2, U_L4_2}
    assert r.md[2] == 2, "tunnel kind = nvgre"
    assert r.md[4] == 0xABCDE0 & 0xFFFF, "VSID"
    # Capacity, which is what P7 exists to measure.
    assert r.steps < STEP_BUDGET, f"worst path {r.steps} steps vs budget {STEP_BUDGET}"
    assert r.steps == 155, "regression canary: the worst path costs 155 steps (61%)"


def test_p7_program_fits_imem():
    words = len(prog(UNION)) // 4
    assert words < IMEM_WORDS
    assert words == 157, "regression canary: the union program is 157 words (15%)"
