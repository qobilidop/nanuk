"""Known-answer tests for the SIIT reference translator (RFC 7915).

Each "sent" test hand-assembles the expected output frame from bytes --
never by calling back into siit_ref's own helpers -- so a bug shared between
production code and test would not cancel out. scapy builds only the INPUT
frames (and computes their input-side checksums); the checksum pattern
mirrors `ones_csum` from tests/test_benchmarks_map.py.
"""

import socket
import struct

from scapy.layers.inet import ICMP, IP, TCP, UDP
from scapy.layers.inet6 import ICMPv6EchoRequest, IPv6
from scapy.layers.l2 import ARP, Ether
from scapy.packet import Raw

from nanuk.testkit.siit_ref import WKP, translate

MAC1 = "aa:bb:cc:dd:ee:01"
MAC2 = "aa:bb:cc:dd:ee:02"
MAC1_B = bytes.fromhex("aabbccddee01")
MAC2_B = bytes.fromhex("aabbccddee02")
ET_IPV4 = 0x0800
ET_IPV6 = 0x86DD


def ones_csum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def v6_header(tos: int, payload_len: int, nh: int, hop_limit: int, src6: bytes, dst6: bytes) -> bytes:
    hdr = bytearray(40)
    hdr[0] = 0x60 | (tos >> 4)
    hdr[1] = (tos & 0x0F) << 4  # flow label stays 0
    struct.pack_into("!H", hdr, 4, payload_len)
    hdr[6] = nh
    hdr[7] = hop_limit
    hdr[8:24] = src6
    hdr[24:40] = dst6
    return bytes(hdr)


def v4_header(tos: int, total_len: int, ttl: int, proto: int, src4: bytes, dst4: bytes) -> bytes:
    hdr = bytearray(20)
    hdr[0] = 0x45
    hdr[1] = tos
    struct.pack_into("!H", hdr, 2, total_len)
    struct.pack_into("!H", hdr, 6, 0x4000)  # id=0, DF=1, MF=0, offset=0
    hdr[8] = ttl
    hdr[9] = proto
    hdr[12:16] = src4
    hdr[16:20] = dst4
    struct.pack_into("!H", hdr, 10, ones_csum(bytes(hdr)))
    return bytes(hdr)


def v6_pseudo(src6: bytes, dst6: bytes, upper_len: int, nh: int) -> bytes:
    return src6 + dst6 + struct.pack("!I", upper_len) + b"\x00\x00\x00" + bytes([nh])


def v4_pseudo(src4: bytes, dst4: bytes, upper_len: int, proto: int) -> bytes:
    return src4 + dst4 + bytes([0, proto]) + struct.pack("!H", upper_len)


# --------------------------------------------------------------------------
# Sent: the address/header/checksum math, both directions.
# --------------------------------------------------------------------------


def test_udp46_6052():
    src4 = socket.inet_aton("198.51.100.2")
    dst4 = socket.inet_aton("192.0.2.33")
    payload = b"hello-udp46"
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(src="198.51.100.2", dst="192.0.2.33", ttl=64, tos=0x88)
        / UDP(sport=12345, dport=53)
        / Raw(payload)
    )
    r = translate(bytes(pkt))
    assert r.verdict == "sent"

    src6, dst6 = WKP + src4, WKP + dst4
    assert src6 == bytes.fromhex("0064ff9b0000000000000000c6336402")
    assert dst6 == bytes.fromhex("0064ff9b0000000000000000c0000221")
    udp_len = 8 + len(payload)
    udp = struct.pack("!HHHH", 12345, 53, udp_len, 0) + payload
    csum = ones_csum(v6_pseudo(src6, dst6, udp_len, 17) + udp)
    udp = udp[:6] + struct.pack("!H", csum) + udp[8:]
    expected = (
        MAC1_B
        + MAC2_B
        + struct.pack("!H", ET_IPV6)
        + v6_header(0x88, udp_len, 17, 63, src6, dst6)
        + udp
    )
    assert r.frame == expected, "full output frame, byte for byte"

    v6 = r.frame[14:54]
    assert struct.unpack("!H", r.frame[12:14])[0] == ET_IPV6
    assert ((v6[0] & 0x0F) << 4) | (v6[1] >> 4) == 0x88, "traffic class == TOS"
    assert struct.unpack("!H", v6[4:6])[0] == udp_len, "payload length"
    assert v6[7] == 63, "hop limit == TTL - 1"
    assert v6[8:24] == src6 and v6[24:40] == dst6, "6052-embedded addresses"

    body = r.frame[54:]
    assert ones_csum(v6_pseudo(src6, dst6, udp_len, 17) + body) == 0, (
        "patched UDP checksum verifies against the v6 pseudo-header"
    )


