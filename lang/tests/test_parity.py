"""Stage-2 done criterion: the eDSL demo is behaviorally identical to the
hand-written examples/l2l3l4/parse.asm over the full test_demo.py corpus.

Gated behind NANUK_COSIM=1 because it needs the built nanuk-emu golden
model; everything here imports cleanly without the emulator binary.
"""

import os
from pathlib import Path

import pytest
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw

from nanuk_lang.programs.l2l3l4 import build
from nanuk_spec.asm import assemble
from nanuk_spec.harness import run_program

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="cosim parity needs NANUK_COSIM=1 and a built nanuk-emu",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HAND_ASM = REPO_ROOT / "examples" / "l2l3l4" / "parse.asm"

DMAC = "aa:bb:cc:dd:ee:01"

# The full spec/python/tests/test_demo.py corpus, including the three
# packets of its step-budget test.
CORPUS = [
    ("plain_ipv4_udp", Ether(dst=DMAC) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi")),
    ("single_vlan", Ether(dst=DMAC) / Dot1Q(vlan=100) / IP() / UDP(dport=4789)),
    ("qinq", Ether(dst=DMAC) / Dot1Q(vlan=200) / Dot1Q(vlan=300) / IP() / UDP(dport=53)),
    ("ipv4_options", Ether(dst=DMAC) / IP(options=b"\x01\x01\x01\x01") / UDP(dport=53)),
    ("ipv4_tcp", Ether(dst=DMAC) / IP() / TCP(dport=80)),
    ("arp", Ether(dst=DMAC) / ARP(pdst="10.0.0.1")),
    ("runt_frame", bytes(10)),
    ("non_v4_version", Ether(dst=DMAC, type=0x0800) / Raw(b"\x60" + bytes(39))),
    ("budget_plain", Ether(dst=DMAC) / IP() / UDP(dport=1)),
    ("budget_triple_vlan",
     Ether(dst=DMAC) / Dot1Q(vlan=1) / Dot1Q(vlan=2) / Dot1Q(vlan=3) / IP() / UDP(dport=2)),
    ("budget_arp", Ether(dst=DMAC) / ARP()),
]


@pytest.fixture(scope="module")
def hand_prog() -> bytes:
    return assemble(HAND_ASM.read_text())


@pytest.fixture(scope="module")
def edsl_prog() -> bytes:
    return assemble(build())


@pytest.mark.parametrize("pkt", [p for _, p in CORPUS], ids=[n for n, _ in CORPUS])
def test_parity(hand_prog, edsl_prog, pkt):
    raw = bytes(pkt)
    hand = run_program(hand_prog, raw)
    edsl = run_program(edsl_prog, raw)

    assert edsl.verdict == hand.verdict
    assert edsl.error == hand.error
    assert edsl.payload_offset == hand.payload_offset
    assert edsl.hdr_present == hand.hdr_present
    assert edsl.hdr_offset == hand.hdr_offset
    assert edsl.smd == hand.smd
    # Instruction counts may differ between the two programs; both must
    # simply stay inside the step budget.
    assert hand.steps < 256
    assert edsl.steps < 256
