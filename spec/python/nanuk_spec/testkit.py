"""Shared test fixtures: the demo packet corpus, MAC constants, and table
factories used by the differential rigs across spec/, hw/, and lang/.

Imports scapy, so this module is tests/dev-only by construction — it must
never be imported from shipping code (the playground-wheel rule).
"""

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw

from .map_harness import Table

# The demo MACs (the docs' example FDB): ...:01 -> port 2, ...:02 -> port 3.
DMAC = "aa:bb:cc:dd:ee:01"
DMAC2 = "aa:bb:cc:dd:ee:02"
DMAC_INT = 0xAABBCCDDEE01
DMAC2_INT = 0xAABBCCDDEE02

NO_TABLE = Table(key_width=0, action_width=0)


def demo_l2_table(both: bool = False) -> Table:
    """The L2 FDB the demos program: DMAC -> port 2 (and DMAC2 -> port 3)."""
    entries = {DMAC_INT: 0x4}
    if both:
        entries[DMAC2_INT] = 0x8
    return Table(key_width=48, action_width=8, entries=entries)


def demo_tun_table() -> Table:
    """The tunnel map (t1 in the demos): DMAC -> the tunnel port."""
    return Table(key_width=48, action_width=8, entries={DMAC_INT: 0x2})


def l2l3l4_packets() -> list[tuple[str, bytes]]:
    """The stage-1 demo corpus: one named packet per parser behavior."""
    return [
        (
            "plain_ipv4_udp",
            bytes(Ether(dst=DMAC) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi")),
        ),
        ("single_vlan", bytes(Ether(dst=DMAC) / Dot1Q(vlan=100) / IP() / UDP(dport=4789))),
        (
            "qinq",
            bytes(Ether(dst=DMAC) / Dot1Q(vlan=200) / Dot1Q(vlan=300) / IP() / UDP(dport=53)),
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
        ("corpus_udp", bytes(Ether(dst=DMAC) / IP() / UDP(dport=1))),
        (
            "corpus_triple_vlan",
            bytes(
                Ether(dst=DMAC) / Dot1Q(vlan=1) / Dot1Q(vlan=2) / Dot1Q(vlan=3)
                / IP() / UDP(dport=2)
            ),
        ),
        ("corpus_arp", bytes(Ether(dst=DMAC) / ARP())),
    ]


def map_packets() -> list[tuple[str, bytes]]:
    """The MAP-rig corpus: parser-shaped traffic plus the MAP edge cases
    (unknown DMAC for flood paths, TTL 0/1 for the router rule)."""
    return [
        (
            "plain",
            bytes(Ether(dst=DMAC) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi")),
        ),
        ("vlan", bytes(Ether(dst=DMAC) / Dot1Q(vlan=100) / IP(ttl=33) / UDP(dport=4789))),
        (
            "qinq",
            bytes(Ether(dst=DMAC) / Dot1Q(vlan=200) / Dot1Q(vlan=300) / IP() / UDP()),
        ),
        (
            "options",
            bytes(Ether(dst=DMAC) / IP(options=b"\x01\x01\x01\x01") / UDP()),
        ),
        ("arp", bytes(Ether(dst=DMAC) / ARP(pdst="10.0.0.1"))),
        ("unknown_dmac", bytes(Ether(dst="02:00:00:00:00:99") / IP() / UDP())),
        ("ttl1", bytes(Ether(dst=DMAC) / IP(ttl=1) / UDP())),
        ("ttl0", bytes(Ether(dst=DMAC) / IP(ttl=0) / UDP())),
    ]