def test_udp64_6052():
    payload = b"hello-udp64"
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IPv6(src="64:ff9b::c633:6402", dst="64:ff9b::c000:221", hlim=64, tc=0x88)
        / UDP(sport=12345, dport=53)
        / Raw(payload)
    )
    r = translate(bytes(pkt))
    assert r.verdict == "sent"

    src4 = socket.inet_aton("198.51.100.2")
    dst4 = socket.inet_aton("192.0.2.33")
    udp_len = 8 + len(payload)
    total_len = udp_len + 20
    v4 = v4_header(0x88, total_len, 63, 17, src4, dst4)
    udp = struct.pack("!HHHH", 12345, 53, udp_len, 0) + payload
    csum = ones_csum(v4_pseudo(src4, dst4, udp_len, 17) + udp)
    udp = udp[:6] + struct.pack("!H", csum) + udp[8:]
    expected = MAC1_B + MAC2_B + struct.pack("!H", ET_IPV4) + v4 + udp
    assert r.frame == expected, "full output frame, byte for byte"

    out_v4 = r.frame[14:34]
    assert struct.unpack("!H", out_v4[4:6])[0] == 0, "IPv4 ID == 0"
    assert struct.unpack("!H", out_v4[6:8])[0] & 0x4000, "DF set"
    assert out_v4[8] == 63, "TTL == hop limit - 1"
    assert ones_csum(out_v4) == 0, "fresh IPv4 header checksum verifies"


def test_eamt_beats_6052():
    """dst 192.0.2.1 is in DEMO_SIIT's EAMT -- RFC 7757 precedence says that
    wins over the 6052 embed, which would otherwise apply to any v4 dst."""
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(src="10.0.0.5", dst="192.0.2.1", ttl=64)
        / UDP(sport=1, dport=2)
        / Raw(b"x")
    )
    r = translate(bytes(pkt))
    assert r.verdict == "sent"
    dst6 = r.frame[14 + 24 : 14 + 40]
    assert dst6 == socket.inet_pton(socket.AF_INET6, "2001:db8:1::c001")
    assert dst6 != WKP + socket.inet_aton("192.0.2.1"), "EAMT wins, not the 6052 embed"


def test_tcp46_checksum_patch():
    src4 = socket.inet_aton("198.51.100.2")
    dst4 = socket.inet_aton("192.0.2.33")
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(src="198.51.100.2", dst="192.0.2.33", ttl=64, tos=0x10)
        / TCP(sport=1111, dport=80, seq=1, ack=0, flags="S", window=8192)
        / Raw(b"tcp-payload")
    )
    r = translate(bytes(pkt))
    assert r.verdict == "sent"

    src6, dst6 = WKP + src4, WKP + dst4
    v6 = r.frame[14:54]
    assert v6[6] == 6, "next header == TCP"
    assert v6[7] == 63

    tcp_seg = r.frame[54:]
    tcp_len = struct.unpack("!H", v6[4:6])[0]
    assert ones_csum(v6_pseudo(src6, dst6, tcp_len, 6) + tcp_seg) == 0, (
        "TCP checksum verifies like UDP -- only the address words were patched"
    )


