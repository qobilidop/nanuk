"""M1 demo 2: TTL decrement + CSUMUPD, with scapy as the checksum oracle.

The output frame must carry TTL-1 and a header checksum byte-identical to
scapy's own recomputation for that TTL. TTL 0/1 drop (router rule). Non-IPv4
traffic lands on the defined err_hdr_absent error halt (totality as guard).
VLAN-tagged IPv4 exercises the hdr-relative addressing (h_ipv4 base moves)."""

from pathlib import Path

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether

from nanuk.isa.pp_asm import assemble as pp_assemble
from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.testkit.map_harness import (
    MAP_ERR_HDR_ABSENT,
    VERDICT_DROP,
    VERDICT_ERROR,
    run_pipeline,
)
from nanuk.testkit.testkit import DMAC, demo_flood_table, demo_l2_table, NO_TABLE

REPO_ROOT = Path(__file__).resolve().parents[4]
PP_ASM = REPO_ROOT / "examples" / "l2l3l4" / "parse.asm"
MAP_ASM = REPO_ROOT / "examples" / "map_ttl" / "fwd.asm"

L2_TABLE = demo_l2_table()
TABLES = [L2_TABLE, NO_TABLE, NO_TABLE, demo_flood_table()]


@pytest.fixture(scope="module")
def pp_prog() -> bytes:
    return pp_assemble(PP_ASM.read_text())


@pytest.fixture(scope="module")
def map_prog() -> bytes:
    return map_assemble(MAP_ASM.read_text())


def forward(pp_prog, map_prog, pkt):
    return run_pipeline(pp_prog, map_prog, bytes(pkt), TABLES, [0])


@pytest.mark.parametrize("ttl", [2, 64, 255])
def test_ttl_decrement_with_scapy_checksum_oracle(pp_prog, map_prog, ttl):
    pkt = Ether(dst=DMAC) / IP(dst="10.0.0.1", src="10.0.0.2", ttl=ttl) / UDP(dport=53)
    pp, mp = forward(pp_prog, map_prog, pkt)
    assert mp is not None and mp.sent and mp.md[0] == 0x4
    out = Ether(mp.frame)
    assert out[IP].ttl == ttl - 1
    # Oracle: scapy recomputes the checksum for the decremented packet.
    oracle = Ether(dst=DMAC) / IP(dst="10.0.0.1", src="10.0.0.2", ttl=ttl - 1) / UDP(dport=53)
    oracle = Ether(bytes(oracle))  # force checksum computation
    assert out[IP].chksum == oracle[IP].chksum
    # Everything else in the frame is untouched.
    assert mp.frame == bytes(oracle)


def test_vlan_tagged_ipv4_ttl(pp_prog, map_prog):
    pkt = Ether(dst=DMAC) / Dot1Q(vlan=100) / IP(dst="10.0.0.1", ttl=33) / UDP()
    pp, mp = forward(pp_prog, map_prog, pkt)
    assert mp is not None and mp.sent
    out = Ether(mp.frame)
    assert out[IP].ttl == 32
    oracle = Ether(dst=DMAC) / Dot1Q(vlan=100) / IP(dst="10.0.0.1", ttl=32) / UDP()
    assert out[IP].chksum == Ether(bytes(oracle))[IP].chksum


@pytest.mark.parametrize("ttl", [0, 1])
def test_expired_ttl_drops(pp_prog, map_prog, ttl):
    pkt = Ether(dst=DMAC) / IP(dst="10.0.0.1", ttl=ttl) / UDP()
    pp, mp = forward(pp_prog, map_prog, pkt)
    assert mp is not None
    assert mp.verdict == VERDICT_DROP
    assert mp.frame is None


def test_non_ipv4_is_defined_error_drop(pp_prog, map_prog):
    pkt = Ether(dst=DMAC) / ARP(pdst="10.0.0.1")
    pp, mp = forward(pp_prog, map_prog, pkt)
    assert pp.accepted, "PP accepts ARP with eth header only"
    assert mp is not None
    assert mp.verdict == VERDICT_ERROR
    assert mp.error == MAP_ERR_HDR_ABSENT
