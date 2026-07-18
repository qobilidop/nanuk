"""RFC 7915 stateless IP/ICMP translation (SIIT) — the reference oracle for
the Nanuk SIIT demo. Every drop and every output byte here encodes a frozen
plan decision (EAMT-before-6052 addressing, ID=0/DF=1 on v6->v4, TTL-1,
RFC 1624 incremental checksum patch); later tasks generate committed vectors
by running inputs through `translate()` and diff the Nanuk program against
them, so this module *is* the spec, not a convenience wrapper around one.

Framing convention: any trailing bytes beyond the IP datagram (as bounded by
the IPv4 Total Length / IPv6 Payload Length field) are below this
translator's abstraction and pass through to the output verbatim, unchanged
and untranslated. The IP length field is what bounds the L4 slice used for
checksum arithmetic and header parsing -- that bounding is real and stays --
but bytes past it (e.g. Ethernet minimum-frame padding) are not part of the
IP datagram this translator speaks for. Three reasons: (1) L2 padding is a
link-layer concern -- Jool, the real-world precedent this demo tracks, is an
L3 kernel module and never sees it either; (2) real hardware MACs pad short
frames on transmit and strip padding on receive, so a correct L3 translator
sitting above that boundary should neither expect padding nor manufacture
its removal; (3) on the Nanuk zero-copy datapath, physical frame length is
not program-visible (reads past the frame end are terminal), so passing
trailing bytes through is free and stripping them is inexpressible.

Pure stdlib on purpose: dev-only (testkit), but the committed vectors this
generates must replay scapy-free, so nothing here reaches for scapy either
— that stays confined to the tests that build input frames.
"""

from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass, field
from pathlib import Path

ET_IPV4 = 0x0800
ET_IPV6 = 0x86DD

PROTO_ICMP = 1
PROTO_TCP = 6
PROTO_UDP = 17
PROTO_ICMPV6 = 58
NH_FRAGMENT = 44  # IPv6 fragment extension header

ICMP4_ECHO_REQUEST = 8
ICMP4_ECHO_REPLY = 0
ICMP6_ECHO_REQUEST = 128
ICMP6_ECHO_REPLY = 129

WKP = bytes.fromhex("0064ff9b000000000000000000")[:12]  # 64:ff9b::/96 (RFC 6052)

# The six legal RFC 6052 §2.2 prefix lengths. All are multiples of 8, so the
# IPv4 embedding is byte-aligned in every case -- which is what lets the
# embed/extract helpers below work byte-wise rather than bit-wise.
_LEGAL_PL6052 = frozenset({32, 40, 48, 56, 64, 96})


def _v4(dotted: str) -> bytes:
    return socket.inet_aton(dotted)


def _v6(colon: str) -> bytes:
    return socket.inet_pton(socket.AF_INET6, colon)


def _embed_6052(v4: bytes, pool6: bytes, prefix_len: int) -> bytes:
    """RFC 6052 §2.2: embed a 32-bit IPv4 address into a 128-bit
    IPv6-embedded address behind a pool6 prefix of one of the six legal
    lengths (32/40/48/56/64/96).

    The IPv4 octets straddle the reserved "u" octet (byte 8, bits 64-71),
    which MUST be zero. The split (v4 bytes before the u-octet, v4 bytes
    after it) per the §2.2 table:

        /32 -> 4 before, 0 after   (bytes 4-7)
        /40 -> 3 before, 1 after   (bytes 5-7, then byte 9)
        /48 -> 2 before, 2 after   (bytes 6-7, then bytes 9-10)
        /56 -> 1 before, 3 after   (byte 7,   then bytes 9-11)
        /64 -> 0 before, 4 after   (bytes 9-12)
        /96 -> all 4 in bytes 12-15 (no u-octet straddle)
    """
    pfx = prefix_len // 8
    if prefix_len == 96:
        return pool6[:12] + v4
    n_before = (64 - prefix_len) // 8  # v4 bytes sitting before the u-octet
    out = bytearray(16)
    out[:pfx] = pool6[:pfx]
    out[pfx:8] = v4[:n_before]
    # out[8] stays 0 -- the reserved u-octet (RFC 6052 §2.2)
    out[9 : 9 + (4 - n_before)] = v4[n_before:]
    return bytes(out)


