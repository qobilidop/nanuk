"""Generates the committed SIIT conformance vectors
(benchmarks/siit/vectors/*.json) from the reference translator
(sw/python/nanuk/testkit/siit_ref.py). See benchmarks/siit/README.md for the
four-leg test architecture and benchmarks/siit/audit.md for the RFC 7915
audit whose stable disposition IDs this module cites in each vector's `rfc`
field.

Run (from the repo root, in the devcontainer):
    cd sw/python && uv run --no-sync python ../../benchmarks/siit/gen_vectors.py

Deterministic by construction: every vector comes from a fixed input frame
run through `translate()`, no randomness, no timestamps, no wall-clock reads.
Each "sent" vector's expected output is asserted against `translate()` at
generation time (not just replayed later) so a hand-crafted input that
doesn't actually exercise what its name claims fails loudly here, not
silently in a committed file. `test_siit_vectors.py::test_regen_is_byte_
identical` is the drift tripwire: regenerating into a tmp dir and diffing
against the committed files must be a no-op.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from scapy.layers.inet import ICMP, IP, TCP, UDP
from scapy.layers.inet6 import ICMPv6EchoRequest, IPv6
from scapy.layers.l2 import ARP, Ether
from scapy.packet import Packet, Raw

from nanuk.testkit.siit_ref import VECTOR_GROUPS, translate
from nanuk.testkit.testkit import DMAC, DMAC2

VECTORS_DIR = Path(__file__).resolve().parent / "vectors"

# Addressing fixture: SRC4/DST4 (and their 6052 embeddings SRC6/DST6) are
# ordinary hosts translated via the RFC 6052 well-known prefix; EAMT4/EAMT6
# is DEMO_SIIT's one explicit-mapping pair (192.0.2.1 <-> 2001:db8:1::c001),
# which takes precedence over 6052 per RFC 7757. Kept disjoint so a vector
# can deliberately choose which addressing path it exercises.
SRC4, DST4 = "198.51.100.2", "203.0.113.7"
SRC6, DST6 = "64:ff9b::c633:6402", "64:ff9b::cb00:7107"
EAMT4, EAMT6 = "192.0.2.1", "2001:db8:1::c001"

UDP_PORTS = dict(sport=12345, dport=53)
TCP_PORTS = dict(sport=1111, dport=80, seq=1, ack=0, flags="S", window=8192)
ICMP_ECHO = dict(type=8, code=0, id=0x1234, seq=1)


def _eth(l3) -> Packet:
    return Ether(dst=DMAC, src=DMAC2) / l3


def _payload(n: int) -> bytes:
    """Deterministic n-byte body -- varies byte-for-byte so length changes
    also change the checksum being patched, not just the frame size."""
    return bytes(i & 0xFF for i in range(n))


def _ones_csum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def _vec(name: str, rfc: str, direction: str, frame, *, verdict: str, why: str = "") -> dict:
    """Build one vector dict, asserting the reference translator actually
    reaches the verdict (and, for drops, the reason) the caller intends --
    a generator-time check, so a malformed fixture fails the build instead
    of silently committing the wrong expectation."""
    in_bytes = bytes(frame)
    r = translate(in_bytes)
    assert r.verdict == verdict, f"{name}: expected verdict={verdict!r}, got {r.verdict!r} ({r.why!r})"
    if verdict == "drop":
        assert r.why == why, f"{name}: expected why={why!r}, got {r.why!r}"
        return {
            "name": name,
            "rfc": rfc,
            "dir": direction,
            "in": in_bytes.hex(),
            "verdict": "drop",
            "out": None,
            "why": why,
        }
    return {
        "name": name,
        "rfc": rfc,
        "dir": direction,
        "in": in_bytes.hex(),
        "verdict": "sent",
        "out": r.frame.hex(),
        "why": "",
    }


# ---------------------------------------------------------------------------
# The six protocol x direction matrices: payload length {0, 4, 25} x
# TTL/hop {64, 2} (ttl=2 exercises the near-boundary decrement without
# dropping -- ttl<=1 is the negative group's job). Addressing here is always
# 6052 (the EAMT hit is the edge group's job -- see audit.md's rationale for
# `7915-4.1-src`/`7915-4.1-dst`: "udp46 covers 6052 embed; edge covers EAMT
# hits").
# ---------------------------------------------------------------------------

_MATRIX_SPECS = [
    # (group, dir, l3 builder, l4 builder, default rfc, ttl-boundary rfc)
    ("udp46", "46", lambda ttl: IP(src=SRC4, dst=DST4, ttl=ttl), lambda: UDP(**UDP_PORTS),
     "7915-4.1-payloadlen", "7915-4.1-hoplimit"),
    ("udp64", "64", lambda ttl: IPv6(src=SRC6, dst=DST6, hlim=ttl), lambda: UDP(**UDP_PORTS),
     "7915-5.1-totallen", "7915-5.1-ttl"),
    ("tcp46", "46", lambda ttl: IP(src=SRC4, dst=DST4, ttl=ttl), lambda: TCP(**TCP_PORTS),
     "7915-4.5-csum-update", "7915-4.1-hoplimit"),
    ("tcp64", "64", lambda ttl: IPv6(src=SRC6, dst=DST6, hlim=ttl), lambda: TCP(**TCP_PORTS),
     "7915-5.5-csum-update", "7915-5.1-ttl"),
    ("icmp46", "46", lambda ttl: IP(src=SRC4, dst=DST4, ttl=ttl), lambda: ICMP(**ICMP_ECHO),
     "7915-4.2-checksum", "7915-4.1-hoplimit"),
    ("icmp64", "64", lambda ttl: IPv6(src=SRC6, dst=DST6, hlim=ttl),
     lambda: ICMPv6EchoRequest(id=0x1234, seq=1),
     "7915-5.2-checksum", "7915-5.1-ttl"),
]


def _matrix_groups() -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for group, direction, l3_fn, l4_fn, default_rfc, ttl_rfc in _MATRIX_SPECS:
        vectors = []
        for payload_len in (0, 4, 25):
            for ttl in (64, 2):
                rfc = ttl_rfc if ttl == 2 else default_rfc
                name = f"{group}_len{payload_len}_ttl{ttl}"
                frame = _eth(l3_fn(ttl) / l4_fn() / Raw(_payload(payload_len)))
                vectors.append(_vec(name, rfc, direction, frame, verdict="sent"))
        groups[group] = vectors
    return groups


# ---------------------------------------------------------------------------
# edge: addressing (EAMT hits, both directions and both address slots),
# options, non-zero TOS/TC, an odd payload length distinct from the len25
# case above, minimum-size frames (with Ethernet-minimum padding beyond the
# declared length -- proving it isn't leaked), and one >256B frame (the
# later RTL tail-passthrough test's fixture).
# ---------------------------------------------------------------------------


def _edge_group() -> list[dict]:
    v = []

    v.append(_vec(
        "edge_eamt_dst_46", "7757-eamt", "46",
        _eth(IP(src=SRC4, dst=EAMT4, ttl=64) / UDP(**UDP_PORTS) / Raw(_payload(4))),
        verdict="sent",
    ))
    v.append(_vec(
        "edge_eamt_dst_64", "7757-eamt", "64",
        _eth(IPv6(src=SRC6, dst=EAMT6, hlim=64) / UDP(**UDP_PORTS) / Raw(_payload(4))),
        verdict="sent",
    ))
    v.append(_vec(
        "edge_eamt_src_46", "7757-eamt", "46",
        _eth(IP(src=EAMT4, dst=DST4, ttl=64) / UDP(**UDP_PORTS) / Raw(_payload(4))),
        verdict="sent",
    ))
    v.append(_vec(
        "edge_eamt_src_64", "7757-eamt-low64", "64",
        _eth(IPv6(src=EAMT6, dst=DST6, hlim=64) / UDP(**UDP_PORTS) / Raw(_payload(4))),
        verdict="sent",
    ))

    v.append(_vec(
        "edge_ipv4_options_46", "7915-4.1-options", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64, options=b"\x01\x01\x01\x01")
             / UDP(**UDP_PORTS) / Raw(_payload(4))),
        verdict="sent",
    ))

    # IHL 11 (44B) / 12 (48B): the v4->v6 Ethernet relocation's new frame
    # start is h_frame + IHL - 40, which lands inside [8,12) of the source
    # MAC for exactly these two IHLs -- the one region where a load-after-
    # store bug would clobber src-MAC bytes before they're copied. Distinct,
    # non-repeating MAC bytes (no shared prefix, unlike the demo DMAC/DMAC2
    # pair) so any such corruption shows up byte-for-byte rather than
    # cancelling out against a look-alike value.
    edge_eth = Ether(dst="de:ad:be:ef:00:11", src="c0:ff:ee:12:34:56")
    v.append(_vec(
        "edge_ipv4_options_ihl11_46", "7915-4.1-options", "46",
        edge_eth / IP(src=SRC4, dst=DST4, ttl=64, options=b"\x01\x02\x03\x04" * 6)
        / UDP(**UDP_PORTS) / Raw(_payload(4)),
        verdict="sent",
    ))
    v.append(_vec(
        "edge_ipv4_options_ihl12_46", "7915-4.1-options", "46",
        edge_eth / IP(src=SRC4, dst=DST4, ttl=64, options=b"\x01\x02\x03\x04" * 7)
        / UDP(**UDP_PORTS) / Raw(_payload(4)),
        verdict="sent",
    ))

    v.append(_vec(
        "edge_tos_nonzero_46", "7915-4.1-tos", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64, tos=0xB8) / UDP(**UDP_PORTS) / Raw(_payload(4))),
        verdict="sent",
    ))
    v.append(_vec(
        "edge_tos_nonzero_64", "7915-5.1-tos", "64",
        _eth(IPv6(src=SRC6, dst=DST6, hlim=64, tc=0xB8) / UDP(**UDP_PORTS) / Raw(_payload(4))),
        verdict="sent",
    ))

    v.append(_vec(
        "edge_odd_payload_46", "7915-4.1-payloadlen", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64) / UDP(**UDP_PORTS) / Raw(_payload(1))),
        verdict="sent",
    ))
    v.append(_vec(
        "edge_odd_payload_64", "7915-5.1-totallen", "64",
        _eth(IPv6(src=SRC6, dst=DST6, hlim=64) / UDP(**UDP_PORTS) / Raw(_payload(1))),
        verdict="sent",
    ))

    min46 = bytes(_eth(IP(src=SRC4, dst=DST4, ttl=64) / UDP(**UDP_PORTS)))
    min46 += b"\x00" * max(0, 60 - len(min46))  # Ethernet-minimum-frame padding, not leaked
    v.append(_vec("edge_min_frame_46", "7915-4.1-payloadlen", "46", min46, verdict="sent"))

    min64 = bytes(_eth(IPv6(src=SRC6, dst=DST6, hlim=64) / UDP(**UDP_PORTS)))
    min64 += b"\x00" * max(0, 60 - len(min64))
    v.append(_vec("edge_min_frame_64", "7915-5.1-totallen", "64", min64, verdict="sent"))

    v.append(_vec(
        "edge_tail_passthrough_46", "7915-4.5-csum-update", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64) / UDP(**UDP_PORTS) / Raw(_payload(300))),
        verdict="sent",
    ))
    v.append(_vec(
        "edge_tail_passthrough_64", "7915-5.5-csum-update", "64",
        _eth(IPv6(src=SRC6, dst=DST6, hlim=64) / UDP(**UDP_PORTS) / Raw(_payload(300))),
        verdict="sent",
    ))

    return v


# ---------------------------------------------------------------------------
# negative: one vector per drop reason in siit_ref's ledger (12 distinct
# `why` strings), plus the ledger-order overlap cases where two drop
# conditions are both true on the same packet and the frozen order picks a
# winner (fragment beats unsupported_l4; fragment beats l4_truncated).
# ---------------------------------------------------------------------------


def _negative_group() -> list[dict]:
    v = []

    v.append(_vec("neg_runt", "7915-ledger-order", "46", bytes(10), verdict="drop", why="runt"))

    v.append(_vec(
        "neg_non_ip_ethertype", "7915-ledger-order", "46",
        Ether(dst=DMAC, src=DMAC2) / ARP(pdst="10.0.0.1"),
        verdict="drop", why="non_ip_ethertype",
    ))

    v4_short = bytes(_eth(IP(src=SRC4, dst=DST4, ttl=64) / UDP(**UDP_PORTS)))[: 14 + 15]
    v.append(_vec("neg_v4_truncated", "7915-ledger-order", "46", v4_short,
                  verdict="drop", why="v4_truncated"))

    v6_short = bytes(_eth(IPv6(src=SRC6, dst=DST6, hlim=64) / UDP(**UDP_PORTS)))[: 14 + 30]
    v.append(_vec("neg_v6_truncated", "7915-ledger-order", "64", v6_short,
                  verdict="drop", why="v6_truncated"))

    bad_csum = bytearray(bytes(
        _eth(IP(src=SRC4, dst=DST4, ttl=64) / UDP(**UDP_PORTS) / Raw(b"x"))
    ))
    bad_csum[14 + 10] ^= 0xFF  # corrupt the IPv4 header checksum field itself
    v.append(_vec("neg_v4_bad_header_checksum", "7915-ledger-order", "46", bytes(bad_csum),
                  verdict="drop", why="v4_bad_header_checksum"))

    v.append(_vec(
        "neg_v4_fragment", "7915-4.1-frag", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64, flags="MF") / UDP(**UDP_PORTS) / Raw(b"x")),
        verdict="drop", why="fragment",
    ))
    v.append(_vec(
        "neg_v6_fragment", "7915-5.1.1-fragment", "64",
        _eth(IPv6(src=SRC6, dst=DST6, hlim=64, nh=44) / Raw(b"x" * 8)),
        verdict="drop", why="fragment",
    ))

    udp_short = bytes(
        _eth(IP(src=SRC4, dst=DST4, ttl=64) / UDP(**UDP_PORTS) / Raw(b"payload"))
    )[: 14 + 20 + 4]  # only 4 of UDP's 8 header bytes present
    v.append(_vec("neg_v4_l4_truncated", "7915-ledger-order", "46", udp_short,
                  verdict="drop", why="l4_truncated"))

    udp6_short = bytearray(bytes(
        _eth(IPv6(src=SRC6, dst=DST6, hlim=64) / UDP(**UDP_PORTS) / Raw(b"payload"))
    )[: 14 + 40 + 4])
    struct.pack_into("!H", udp6_short, 14 + 4, 4)  # payload length matches what's present
    v.append(_vec("neg_v6_l4_truncated", "7915-ledger-order", "64", bytes(udp6_short),
                  verdict="drop", why="l4_truncated"))

    v.append(_vec(
        "neg_v4_zero_udp_checksum", "7915-4.5-udp-zero-csum", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64) / UDP(chksum=0, **UDP_PORTS) / Raw(b"x")),
        verdict="drop", why="zero_udp_checksum",
    ))

    v.append(_vec(
        "neg_v4_icmp_error", "7915-4.2-err-dest-unreach", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64) / ICMP(type=3, code=1) / Raw(b"x" * 8)),
        verdict="drop", why="icmp_error",
    ))
    v.append(_vec(
        "neg_v6_icmp_error", "7915-5.2-err-dest-unreach", "64",
        _eth(IPv6(src=SRC6, dst=DST6, hlim=64, nh=58) / Raw(bytes([1, 0, 0, 0, 0, 0, 0, 0]))),
        verdict="drop", why="icmp_error",
    ))

    v.append(_vec(
        "neg_v4_unsupported_l4", "7915-4.5-forward-all", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64, proto=47) / Raw(b"x" * 8)),
        verdict="drop", why="unsupported_l4",
    ))
    v.append(_vec(
        "neg_v6_unsupported_l4", "7915-5.5-forward-all", "64",
        _eth(IPv6(src=SRC6, dst=DST6, hlim=64, nh=47) / Raw(b"x" * 8)),
        verdict="drop", why="unsupported_l4",
    ))

    v.append(_vec(
        "neg_v4_ttl_expired", "7915-4.1-hoplimit", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=1) / UDP(**UDP_PORTS) / Raw(b"x")),
        verdict="drop", why="ttl_expired",
    ))
    v.append(_vec(
        "neg_v6_ttl_expired", "7915-5.1-ttl", "64",
        _eth(IPv6(src=SRC6, dst=DST6, hlim=1) / UDP(**UDP_PORTS) / Raw(b"x")),
        verdict="drop", why="ttl_expired",
    ))

    v.append(_vec(
        "neg_v6_untranslatable_address", "7915-5.1-untranslatable", "64",
        _eth(IPv6(src=SRC6, dst="2001:db8:9::1234", hlim=64) / UDP(**UDP_PORTS) / Raw(b"x")),
        verdict="drop", why="untranslatable_address",
    ))

    v.append(_vec(
        "neg_v4_fragment_beats_unsupported_l4", "7915-ledger-order", "46",
        _eth(IP(src=SRC4, dst=DST4, ttl=64, proto=47, flags="MF") / Raw(b"x" * 8)),
        verdict="drop", why="fragment",
    ))

    frag_trunc = bytearray(bytes(
        _eth(IP(src=SRC4, dst=DST4, ttl=64, flags="MF") / UDP(**UDP_PORTS) / Raw(b"x"))
    )[: 14 + 20 + 4])
    struct.pack_into("!H", frag_trunc, 14 + 2, 24)  # Total Length == 20 + 4
    struct.pack_into("!H", frag_trunc, 14 + 10, 0)
    struct.pack_into("!H", frag_trunc, 14 + 10, _ones_csum(bytes(frag_trunc[14:34])))
    v.append(_vec(
        "neg_v4_fragment_beats_l4_truncated", "7915-ledger-order", "46", bytes(frag_trunc),
        verdict="drop", why="fragment",
    ))

    return v


def build_groups() -> dict[str, list[dict]]:
    groups = _matrix_groups()
    groups["edge"] = _edge_group()
    groups["negative"] = _negative_group()
    assert set(groups) == set(VECTOR_GROUPS), "generator groups must match the frozen schema"
    return groups


def write_groups(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = build_groups()
    for group in VECTOR_GROUPS:
        vectors = sorted(groups[group], key=lambda vec: vec["name"])
        names = [vec["name"] for vec in vectors]
        assert len(names) == len(set(names)), f"{group}: duplicate vector names {names}"
        path = out_dir / f"{group}.json"
        path.write_text(json.dumps(vectors, indent=2) + "\n")


def main() -> None:
    write_groups(VECTORS_DIR)
    for group in VECTOR_GROUPS:
        n = len(json.loads((VECTORS_DIR / f"{group}.json").read_text()))
        print(f"wrote {VECTORS_DIR / f'{group}.json'} ({n} vectors)")


if __name__ == "__main__":
    main()
