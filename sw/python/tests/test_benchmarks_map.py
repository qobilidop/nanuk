"""Match-action expressiveness benchmarks (track `map`) against the golden model.

Covers the two benchmarks that had no program: T0 (table-free forward) and
E1 (fixed rewrite). The rest of the ladder is already exercised by the demo
examples -- E0 by drop_all, E2 by map_ttl, E4/E5 by nanukproto, T1 by
map_l2fwd -- and their tests live with those examples.

See docs/superpowers/specs/2026-07-13-benchmark-suite-design.md.
"""

import struct
from pathlib import Path

import pytest

from nanuk.isa import map_asm, pp_asm
from nanuk.testkit import map_harness
from nanuk.testkit.map_harness import Table

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"

VERDICT_SENT = 0
VERDICT_DROPPED = 1


def programs(name: str, map_file: str) -> tuple[bytes, bytes]:
    d = EXAMPLES / name
    return (
        pp_asm.assemble((d / "parse.asm").read_text()),
        map_asm.assemble((d / map_file).read_text()),
    )


def ones_csum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


# --------------------------------------------------------------------------
# T0 — table-free forwarding, and E5 (shrink head) via a mid-frame pop.
#
# The route is in the packet. This program performs no lookup at all, which is
# what makes it worth having: it proves "the table is the policy" is a choice,
# not a structural requirement.
# --------------------------------------------------------------------------

SR_MAC = b"\xaa\xbb\xcc\xdd\xee\x01" + b"\xaa\xbb\xcc\xdd\xee\x02"
SR_PAYLOAD = b"PAYLOAD-" * 3
ET_SRCROUTE = 0x1234
ET_IPV4 = 0x0800


def srcroute_frame(hops: list[tuple[int, int]]) -> bytes:
    """hops: (egress bitmap, bottom-of-stack)."""
    out = SR_MAC + struct.pack("!H", ET_SRCROUTE)
    for bitmap, bos in hops:
        out += struct.pack("!H", (bos << 15) | bitmap)
    return out + SR_PAYLOAD


def test_t0_route_comes_from_the_packet_not_a_table():
    """Three hops, three switches, zero tables. Each hop pops its own entry and
    the frame that leaves the last switch is byte-identical to a plain Ethernet
    frame carrying the payload -- the routing header is fully consumed."""
    pp, mp = programs("srcroute", "fwd.asm")
    frame = srcroute_frame([(0x4, 0), (0x8, 0), (0x2, 1)])

    egress_seen = []
    cur = frame
    for _ in range(3):
        _, r = map_harness.run_pipeline(pp, mp, cur, tables=[], md_in=(0,) * 8)
        assert r is not None
        assert r.verdict == VERDICT_SENT
        assert r.error == 0
        assert r.delta == -2, "each hop pops its own 2-byte entry"
        egress_seen.append(r.md[0])
        cur = r.frame

    assert egress_seen == [0x4, 0x8, 0x2], "each switch is steered by the packet"
    # The routing header is gone and the payload's EtherType is restored.
    assert cur == SR_MAC + struct.pack("!H", ET_IPV4) + SR_PAYLOAD


def test_t0_relocation_is_an_overlapping_copy():
    """The Ethernet header moves forward by 2 bytes over itself. A copy done in
    the wrong order mangles the source MAC -- this pins the order down."""
    pp, mp = programs("srcroute", "fwd.asm")
    _, r = map_harness.run_pipeline(
        pp, mp, srcroute_frame([(0x1, 1)]), tables=[], md_in=(0,) * 8
    )
    assert r.frame[0:6] == SR_MAC[0:6], "destination MAC survived the shift"
    assert r.frame[6:12] == SR_MAC[6:12], "source MAC survived the shift"


def test_t0_non_srcroute_traffic_is_dropped():
    pp, mp = programs("srcroute", "fwd.asm")
    plain = SR_MAC + struct.pack("!H", ET_IPV4) + SR_PAYLOAD
    r_pp, r_map = map_harness.run_pipeline(pp, mp, plain, tables=[], md_in=(0,) * 8)
    assert r_pp.verdict == 1, "the parser refuses it; MAP never runs"


# --------------------------------------------------------------------------
# E1 — fixed rewrite: the switch answers a ping itself.
# --------------------------------------------------------------------------