def _extract_6052(v6: bytes, pool6: bytes, prefix_len: int) -> bytes | None:
    """Inverse of `_embed_6052`: pull the IPv4 address back out if `v6`
    carries the pool6 prefix, else None. The u-octet is not re-validated --
    extraction reads only the IPv4 bit positions (RFC 6052 §2.2 puts the
    zero constraint on embedding)."""
    pfx = prefix_len // 8
    if v6[:pfx] != pool6[:pfx]:
        return None
    if prefix_len == 96:
        return v6[12:16]
    n_before = (64 - prefix_len) // 8
    return v6[pfx:8] + v6[9 : 9 + (4 - n_before)]


def _prefix_match(addr: int, width: int, prefix: int, plen: int) -> bool:
    if plen == 0:
        return True
    shift = width - plen
    return (addr >> shift) == (prefix >> shift)


def _eam_reproject(
    addr: int, src_w: int, src_plen: int, dst_prefix: int, dst_w: int, dst_plen: int
) -> int:
    """RFC 7757 §3.3: strip the source prefix, prepend the destination
    prefix, then pad with trailing zeros (4->6, §3.3.1 step 5) or discard
    trailing bits (6->4, §3.3.2 step 5) to reach the destination width.
    `dst_prefix` is the full-width integer form of the destination prefix
    (its own suffix bits are ignored)."""
    suffix_len = src_w - src_plen
    suffix = addr & ((1 << suffix_len) - 1) if suffix_len else 0
    top = (dst_prefix >> (dst_w - dst_plen)) if dst_plen else 0
    result = top << (dst_w - dst_plen)
    shift = (dst_w - dst_plen) - suffix_len
    if shift >= 0:
        result |= suffix << shift
    else:
        result |= suffix >> (-shift)  # trailing bits don't fit -> discarded
    return result & ((1 << dst_w) - 1)


def _parse_eam(v4s: str, v6s: str) -> tuple[int, int, int, int]:
    """Parse one EAMT row `(v4, v6)` where each side is an address or a
    CIDR. A bare address is the exact-host case: v4 -> /32, v6 -> /128
    (so existing single-address configs are /32<->/128 pairs, unchanged)."""

    def one(s: str, af: int, host_len: int) -> tuple[int, int]:
        if "/" in s:
            addr, plen_s = s.split("/", 1)
            plen = int(plen_s)
        else:
            addr, plen = s, host_len
        return int.from_bytes(socket.inet_pton(af, addr), "big"), plen

    v4, p4 = one(v4s, socket.AF_INET, 32)
    v6, p6 = one(v6s, socket.AF_INET6, 128)
    return v4, p4, v6, p6


