"""M1 demo 1: L2 forwarding through the composed PP -> MAP golden models.

PP = examples/l2l3l4/parse.asm, MAP = examples/map_l2fwd/fwd.asm, over the
stage-1 corpus shapes: known DMACs egress exactly their configured port with
the frame unmodified; unknown DMACs flood everything but the ingress port;
PP-dropped packets short-circuit before the MAP."""

from pathlib import Path

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Dot1Q, Ether
from scapy.packet import Raw

from nanuk_spec.asm import assemble as pp_assemble
from nanuk_spec.map_asm import assemble as map_assemble
from nanuk_spec.map_harness import Table, run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[3]
PP_ASM = REPO_ROOT / "examples" / "l2l3l4" / "parse.asm"
MAP_ASM = REPO_ROOT / "examples" / "map_l2fwd" / "fwd.asm"

DMAC_PORT2 = "aa:bb:cc:dd:ee:01"
DMAC_PORT3 = "aa:bb:cc:dd:ee:02"

L2_TABLE = Table(
    key_width=48,
    action_width=8,
    entries={
        0xAABBCCDDEE01: 0x4,  # -> port 2
        0xAABBCCDDEE02: 0x8,  # -> port 3
    },
)


@pytest.fixture(scope="module")
def pp_prog() -> bytes:
    return pp_assemble(PP_ASM.read_text())


@pytest.fixture(scope="module")
def map_prog() -> bytes:
    return map_assemble(MAP_ASM.read_text())


def forward(pp_prog, map_prog, pkt, ingress=0):
    return run_pipeline(pp_prog, map_prog, bytes(pkt), [L2_TABLE], ingress=ingress)


@pytest.mark.parametrize(
    "pkt_builder",
    [
        lambda dst: Ether(dst=dst) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi"),
        lambda dst: Ether(dst=dst) / Dot1Q(vlan=100) / IP() / UDP(dport=4789),
        lambda dst: Ether(dst=dst) / Dot1Q(vlan=7) / Dot1Q(vlan=300) / IP() / UDP(),
        lambda dst: Ether(dst=dst) / IP(dst="10.0.0.1", options=[]) / UDP() / Raw(b"x" * 32),
    ],
)
def test_known_dmacs_unicast(pp_prog, map_prog, pkt_builder):
    for dmac, bitmap in ((DMAC_PORT2, 0x4), (DMAC_PORT3, 0x8)):
        pkt = pkt_builder(dmac)
        pp, mp = forward(pp_prog, map_prog, pkt, ingress=0)
        assert pp.accepted
        assert mp is not None and mp.sent
        assert mp.egress == bitmap
        assert mp.frame == bytes(pkt), "L2 forward must not modify the frame"


def test_unknown_dmac_floods_all_but_ingress(pp_prog, map_prog):
    pkt = Ether(dst="02:00:00:00:00:99") / IP() / UDP(dport=9)
    for ingress in range(4):
        pp, mp = forward(pp_prog, map_prog, pkt, ingress=ingress)
        assert mp is not None and mp.sent
        assert mp.egress == (0xF & ~(1 << ingress))
        assert mp.frame == bytes(pkt)


def test_pp_drop_short_circuits(pp_prog, map_prog):
    # EtherType says IPv4, version nibble says 6 -> PP drops; MAP never runs.
    pkt = Ether(dst=DMAC_PORT2, type=0x0800) / Raw(bytes([0x60] + [0] * 27))
    pp, mp = forward(pp_prog, map_prog, pkt)
    assert not pp.accepted
    assert mp is None


def test_map_steps_are_tiny(pp_prog, map_prog):
    # Hit path: LD, LOOKUP, SEND = 3 steps. Miss path: 4 steps.
    hit_pkt = Ether(dst=DMAC_PORT2) / IP() / UDP()
    _, mp = forward(pp_prog, map_prog, hit_pkt)
    assert mp is not None and mp.steps == 3
    miss_pkt = Ether(dst="02:00:00:00:00:99") / IP() / UDP()
    _, mp = forward(pp_prog, map_prog, miss_pkt)
    assert mp is not None and mp.steps == 4
