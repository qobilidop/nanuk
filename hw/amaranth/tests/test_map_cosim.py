"""Differential cosimulation: MatchActionProcessor (pysim) vs the nanuk-map-emu golden
model, over the three M1 demo programs with their tables, driven by real PP
golden-model output — plus the composed PP-RTL -> MAP-RTL pipeline diffed
against run_pipeline. The ENTIRE outbound contract is compared, including
the transmitted frame bytes.

Gated on NANUK_COSIM=1 (needs both emulator binaries from the devcontainer
build)."""

import os
from pathlib import Path

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

from nanuk.isa.asm import assemble as pp_assemble
from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.testkit.harness import VERDICT_ACCEPT, run_program
from nanuk.testkit.map_harness import run_map, run_pipeline
from nanuk.testkit.testkit import (
    DMAC,
    NO_TABLE,
    demo_l2_table,
    demo_tun_table,
    map_packets,
)

from nanuk_amaranth.map_sim_util import run_map_one, run_pipeline_rtl

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-emu + nanuk-map-emu (linux container)",
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples"

L2_TABLE = demo_l2_table()
TUN_TABLE = demo_tun_table()


def assert_map_matches(name, golden, rtl):
    assert rtl.verdict == golden.verdict, f"{name}: verdict"
    assert rtl.error == golden.error, f"{name}: error"
    assert rtl.egress == golden.egress, f"{name}: egress"
    assert rtl.delta == golden.delta, f"{name}: delta"
    assert rtl.steps == golden.steps, f"{name}: steps"
    assert rtl.frame == golden.frame, f"{name}: frame"


@pytest.fixture(scope="module")
def progs():
    return {
        "pp": pp_assemble((EXAMPLES / "l2l3l4" / "parse.asm").read_text()),
        "pp_tunnel": pp_assemble(
            (EXAMPLES / "nanukproto" / "parse_tunnel.asm").read_text()
        ),
        "l2fwd": map_assemble((EXAMPLES / "map_l2fwd" / "fwd.asm").read_text()),
        "ttl": map_assemble((EXAMPLES / "map_ttl" / "fwd.asm").read_text()),
        "push": map_assemble((EXAMPLES / "nanukproto" / "tunnel_push.asm").read_text()),
        "pop": map_assemble((EXAMPLES / "nanukproto" / "tunnel_pop.asm").read_text()),
    }


@pytest.mark.parametrize(
    "map_key,tables",
    [("l2fwd", [L2_TABLE]), ("ttl", [L2_TABLE]), ("push", [NO_TABLE, TUN_TABLE])],
)
def test_demo_programs_cosim(progs, map_key, tables):
    for name, pkt in map_packets():
        pp = run_program(progs["pp"], pkt)
        if pp.verdict != VERDICT_ACCEPT:
            continue
        for ingress in (0, 1):
            golden = run_map(progs[map_key], pkt, pp, tables, ingress)
            rtl = run_map_one(progs[map_key], pkt, pp, tables, ingress)
            assert_map_matches(f"{map_key}/{name}/in{ingress}", golden, rtl)


def test_tunnel_pop_cosim(progs):
    # Feed the pop program frames produced by the golden push.
    inner = bytes(Ether(dst=DMAC) / IP(dst="10.1.0.9", ttl=17) / UDP(dport=4242))
    pp1 = run_program(progs["pp"], inner)
    pushed = run_map(progs["push"], inner, pp1, [NO_TABLE, TUN_TABLE], 0)
    assert pushed.sent and pushed.delta == 22
    for name, frame in [("tunnel", pushed.frame), ("plain", inner)]:
        pp2 = run_program(progs["pp_tunnel"], frame)
        assert pp2.verdict == VERDICT_ACCEPT
        golden = run_map(progs["pop"], frame, pp2, [], 1)
        rtl = run_map_one(progs["pop"], frame, pp2, [], 1)
        assert_map_matches(f"pop/{name}", golden, rtl)


def test_composed_pipeline_rtl_vs_golden(progs):
    for name, pkt in map_packets():
        gp, gm = run_pipeline(progs["pp"], progs["l2fwd"], pkt, [L2_TABLE], 1)
        rp, rm = run_pipeline_rtl(progs["pp"], progs["l2fwd"], pkt, [L2_TABLE], 1)
        assert rp.verdict == gp.verdict, f"{name}: PP verdict"
        assert (rm is None) == (gm is None), f"{name}: gating"
        if gm is not None:
            assert_map_matches(f"composed/{name}", gm, rm)