@dataclass(frozen=True)
class SiitConfig:
    """Addressing config, RFC 7757 precedence: EAMT longest-prefix-match
    first, else RFC 6052 pool6 embed/extract.

    `pool6` is the prefix's network address bytes and `pool6_len` its length
    (one of the six RFC 6052 §2.2 lengths); the default is the /96 Well-Known
    Prefix, so callers that pass neither get the original behavior. `eamt` is
    the control-plane-shaped form: each entry is `(v4, v6)`, either bare
    addresses (an exact /32<->/128 host pair, as before) or CIDRs (a general
    RFC 7757 prefix pair). `__post_init__` derives (a) `_eam`, the ordered
    entry table `translate()` uses for longest-prefix-match in both
    directions, and (b) the byte-keyed `eamt46`/`eamt64` dicts that the
    program table-plane builder (`testkit.siit_tables`) consumes -- populated
    only from *exact host* pairs, since the program plane implements
    EAMT-exact + /96 (general prefixes are the LPM/T3 trigger). Frozen so a
    shared config (DEMO_SIIT) can't be mutated out from under callers; the
    derived attributes still need object.__setattr__ to land."""

    pool6: bytes = WKP
    pool6_len: int = 96
    eamt: tuple[tuple[str, str], ...] = ()
    eamt46: dict[bytes, bytes] = field(init=False, default_factory=dict, repr=False)
    eamt64: dict[bytes, bytes] = field(init=False, default_factory=dict, repr=False)
    _eam: tuple[tuple[int, int, int, int], ...] = field(
        init=False, default_factory=tuple, repr=False
    )

    def __post_init__(self) -> None:
        if self.pool6_len not in _LEGAL_PL6052:
            raise ValueError(f"pool6_len {self.pool6_len} is not an RFC 6052 prefix length")
        eam: list[tuple[int, int, int, int]] = []
        eamt46: dict[bytes, bytes] = {}
        eamt64: dict[bytes, bytes] = {}
        for v4s, v6s in self.eamt:
            v4, p4, v6, p6 = _parse_eam(v4s, v6s)
            eam.append((v4, p4, v6, p6))
            if p4 == 32 and p6 == 128:
                # exact host pair -> expose in the byte-keyed dicts the
                # program-table builder consumes (general prefixes cannot be).
                v4b, v6b = v4.to_bytes(4, "big"), v6.to_bytes(16, "big")
                eamt46[v4b] = v6b
                eamt64[v6b] = v4b
        object.__setattr__(self, "_eam", tuple(eam))
        object.__setattr__(self, "eamt46", eamt46)
        object.__setattr__(self, "eamt64", eamt64)


DEMO_SIIT = SiitConfig(eamt=(("192.0.2.1", "2001:db8:1::c001"),))


@dataclass
class SiitResult:
    verdict: str  # "sent" | "drop"
    frame: bytes | None
    why: str  # ledger reason; "" when sent. Becomes a vector's `why` field.


def _fold(s: int) -> int:
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return s


