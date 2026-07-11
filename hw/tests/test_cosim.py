"""Differential cosimulation: NanukCore (pysim) vs the nanuk-emu golden
model, over the l2l3l4 demo corpus (the packets of
spec/python/tests/test_demo.py, rebuilt with scapy the same way) plus
seeded-random packets. The entire output contract is diffed field by field.

Requires the nanuk-emu binary (built in the linux devcontainer); gated on
NANUK_COSIM=1 so the module always imports cleanly.
"""

import os
import random
from pathlib import Path

import pytest
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw

from nanuk_spec.asm import assemble
from nanuk_spec.harness import run_program

from nanuk_hw.sim_util import run_core

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-emu (linux container)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ASM = REPO_ROOT / "examples" / "l2l3l4" / "parse.asm"

DMAC = "aa:bb:cc:dd:ee:01"


@pytest.fixture(scope="module")
def prog() -> bytes:
    return assemble(DEMO_ASM.read_text())


def demo_packets() -> list[tuple[str, bytes]]:
    """The demo corpus, one entry per test_demo.py case (same scapy builds)."""
    return [
        (
            "plain_ipv4_udp",
            bytes(Ether(dst=DMAC) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi")),
        ),
        (
            "single_vlan",
            bytes(Ether(dst=DMAC) / Dot1Q(vlan=100) / IP() / UDP(dport=4789)),
        ),
        (
            "qinq",
            bytes(
                Ether(dst=DMAC) / Dot1Q(vlan=200) / Dot1Q(vlan=300) / IP() / UDP(dport=53)
            ),
        ),
        (
            "ipv4_options",
            bytes(Ether(dst=DMAC) / IP(options=b"\x01\x01\x01\x01") / UDP(dport=53)),
        ),
        ("ipv4_tcp", bytes(Ether(dst=DMAC) / IP() / TCP(dport=80))),
        ("arp", bytes(Ether(dst=DMAC) / ARP(pdst="10.0.0.1"))),
        ("runt", bytes(10)),
        (
            "non_v4_version",
            bytes(Ether(dst=DMAC, type=0x0800) / Raw(b"\x60" + bytes(39))),
        ),
        (
            "corpus_udp",
            bytes(Ether(dst=DMAC) / IP() / UDP(dport=1)),
        ),
        (
            "corpus_triple_vlan",
            bytes(
                Ether(dst=DMAC)
                / Dot1Q(vlan=1)
                / Dot1Q(vlan=2)
                / Dot1Q(vlan=3)
                / IP()
                / UDP(dport=2)
            ),
        ),
        ("corpus_arp", bytes(Ether(dst=DMAC) / ARP())),
    ]


def assert_contract_matches(name: str, golden, rtl):
    """Diff the ENTIRE output contract, field by field."""
    assert rtl.verdict == golden.verdict, f"{name}: verdict"
    assert rtl.error == golden.error, f"{name}: error"
    assert rtl.payload_offset == golden.payload_offset, f"{name}: payload_offset"
    assert rtl.steps == golden.steps, f"{name}: steps"
    for i in range(16):
        assert bool(rtl.hdr_present[i]) == bool(golden.hdr_present[i]), (
            f"{name}: hdr_present[{i}]"
        )
        assert rtl.hdr_offset[i] == golden.hdr_offset[i], f"{name}: hdr_offset[{i}]"
    for i in range(8):
        assert rtl.smd[i] == golden.smd[i], f"{name}: smd[{i}]"


def test_demo_corpus_cosim(prog):
    named = demo_packets()
    rtl_results = run_core(prog, [pkt for _, pkt in named])
    for (name, pkt), rtl in zip(named, rtl_results):
        golden = run_program(prog, pkt)
        assert_contract_matches(name, golden, rtl)


def test_random_packets_cosim(prog):
    rng = random.Random(0x4E414E) # "NAN"
    packets = [rng.randbytes(rng.randint(0, 300)) for _ in range(20)]
    rtl_results = run_core(prog, packets)
    for i, (pkt, rtl) in enumerate(zip(packets, rtl_results)):
        golden = run_program(prog, pkt)
        assert_contract_matches(f"random[{i}] len={len(pkt)}", golden, rtl)