E_DST = b"\xaa\xbb\xcc\xdd\xee\xff"
E_SRC = b"\x00\x11\x22\x33\x44\x55"
E_SIP = bytes([10, 0, 0, 1])
E_DIP = bytes([10, 0, 0, 2])
ICMP_OFF = 14 + 20


def icmp(type_: int = 8, payload: bytes = b"ping-payload") -> bytes:
    body = struct.pack("!BBHHH", type_, 0, 0, 0x1234, 1) + payload
    return body[:2] + struct.pack("!H", ones_csum(body)) + body[4:]


def echo_frame(type_: int = 8) -> bytes:
    body = icmp(type_)
    hdr = bytearray(20)
    hdr[0] = 0x45
    struct.pack_into("!H", hdr, 2, 20 + len(body))
    hdr[8] = 64
    hdr[9] = 1  # ICMP
    hdr[12:16] = E_SIP
    hdr[16:20] = E_DIP
    struct.pack_into("!H", hdr, 10, ones_csum(bytes(hdr)))
    return E_DST + E_SRC + struct.pack("!H", ET_IPV4) + bytes(hdr) + body


def reflect_table() -> Table:
    """{ingress port -> egress bitmap}. The control plane owns this because MAP
    cannot compute 1 << ingress: `shli` takes an immediate, and there is no
    shift-by-register."""
    return Table(key_width=16, action_width=16, entries={p: 1 << p for p in range(8)})


@pytest.mark.parametrize("ingress", [0, 3, 7])
def test_e1_echo_reply_is_correct_on_the_wire(ingress):
    pp, mp = programs("icmp_echo", "reply.asm")
    _, r = map_harness.run_pipeline(
        pp, mp, echo_frame(), tables=[reflect_table()], md_in=(ingress,) + (0,) * 7
    )
    assert (r.verdict, r.error) == (VERDICT_SENT, 0)
    assert r.delta == 0, "a reply is the same length as the request"

    f = r.frame
    assert f[0:6] == E_SRC and f[6:12] == E_DST, "MACs swapped"
    assert f[26:30] == E_DIP and f[30:34] == E_SIP, "IPv4 addresses swapped"

    body = f[ICMP_OFF:]
    assert body[0] == 0, "ICMP type 8 (request) -> 0 (reply)"
    assert body[8:] == b"ping-payload", "payload untouched"

    # The checksums are the point of the benchmark, so verify them independently.
    assert ones_csum(body) == 0, "ICMP checksum, patched incrementally, is valid"
    assert ones_csum(f[14:34]) == 0, (
        "IPv4 header checksum still valid -- swapping two terms of a sum does "
        "not change it, and the program never recomputes it"
    )
    assert r.md[0] == 1 << ingress, "reflected out the ingress port, via the table"


def test_e1_only_answers_echo_requests():
    pp, mp = programs("icmp_echo", "reply.asm")
    _, r = map_harness.run_pipeline(
        pp, mp, echo_frame(type_=3), tables=[reflect_table()], md_in=(0,) * 8
    )
    assert r.verdict == VERDICT_DROPPED, "ICMP destination-unreachable is not ours"
    assert r.error == 0, "a decision, not an error"


def test_e1_end_around_carry_actually_fires():
    """The carry path is only taken for some checksum values, so exercise a
    payload that forces it. MAP is flagless: the carry is recovered by masking
    to 16 bits and asking whether anything was lost."""
    pp, mp = programs("icmp_echo", "reply.asm")
    hit_carry = False
    for n in range(1, 40):
        frame = echo_frame()
        # Vary the payload to sweep checksum values.
        body = icmp(payload=bytes([n]) * 12)
        frame = frame[:ICMP_OFF] + body
        _, r = map_harness.run_pipeline(
            pp, mp, frame, tables=[reflect_table()], md_in=(0,) * 8
        )
        out = r.frame[ICMP_OFF:]
        assert ones_csum(out) == 0, f"checksum invalid for payload byte {n}"
        old = struct.unpack("!H", body[2:4])[0]
        if old + 0x0800 > 0xFFFF:
            hit_carry = True
    assert hit_carry, "the sweep must include at least one end-around carry"
