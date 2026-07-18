"""Shared test fixtures: the demo packet corpus, MAC constants, and table
factories used by the differential rigs across spec/, hw/, and lang/.

Imports scapy, so this module is tests/dev-only by construction — it must
never be imported from shipping code (the playground-wheel rule).
"""

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw

from .map_harness import Table
from .siit_ref import DEMO_SIIT, SiitConfig

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


def demo_flood_table(n_ports: int = 4) -> Table:
    """The system flood table (t3 by nanuk_switch convention):
    {ingress port id -> flood bitmap}, installed by the control plane."""
    all_ports = (1 << n_ports) - 1
    return Table(
        key_width=16,
        action_width=16,
        entries={i: all_ports & ~(1 << i) for i in range(n_ports)},
    )


def demo_tables(l2_both: bool = False) -> list[Table]:
    """The standard demo table plane: t0 = L2 FDB, t1 = tunnel map,
    t2 unconfigured, t3 = system flood table."""
    return [demo_l2_table(both=l2_both), demo_tun_table(), NO_TABLE, demo_flood_table()]


def siit_tables(cfg: SiitConfig = DEMO_SIIT) -> list[Table]:
    """The SIIT translator's table plane (frozen in the part-A plan), built
    from a SiitConfig's EAMT. LOOKUP keys/actions are <=64 bits, hence the
    hi/lo split for the 128-bit v6 side:

      t0: v4 -> v6 EAMT, key = v4 addr (32b), action = v6 addr high 64b
      t1: v4 -> v6 EAMT, key = v4 addr (32b), action = v6 addr low 64b
      t2: v6 -> v4 EAMT, key = v6 addr LOW 64b, action = v4 addr (32b)

    t2's low-64 keying is a documented demo constraint: EAMT v6 entries must
    be distinct in their low 64 bits (general prefixes are the LPM trigger).
    Keys/actions are big-endian ints of the address bytes.
    """
    t0 = {
        int.from_bytes(v4, "big"): int.from_bytes(v6[:8], "big")
        for v4, v6 in cfg.eamt46.items()
    }
    t1 = {
        int.from_bytes(v4, "big"): int.from_bytes(v6[8:], "big")
        for v4, v6 in cfg.eamt46.items()
    }
    t2 = {
        int.from_bytes(v6[8:], "big"): int.from_bytes(v4, "big")
        for v6, v4 in cfg.eamt64.items()
    }
    return [
        Table(key_width=32, action_width=64, entries=t0),
        Table(key_width=32, action_width=64, entries=t1),
        Table(key_width=64, action_width=32, entries=t2),
    ]


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
