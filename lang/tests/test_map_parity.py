"""M3 parity gate: the eDSL demo MAP programs are behaviorally identical to
the hand-written examples/*.asm through the golden emulator (full MapResult
diff except `steps` — instruction schedules may differ), and interp_map
agrees with each eDSL program's own lowering on ALL fields including steps.

Gated on NANUK_COSIM=1 (needs both emulator binaries)."""

import os
from pathlib import Path

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw

from nanuk_ir.interp_map import interp_map
from nanuk_lang.programs.map_demos import (
    make_l2fwd,
    make_ttl,
    make_tunnel_pop,
    make_tunnel_push,
)
from nanuk_spec.asm import assemble as pp_assemble
from nanuk_spec.harness import VERDICT_ACCEPT, run_program
from nanuk_spec.map_asm import assemble as map_assemble
from nanuk_spec.map_harness import Table, run_map

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-emu + nanuk-map-emu (linux container)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "examples"

DMAC = "aa:bb:cc:dd:ee:01"
L2_TABLE = Table(key_width=48, action_width=8, entries={0xAABBCCDDEE01: 0x4})
NO_TABLE = Table(key_width=0, action_width=0)
TUN_TABLE = Table(key_width=48, action_width=8, entries={0xAABBCCDDEE01: 0x2})

DEMOS = {
    "l2fwd": (make_l2fwd, "map_l2fwd/fwd.asm", [L2_TABLE], "l2l3l4/parse.asm"),
    "ttl": (make_ttl, "map_ttl/fwd.asm", [L2_TABLE], "l2l3l4/parse.asm"),
    "push": (
        make_tunnel_push,
        "nanukproto/tunnel_push.asm",
        [NO_TABLE, TUN_TABLE],
        "l2l3l4/parse.asm",
    ),
    "pop": (make_tunnel_pop, "nanukproto/tunnel_pop.asm", [], "nanukproto/parse_tunnel.asm"),
}


def corpus() -> list[tuple[str, bytes]]:
    return [
        ("plain", bytes(Ether(dst=DMAC) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi"))),
        ("vlan", bytes(Ether(dst=DMAC) / Dot1Q(vlan=100) / IP(ttl=33) / UDP(dport=4789))),
        ("qinq", bytes(Ether(dst=DMAC) / Dot1Q(vlan=200) / Dot1Q(vlan=300) / IP() / UDP())),
        ("arp", bytes(Ether(dst=DMAC) / ARP(pdst="10.0.0.1"))),
        ("unknown", bytes(Ether(dst="02:00:00:00:00:99") / IP() / UDP())),
        ("ttl1", bytes(Ether(dst=DMAC) / IP(ttl=1) / UDP())),
        ("ttl0", bytes(Ether(dst=DMAC) / IP(ttl=0) / UDP())),
    ]


def tunnel_frames() -> list[tuple[str, bytes]]:
    inner = bytes(Ether(dst=DMAC) / IP(dst="10.1.0.9", ttl=17) / UDP(dport=4242))
    pp = run_program(pp_assemble((EXAMPLES / "l2l3l4" / "parse.asm").read_text()), inner)
    pushed = run_map(
        map_assemble((EXAMPLES / "nanukproto" / "tunnel_push.asm").read_text()),
        inner, pp, [NO_TABLE, TUN_TABLE], 0,
    )
    assert pushed.sent and pushed.delta == 22
    return [("tunnel_frame", pushed.frame), ("plain_frame", inner)]


@pytest.mark.parametrize("name", DEMOS)
def test_edsl_matches_hand_asm(name):
    make, hand_path, tables, pp_path = DEMOS[name]
    pp_prog = pp_assemble((EXAMPLES / pp_path).read_text())
    hand = map_assemble((EXAMPLES / hand_path).read_text())
    edsl = map_assemble(make().compile())
    packets = corpus() if name != "pop" else corpus() + tunnel_frames()
    compared = 0
    for pname, pkt in packets:
        pp = run_program(pp_prog, pkt)
        if pp.verdict != VERDICT_ACCEPT:
            continue
        for ingress in (0, 2):
            g = run_map(hand, pkt, pp, tables, ingress)
            e = run_map(edsl, pkt, pp, tables, ingress)
            for field in ("verdict", "error", "egress", "delta", "frame"):
                assert getattr(g, field) == getattr(e, field), (
                    f"{name}/{pname}/in{ingress}: {field} "
                    f"hand={getattr(g, field)} edsl={getattr(e, field)}"
                )
            compared += 1
    assert compared > 0


@pytest.mark.parametrize("name", DEMOS)
def test_interp_map_matches_own_lowering(name):
    make, _, tables, pp_path = DEMOS[name]
    mp = make()
    program = mp.build_ir()
    binary = map_assemble(mp.compile())
    pp_prog = pp_assemble((EXAMPLES / pp_path).read_text())
    packets = corpus() if name != "pop" else corpus() + tunnel_frames()
    for pname, pkt in packets:
        pp = run_program(pp_prog, pkt)
        if pp.verdict != VERDICT_ACCEPT:
            continue
        g = run_map(binary, pkt, pp, tables, 1)
        i = interp_map(program, pkt, pp, tables, 1)
        for field in ("verdict", "error", "egress", "delta", "steps", "frame"):
            assert getattr(g, field) == getattr(i, field), (
                f"{name}/{pname}: {field} emu={getattr(g, field)} "
                f"interp={getattr(i, field)}"
            )
