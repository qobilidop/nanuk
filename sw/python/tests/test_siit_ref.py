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
from scapy.layers.inet6 import IPv6
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
