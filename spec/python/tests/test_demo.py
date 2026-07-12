"""Stage-1 done criterion: the v0 demo program parses a pcap corpus of
Ethernet / VLAN (QinQ) / IPv4 (options) / UDP traffic on the golden model."""

from pathlib import Path

import pytest
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw
from scapy.utils import wrpcap

from nanuk_spec.asm import assemble
from nanuk_spec.testkit import DMAC
from nanuk_spec.harness import (
    ERR_HDR_VIOLATION,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
    run_pcap,
    run_program,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_ASM = REPO_ROOT / "examples" / "l2l3l4" / "parse.asm"

H_ETH, H_VLAN, H_IPV4, H_UDP = 0, 1, 2, 3
DMAC_SMD = [0xAABB, 0xCCDD, 0xEE01]


@pytest.fixture(scope="module")
def prog() -> bytes:
    return assemble(DEMO_ASM.read_text())


def run_one(prog: bytes, pkt, tmp_path: Path):
    pcap = tmp_path / "one.pcap"
    wrpcap(str(pcap), [pkt])
    results = run_pcap(prog, pcap)
    assert len(results) == 1
    return results[0]


def test_plain_ipv4_udp(prog, tmp_path):
    pkt = Ether(dst=DMAC) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi")
    r = run_one(prog, pkt, tmp_path)
    assert r.verdict == VERDICT_ACCEPT
    assert r.hdr(H_ETH) == 0
    assert r.hdr(H_VLAN) is None
    assert r.hdr(H_IPV4) == 14
    assert r.hdr(H_UDP) == 34
    assert r.payload_offset == 42
    assert r.smd[0:3] == DMAC_SMD
    assert r.smd[4] == 53


def test_single_vlan(prog, tmp_path):
    pkt = Ether(dst=DMAC) / Dot1Q(vlan=100) / IP() / UDP(dport=4789)
    r = run_one(prog, pkt, tmp_path)
    assert r.verdict == VERDICT_ACCEPT
    assert r.hdr(H_VLAN) == 14
    assert r.hdr(H_IPV4) == 18
    assert r.hdr(H_UDP) == 38
    assert r.payload_offset == 46
    assert r.smd[3] == 100  # TCI with priority 0 = VID
    assert r.smd[4] == 4789


def test_qinq_records_last_tag(prog, tmp_path):
    pkt = Ether(dst=DMAC) / Dot1Q(vlan=200) / Dot1Q(vlan=300) / IP() / UDP(dport=53)
    r = run_one(prog, pkt, tmp_path)
    assert r.verdict == VERDICT_ACCEPT
    assert r.hdr(H_VLAN) == 18  # last tag
    assert r.hdr(H_IPV4) == 22
    assert r.hdr(H_UDP) == 42
    assert r.smd[3] == 300


def test_ipv4_with_options(prog, tmp_path):
    # Four NOP option bytes -> IHL 6 (24-byte IPv4 header).
    pkt = Ether(dst=DMAC) / IP(options=b"\x01\x01\x01\x01") / UDP(dport=53)
    r = run_one(prog, pkt, tmp_path)
    assert r.verdict == VERDICT_ACCEPT
    assert r.hdr(H_IPV4) == 14
    assert r.hdr(H_UDP) == 38  # 14 + 24
    assert r.payload_offset == 46


def test_ipv4_tcp_accepts_without_udp_header(prog, tmp_path):
    pkt = Ether(dst=DMAC) / IP() / TCP(dport=80)
    r = run_one(prog, pkt, tmp_path)
    assert r.verdict == VERDICT_ACCEPT
    assert r.hdr(H_IPV4) == 14
    assert r.hdr(H_UDP) is None
    assert r.payload_offset == 34  # L4 start


def test_arp_accepts_with_eth_only(prog, tmp_path):
    pkt = Ether(dst=DMAC) / ARP(pdst="10.0.0.1")
    r = run_one(prog, pkt, tmp_path)
    assert r.verdict == VERDICT_ACCEPT
    assert r.hdr(H_ETH) == 0
    assert r.hdr(H_IPV4) is None
    assert r.payload_offset == 14


def test_runt_frame_is_header_violation(prog):
    # 10 bytes: enough for the 48-bit DMAC extract, short of the EtherType.
    r = run_program(prog, bytes(10))
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_HDR_VIOLATION


def test_non_v4_version_drops(prog, tmp_path):
    # EtherType says IPv4, first IP byte says version 6.
    pkt = Ether(dst=DMAC, type=0x0800) / Raw(b"\x60" + bytes(39))
    r = run_one(prog, pkt, tmp_path)
    assert r.verdict == VERDICT_DROP
    assert r.hdr(H_IPV4) == 14  # recorded before validation, v0 semantics


def test_whole_corpus_stays_within_step_budget(prog, tmp_path):
    packets = [
        Ether(dst=DMAC) / IP() / UDP(dport=1),
        Ether(dst=DMAC) / Dot1Q(vlan=1) / Dot1Q(vlan=2) / Dot1Q(vlan=3) / IP() / UDP(dport=2),
        Ether(dst=DMAC) / ARP(),
    ]
    pcap = tmp_path / "corpus.pcap"
    wrpcap(str(pcap), packets)
    for r in run_pcap(prog, pcap):
        assert r.steps < 256
