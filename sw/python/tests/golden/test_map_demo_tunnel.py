"""M1 demo 3: nanukproto tunnel push/pop round-trip through two composed
switch hops: (l2l3l4 PP + tunnel_push MAP) encapsulates, (parse_tunnel PP +
tunnel_pop MAP) decapsulates, and the inner frame comes back byte-identical.
"""

from pathlib import Path

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

from nanuk.isa.asm import assemble as pp_assemble
from nanuk.isa.map_asm import assemble as map_assemble
from tests.support.map_harness import run_pipeline
from tests.support.testkit import DMAC, NO_TABLE, demo_tun_table

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLES = REPO_ROOT / "examples"

OUTER = bytes.fromhex(
    "024e4b000001" "024e4b000002" "88b5"      # outer Ethernet: dst, src, type
    "4e4b" "10" "000001" "6558"               # nk: magic, ver/flags, tenant, inner
)

TUN_TABLE = demo_tun_table()


@pytest.fixture(scope="module")
def progs():
    return {
        "pp_l2l3l4": pp_assemble((EXAMPLES / "l2l3l4" / "parse.asm").read_text()),
        "pp_tunnel": pp_assemble((EXAMPLES / "nanukproto" / "parse_tunnel.asm").read_text()),
        "map_push": map_assemble((EXAMPLES / "nanukproto" / "tunnel_push.asm").read_text()),
        "map_pop": map_assemble((EXAMPLES / "nanukproto" / "tunnel_pop.asm").read_text()),
    }


def test_push_prepends_exact_outer_header(progs):
    inner = bytes(Ether(dst=DMAC) / IP(dst="10.1.0.9") / UDP(dport=4242))
    pp, mp = run_pipeline(
        progs["pp_l2l3l4"], progs["map_push"], inner, [NO_TABLE, TUN_TABLE], ingress=0
    )
    assert pp.accepted
    assert mp is not None and mp.sent
    assert mp.egress == 0x2 and mp.delta == 22
    assert len(OUTER) == 22
    assert mp.frame == OUTER + inner


def test_push_pop_round_trip(progs):
    inner = bytes(Ether(dst=DMAC) / IP(dst="10.1.0.9", ttl=17) / UDP(dport=4242))
    # Hop 1: encap switch.
    _, push = run_pipeline(
        progs["pp_l2l3l4"], progs["map_push"], inner, [NO_TABLE, TUN_TABLE], ingress=0
    )
    assert push is not None and push.sent
    # Hop 2: decap switch receives the tunnel frame.
    pp2, pop = run_pipeline(
        progs["pp_tunnel"], progs["map_pop"], push.frame, [], ingress=1
    )
    assert pp2.accepted
    assert pp2.smd[5] == 0x4E4B, "PP flags the tunnel via SMD slot 5"
    assert pop is not None and pop.sent
    assert pop.delta == -22
    assert pop.frame == inner, "decap returns the original frame byte-identical"


def test_non_tunnel_traffic_floods_unchanged_through_pop(progs):
    plain = bytes(Ether(dst="02:00:00:00:00:07") / IP() / UDP(dport=9))
    pp, pop = run_pipeline(progs["pp_tunnel"], progs["map_pop"], plain, [], ingress=2)
    assert pp.accepted
    assert pp.smd[5] == 0
    assert pop is not None and pop.sent
    assert pop.delta == 0
    assert pop.frame == plain
    assert pop.egress == 0xB  # flood, ingress 2 excluded


def test_bad_magic_drops_at_pp(progs):
    # Valid EtherType, corrupted magic: PP drops, MAP never runs.
    inner = bytes(Ether(dst=DMAC) / IP() / UDP())
    frame = bytearray(OUTER + inner)
    frame[14] = 0xFF  # clobber magic hi byte
    pp, pop = run_pipeline(progs["pp_tunnel"], progs["map_pop"], bytes(frame), [], ingress=0)
    assert not pp.accepted
    assert pop is None


def test_unknown_dmac_bypasses_tunnel(progs):
    inner = bytes(Ether(dst="02:00:00:00:00:07") / IP() / UDP())
    pp, mp = run_pipeline(
        progs["pp_l2l3l4"], progs["map_push"], inner, [NO_TABLE, TUN_TABLE], ingress=3
    )
    assert mp is not None and mp.sent
    assert mp.delta == 0
    assert mp.frame == inner
    assert mp.egress == 0x7