def test_udp46_trailing_padding_is_not_leaked():
    """Ethernet minimum-frame padding (or any junk past the IPv4 Total
    Length) must not become IPv6 payload -- L4 is bound to Total Length,
    not to "whatever bytes happen to follow the header"."""
    src4 = socket.inet_aton("198.51.100.2")
    dst4 = socket.inet_aton("192.0.2.33")
    payload = b"pad-test"
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(src="198.51.100.2", dst="192.0.2.33", ttl=64)
        / UDP(sport=1, dport=2)
        / Raw(payload)
    )
    frame = bytes(pkt) + b"\x00\x00\x00\x00"  # 4 bytes beyond Total Length

    r = translate(frame)
    assert r.verdict == "sent"

    src6, dst6 = WKP + src4, WKP + dst4
    udp_len = 8 + len(payload)
    v6 = r.frame[14:54]
    assert struct.unpack("!H", v6[4:6])[0] == udp_len, "payload length excludes the padding"
    assert len(r.frame) == 14 + 40 + udp_len, "no leaked padding in the output frame"
    assert ones_csum(v6_pseudo(src6, dst6, udp_len, 17) + r.frame[54:]) == 0


def test_v6_output_zero_udp_checksum_becomes_0xffff():
    """RFC 768/8200: IPv6 UDP checksums are mandatory, so if the patched
    checksum would compute to 0x0000, it must be sent as 0xFFFF instead.
    Brute-force a payload word that lands the patched checksum on zero."""
    src4 = socket.inet_aton("198.51.100.2")
    dst4 = socket.inet_aton("192.0.2.33")
    src6, dst6 = WKP + src4, WKP + dst4
    sport, dport = 12345, 53

    tail = None
    for n in range(1 << 16):
        candidate = struct.pack("!H", n)
        udp_len = 8 + len(candidate)
        udp = struct.pack("!HHHH", sport, dport, udp_len, 0) + candidate
        if ones_csum(v6_pseudo(src6, dst6, udp_len, 17) + udp) == 0:
            tail = candidate
            break
    assert tail is not None, "the search space is a full 16-bit sweep -- one hit is guaranteed"

    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(src="198.51.100.2", dst="192.0.2.33", ttl=64)
        / UDP(sport=sport, dport=dport)
        / Raw(tail)
    )
    r = translate(bytes(pkt))
    assert r.verdict == "sent"
    assert r.frame[54 + 6 : 54 + 8] == b"\xff\xff", (
        "a computed-zero UDP checksum is transmitted as all-ones"
    )


def test_icmp46_echo():
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(src="198.51.100.2", dst="192.0.2.33", ttl=64)
        / ICMP(type=8, code=0)
        / Raw(struct.pack("!HH", 0x1234, 1) + b"ping-payload")
    )
    r = translate(bytes(pkt))
    assert r.verdict == "sent"

    src6 = WKP + socket.inet_aton("198.51.100.2")
    dst6 = WKP + socket.inet_aton("192.0.2.33")
    v6 = r.frame[14:54]
    body = r.frame[54:]
    assert v6[6] == 58, "next header 1 (ICMP) -> 58 (ICMPv6)"
    assert (body[0], body[1]) == (128, 0), "echo request 8 -> 128"
    icmp_len = struct.unpack("!H", v6[4:6])[0]
    assert ones_csum(v6_pseudo(src6, dst6, icmp_len, 58) + body) == 0, (
        "checksum verifies against the v6 pseudo-header ICMPv4 never had"
    )


# --------------------------------------------------------------------------
# Drops: every ledger reason gets a verdict.
# --------------------------------------------------------------------------


def test_ttl1_drops():
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(dst="192.0.2.33", ttl=1)
        / UDP(sport=1, dport=2)
        / Raw(b"x")
    )
    r = translate(bytes(pkt))
    assert (r.verdict, r.frame, r.why) == ("drop", None, "ttl_expired")


def test_zero_udp_csum_drops():
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(dst="192.0.2.33")
        / UDP(sport=1, dport=2, chksum=0)
        / Raw(b"x")
    )
    r = translate(bytes(pkt))
    assert (r.verdict, r.why) == ("drop", "zero_udp_checksum")