def _sum16(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    return _fold(sum(struct.unpack("!%dH" % (len(data) // 2), data)))


def _patch(csum: int, old: bytes, new: bytes) -> int:
    # RFC 1624: HC' = ~fold(~HC + ~sum(old) + sum(new)). `old`/`new` need not
    # match in length -- e.g. ICMP goes from no pseudo-header (old = just the
    # type/code word) to one (new = type/code word + full v6 pseudo-header).
    return (~_fold((~csum & 0xFFFF) + (~_sum16(old) & 0xFFFF) + _sum16(new))) & 0xFFFF


def _addr46(v4b: bytes, cfg: SiitConfig) -> bytes:
    """v4->v6 mapping, RFC 7757 precedence: EAMT longest-prefix-match first,
    else RFC 6052 pool6 embedding."""
    v4 = int.from_bytes(v4b, "big")
    best: tuple[int, int, int, int] | None = None
    for e in cfg._eam:
        if _prefix_match(v4, 32, e[0], e[1]) and (best is None or e[1] > best[1]):
            best = e
    if best is not None:
        return _eam_reproject(v4, 32, best[1], best[2], 128, best[3]).to_bytes(16, "big")
    return _embed_6052(v4b, cfg.pool6, cfg.pool6_len)


def _addr64(v6b: bytes, cfg: SiitConfig) -> bytes | None:
    """v6->v4 mapping: EAMT longest-prefix-match first, else RFC 6052
    extraction; None (untranslatable) if neither matches."""
    v6 = int.from_bytes(v6b, "big")
    best: tuple[int, int, int, int] | None = None
    for e in cfg._eam:
        if _prefix_match(v6, 128, e[2], e[3]) and (best is None or e[3] > best[3]):
            best = e
    if best is not None:
        return _eam_reproject(v6, 128, best[3], best[0], 32, best[1]).to_bytes(4, "big")
    return _extract_6052(v6b, cfg.pool6, cfg.pool6_len)


def _drop(why: str) -> SiitResult:
    return SiitResult("drop", None, why)


def _icmp6_pseudo(src6: bytes, dst6: bytes, upper_len: int) -> bytes:
    return src6 + dst6 + struct.pack("!I", upper_len) + b"\x00\x00\x00" + bytes([PROTO_ICMPV6])


def _translate46(l3: bytes, cfg: SiitConfig) -> SiitResult:
    """v4 -> v6, RFC 7915 §4.1. Head grows 20B net; body is untouched apart
    from the L4 checksum patch and (for ICMP) the type-word rewrite.

    This is the ledger order (controller decision, outer-in; both
    directions use this identical sequence -- v6 has no analogue of (b),
    the v4 header checksum):
      (a) IP-level structural drops -- runt, IP header truncated, Total
          Length overrunning the physical frame
      (b) v4 header checksum
      (c) fragment
      (d) L4 truncation (UDP < 8B, TCP < 20B, ICMP < 4B)
      (e) v4 zero UDP checksum
      (f) ICMP non-echo
      (g) unsupported L4
      (h) TTL/hop <= 1
    (c) before (d)/(e): a non-initial fragment's payload is not an L4
    header at all, so ANY fragment reports "fragment", never a truncation
    or checksum verdict about bytes that aren't what they'd otherwise
    look like.
    """
    if len(l3) < 20:
        return _drop("v4_truncated")
    ihl = (l3[0] & 0x0F) * 4
    if ihl < 20 or len(l3) < ihl:
        return _drop("v4_truncated")
    total_len = struct.unpack("!H", l3[2:4])[0]
    if total_len > len(l3):
        return _drop("l4_truncated")  # header claims more than the frame carries
    if _sum16(l3[:ihl]) != 0xFFFF:
        return _drop("v4_bad_header_checksum")

    tos = l3[1]
    flags_frag = struct.unpack("!H", l3[6:8])[0]
    ttl = l3[8]
    proto = l3[9]
    src4, dst4 = l3[12:16], l3[16:20]
    l4 = l3[ihl:total_len]  # bound to Total Length -- for checksum arithmetic only
    trailer = l3[total_len:]  # below the IP datagram: sub-abstraction, passes through verbatim

    if flags_frag & 0x3FFF:  # MF (bit 13) or a nonzero 13-bit offset
        return _drop("fragment")
    if proto == PROTO_UDP:
        if len(l4) < 8:
            return _drop("l4_truncated")
        if l4[6:8] == b"\x00\x00":
            return _drop("zero_udp_checksum")
    elif proto == PROTO_TCP:
        if len(l4) < 20:
            return _drop("l4_truncated")
    elif proto == PROTO_ICMP:
        if len(l4) < 4:  # type/code/checksum -- the checksum patch reads body[2:4]
            return _drop("l4_truncated")
        if l4[0] not in (ICMP4_ECHO_REQUEST, ICMP4_ECHO_REPLY) or l4[1] != 0:
            return _drop("icmp_error")  # non-echo: error translation deferred
    else:
        return _drop("unsupported_l4")
    if ttl <= 1:
        return _drop("ttl_expired")

    new_nh = 58 if proto == PROTO_ICMP else proto
    src6 = _addr46(src4, cfg)
    dst6 = _addr46(dst4, cfg)
    payload_len = total_len - ihl

    v6 = bytearray(40)
    v6[0] = 0x60 | (tos >> 4)
    v6[1] = (tos & 0x0F) << 4  # flow label stays 0
    struct.pack_into("!H", v6, 4, payload_len)
    v6[6] = new_nh
    v6[7] = ttl - 1
    v6[8:24] = src6
    v6[24:40] = dst6

    body = bytearray(l4)
    if proto in (PROTO_UDP, PROTO_TCP):
        # Proto and upper-layer length contribute equally to both
        # pseudo-headers, so only the address words need patching.
        csum_off = 6 if proto == PROTO_UDP else 16
        old_csum = struct.unpack("!H", body[csum_off : csum_off + 2])[0]
        new_csum = _patch(old_csum, src4 + dst4, src6 + dst6)
        if proto == PROTO_UDP and new_csum == 0:
            # RFC 768/8200: IPv6 UDP checksums are mandatory, so a
            # computed-zero result must be sent as all-ones.
            new_csum = 0xFFFF
        struct.pack_into("!H", body, csum_off, new_csum)
    else:
        # v4 ICMP has no pseudo-header; v6 does. Patch the type word AND
        # add the whole v6 pseudo-header in one _patch call (old has no
        # pseudo-header term, so its absence there *is* "adding" it).
        new_type = ICMP6_ECHO_REQUEST if body[0] == ICMP4_ECHO_REQUEST else ICMP6_ECHO_REPLY
        old_word = bytes(body[0:2])
        body[0] = new_type
        new_word = bytes(body[0:2])
        pseudo = _icmp6_pseudo(src6, dst6, payload_len)
        old_csum = struct.unpack("!H", body[2:4])[0]
        new_csum = _patch(old_csum, old_word, new_word + pseudo)
        if new_csum == 0:  # same RFC 768 idiom applies to ICMPv6 (RFC 4443)
            new_csum = 0xFFFF
        struct.pack_into("!H", body, 2, new_csum)

    return SiitResult("sent", bytes(v6) + bytes(body) + bytes(trailer), "")


def _translate64(l3: bytes, cfg: SiitConfig) -> SiitResult:
    """v6 -> v4, RFC 7915 §5.1. Head shrinks 20B net; IPv4 header checksum
    is always computed fresh (there is nothing to patch it from).

    Same ledger order as _translate46 (see the comment there): (a)
    structural truncation, [no v6 analogue of (b)], (c) fragment, (d) L4
    truncation, [no v6 analogue of (e)], (f) ICMP non-echo, (g) unsupported
    L4, (h) TTL/hop.
    """
    if len(l3) < 40:
        return _drop("v6_truncated")
    payload_len = struct.unpack("!H", l3[4:6])[0]
    if len(l3) - 40 < payload_len:
        return _drop("l4_truncated")  # header claims more than the frame carries

    tc = ((l3[0] & 0x0F) << 4) | (l3[1] >> 4)
    nh = l3[6]
    hop_limit = l3[7]
    src6, dst6 = l3[8:24], l3[24:40]
    l4 = l3[40 : 40 + payload_len]
    trailer = l3[40 + payload_len :]  # below the IP datagram: sub-abstraction, passes through verbatim

    if nh == NH_FRAGMENT:
        return _drop("fragment")
    if nh == PROTO_UDP:
        if len(l4) < 8:
            return _drop("l4_truncated")
    elif nh == PROTO_TCP:
        if len(l4) < 20:
            return _drop("l4_truncated")
    elif nh == PROTO_ICMPV6:
        if len(l4) < 4:  # type/code/checksum -- the checksum patch reads body[2:4]
            return _drop("l4_truncated")
        if l4[0] not in (ICMP6_ECHO_REQUEST, ICMP6_ECHO_REPLY) or l4[1] != 0:
            return _drop("icmp_error")
    else:
        return _drop("unsupported_l4")
    if hop_limit <= 1:
        return _drop("ttl_expired")

    src4 = _addr64(src6, cfg)
    dst4 = _addr64(dst6, cfg)
    if src4 is None or dst4 is None:
        return _drop("untranslatable_address")

    new_proto = 1 if nh == PROTO_ICMPV6 else nh
    total_len = payload_len + 20

    v4 = bytearray(20)
    v4[0] = 0x45  # version 4, IHL 5 -- options never emitted
    v4[1] = tc
    struct.pack_into("!H", v4, 2, total_len)
    struct.pack_into("!H", v4, 6, 0x4000)  # id=0, DF=1, MF=0, offset=0
    v4[8] = hop_limit - 1
    v4[9] = new_proto
    v4[12:16] = src4
    v4[16:20] = dst4
    struct.pack_into("!H", v4, 10, (~_sum16(bytes(v4))) & 0xFFFF)

    body = bytearray(l4)
    if nh in (PROTO_UDP, PROTO_TCP):
        csum_off = 6 if nh == PROTO_UDP else 16
        old_csum = struct.unpack("!H", body[csum_off : csum_off + 2])[0]
        new_csum = _patch(old_csum, src6 + dst6, src4 + dst4)
        struct.pack_into("!H", body, csum_off, new_csum)
        # v4 UDP checksum 0 is legal (unlike the 46 direction, where it's
        # dropped on ingress) -- passed through patched, no special case.
    else:
        new_type = ICMP4_ECHO_REQUEST if body[0] == ICMP6_ECHO_REQUEST else ICMP4_ECHO_REPLY
        old_word = bytes(body[0:2])
        body[0] = new_type
        new_word = bytes(body[0:2])
        pseudo = _icmp6_pseudo(src6, dst6, payload_len)
        old_csum = struct.unpack("!H", body[2:4])[0]
        new_csum = _patch(old_csum, old_word + pseudo, new_word)
        struct.pack_into("!H", body, 2, new_csum)

    return SiitResult("sent", bytes(v4) + bytes(body) + bytes(trailer), "")


def translate(frame: bytes, cfg: SiitConfig = DEMO_SIIT) -> SiitResult:
    """The whole translator: dispatch on EtherType, run the ingress ledger,
    rewrite only the EtherType at L2 (MACs pass through -- L2 forwarding is
    the switch's job, not the translator's)."""
    if len(frame) < 14:
        return _drop("runt")
    dmac, smac = frame[0:6], frame[6:12]
    ethertype = struct.unpack("!H", frame[12:14])[0]
    l3 = frame[14:]

    if ethertype == ET_IPV4:
        r, new_et = _translate46(l3, cfg), ET_IPV6
    elif ethertype == ET_IPV6:
        r, new_et = _translate64(l3, cfg), ET_IPV4
    else:
        return _drop("non_ip_ethertype")

    if r.verdict == "drop":
        return r
    return SiitResult("sent", dmac + smac + struct.pack("!H", new_et) + r.frame, "")


# The eight committed vector groups, in the schema's fixed order (also the
# order `load_vectors(group=None)` concatenates them in). `gen_vectors.py`
# writes exactly these files; nothing else lives under vectors/.
VECTOR_GROUPS = (
    "udp46",
    "udp64",
    "tcp46",
    "tcp64",
    "icmp46",
    "icmp64",
    "edge",
    "negative",
)

# Anchored the same way as testkit/load.py's _EXAMPLES: this file lives at
# sw/python/nanuk/testkit/siit_ref.py, four parents up is the repo root.
_VECTORS_DIR = Path(__file__).resolve().parents[4] / "benchmarks" / "siit" / "vectors"


def load_vectors(group: str | None = None) -> list[dict]:
    """Load committed conformance vectors from benchmarks/siit/vectors/*.json
    (generated by benchmarks/siit/gen_vectors.py; plain JSON+hex, no scapy
    needed to replay). `group=None` concatenates all eight groups in
    `VECTOR_GROUPS` order; a group name loads just that one file."""
    names = VECTOR_GROUPS if group is None else (group,)
    vectors: list[dict] = []
    for name in names:
        with open(_VECTORS_DIR / f"{name}.json") as f:
            vectors.extend(json.load(f))
    return vectors