def test_bad_v4_header_csum_drops():
    pkt = Ether(dst=MAC1, src=MAC2) / IP(dst="192.0.2.33") / UDP(sport=1, dport=2) / Raw(b"x")
    frame = bytearray(bytes(pkt))
    frame[24] ^= 0xFF  # corrupt the IPv4 header checksum itself
    r = translate(bytes(frame))
    assert (r.verdict, r.why) == ("drop", "v4_bad_header_checksum")


def test_fragment_drops():
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(dst="192.0.2.33", flags="MF")
        / UDP(sport=1, dport=2)
        / Raw(b"x")
    )
    r = translate(bytes(pkt))
    assert (r.verdict, r.why) == ("drop", "fragment")


def test_icmp_error_drops():
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(dst="192.0.2.33")
        / ICMP(type=3, code=1)
        / Raw(b"x" * 8)
    )
    r = translate(bytes(pkt))
    assert (r.verdict, r.why) == ("drop", "icmp_error")


def test_unknown_l4_drops():
    pkt = Ether(dst=MAC1, src=MAC2) / IP(dst="192.0.2.33", proto=47) / Raw(b"x" * 8)
    r = translate(bytes(pkt))
    assert (r.verdict, r.why) == ("drop", "unsupported_l4")


def test_non_ip_ethertype_drops():
    pkt = Ether(dst=MAC1, src=MAC2) / ARP(pdst="10.0.0.1")
    r = translate(bytes(pkt))
    assert (r.verdict, r.why) == ("drop", "non_ip_ethertype")


def test_v6_dst_neither_pool6_nor_eamt_drops():
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IPv6(src="64:ff9b::c633:6402", dst="2001:db8:9::1234", hlim=64)
        / UDP(sport=1, dport=2)
        / Raw(b"x")
    )
    r = translate(bytes(pkt))
    assert (r.verdict, r.why) == ("drop", "untranslatable_address")


def test_v4_fragment_beats_unsupported_l4_drops():
    """A fragmented packet on an unsupported protocol reports "fragment",
    not "unsupported_l4" -- the frozen ledger order, both fields on the
    very same packet (MF set, proto 47)."""
    pkt = Ether(dst=MAC1, src=MAC2) / IP(dst="192.0.2.33", proto=47, flags="MF") / Raw(b"x" * 8)
    r = translate(bytes(pkt))
    assert (r.verdict, r.why) == ("drop", "fragment")


def test_v6_fragment_beats_unsupported_l4_drops():
    """Same overlap, v6 side: next header 44 (a Fragment extension header)
    is also not in {UDP, TCP, ICMPv6} -- must report "fragment", not
    "unsupported_l4"."""
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IPv6(src="64:ff9b::c633:6402", dst="64:ff9b::c000:221", hlim=64, nh=44)
        / Raw(b"x" * 8)
    )
    r = translate(bytes(pkt))
    assert (r.verdict, r.why) == ("drop", "fragment")


def test_v4_udp_truncated_l4_drops():
    """A UDP header cut short (here: 4 of its 8 bytes present) must DROP,
    never raise -- translate() is total."""
    pkt = Ether(dst=MAC1, src=MAC2) / IP(dst="192.0.2.33") / UDP(sport=1, dport=2) / Raw(b"payload")
    frame = bytes(pkt)[: 14 + 20 + 4]  # IPv4 header intact, only 4 UDP bytes
    r = translate(frame)
    assert (r.verdict, r.why) == ("drop", "l4_truncated")


def test_v6_tcp_truncated_l4_drops():
    """A TCP header cut short (10 of its 20 bytes) on the 64 side must
    DROP, with the v6 payload length field patched to match what's
    actually present -- isolating the TCP<20 guard from the payload_len-
    overrun guard covered separately below."""
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IPv6(src="64:ff9b::c633:6402", dst="64:ff9b::c000:221", hlim=64)
        / TCP(sport=1111, dport=80, flags="S")
        / Raw(b"payload")
    )
    frame = bytearray(bytes(pkt)[: 14 + 40 + 10])  # only 10 of the 20 TCP header bytes
    struct.pack_into("!H", frame, 14 + 4, 10)  # payload length matches what's present
    r = translate(bytes(frame))
    assert (r.verdict, r.why) == ("drop", "l4_truncated")


def test_v6_payload_len_overruns_frame_drops():
    """The v6 payload length field claiming more bytes than the frame
    actually carries is truncation too, distinct from a too-short L4
    sub-header -- must DROP, never slice past the end and misread."""
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IPv6(src="64:ff9b::c633:6402", dst="64:ff9b::c000:221", hlim=64)
        / UDP(sport=1, dport=2)
        / Raw(b"payload")
    )
    frame = bytearray(bytes(pkt))
    struct.pack_into("!H", frame, 14 + 4, 9999)  # claims far more than the frame carries
    r = translate(bytes(frame))
    assert (r.verdict, r.why) == ("drop", "l4_truncated")


def test_v4_icmp_2byte_l4_drops_without_raising():
    """type/code only, no checksum field at all -- the ICMP checksum patch
    reads body[2:4], so anything under 4 bytes must DROP before that read,
    never raise struct.error. Total Length is patched down to match the
    physical truncation (and the header checksum recomputed) so this
    actually reaches the ICMP<4B guard, not the earlier Total-Length-
    overrun guard."""
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(src="198.51.100.2", dst="192.0.2.33", ttl=64)
        / ICMP(type=8, code=0)
        / Raw(struct.pack("!HH", 0x1234, 1) + b"ping-payload")
    )
    frame = bytearray(bytes(pkt)[: 14 + 20 + 2])  # IPv4 header + type/code only
    struct.pack_into("!H", frame, 14 + 2, 22)  # Total Length == 20 + 2
    struct.pack_into("!H", frame, 14 + 10, 0)  # zero before recomputing
    struct.pack_into("!H", frame, 14 + 10, ones_csum(bytes(frame[14:34])))
    r = translate(bytes(frame))
    assert (r.verdict, r.why) == ("drop", "l4_truncated")


def test_v6_icmp_2byte_l4_drops_without_raising():
    """Same guard, v6 side: no v4-style header checksum to fix up, just
    the payload length patched to match the physical truncation."""
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IPv6(src="64:ff9b::c633:6402", dst="64:ff9b::c000:221", hlim=64)
        / ICMPv6EchoRequest(id=0x1234, seq=1)
        / Raw(b"ping-payload")
    )
    frame = bytearray(bytes(pkt)[: 14 + 40 + 2])  # v6 header + type/code only
    struct.pack_into("!H", frame, 14 + 4, 2)  # payload length == 2
    r = translate(bytes(frame))
    assert (r.verdict, r.why) == ("drop", "l4_truncated")


def test_v4_total_length_overrun_drops():
    """Total Length claiming far more than the physical frame carries must
    DROP as truncation, not fall through to build a malformed sent frame."""
    pkt = Ether(dst=MAC1, src=MAC2) / IP(dst="192.0.2.33") / UDP(sport=1, dport=2) / Raw(b"x")
    frame = bytearray(bytes(pkt))
    struct.pack_into("!H", frame, 14 + 2, 9999)  # Total Length far exceeds the frame
    r = translate(bytes(frame))
    assert (r.verdict, r.why) == ("drop", "l4_truncated")


def test_v4_fragment_with_truncated_l4_reports_fragment():
    """The definitive ledger order: fragment (c) is checked before L4
    truncation (d). A fragment whose remaining bytes are shorter than a
    UDP header still reports "fragment", not "l4_truncated" -- a
    non-initial fragment's payload was never going to be a UDP header."""
    pkt = (
        Ether(dst=MAC1, src=MAC2)
        / IP(dst="192.0.2.33", flags="MF")
        / UDP(sport=1, dport=2)
        / Raw(b"x")
    )
    frame = bytearray(bytes(pkt)[: 14 + 20 + 4])  # only 4 of the 8 UDP header bytes
    struct.pack_into("!H", frame, 14 + 2, 24)  # Total Length == 20 + 4
    struct.pack_into("!H", frame, 14 + 10, 0)
    struct.pack_into("!H", frame, 14 + 10, ones_csum(bytes(frame[14:34])))
    r = translate(bytes(frame))
    assert (r.verdict, r.why) == ("drop", "fragment")
