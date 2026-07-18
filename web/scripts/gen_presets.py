"""Generate web/public/presets.json: the demo corpus + the nanukproto
tunnel + the SIIT translator vectors, as {name, hex, note, programs}. Runs
offline in the devcontainer (scapy lives there); only hex strings ship —
scapy never enters the bundle. `programs` scopes each preset's chip to the
programs it makes sense for (a preset without the field would show for all).

The SIIT presets are read straight from the committed conformance vectors
(benchmarks/siit/vectors/*.json — already scapy-free bytes), so no scapy
dependency reaches the SIIT half of the corpus."""

import json
import pathlib
import struct

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw

DMAC = "aa:bb:cc:dd:ee:01"
REPO = pathlib.Path(__file__).resolve().parents[2]
OUT = REPO / "web" / "public" / "presets.json"
VECTORS = REPO / "benchmarks" / "siit" / "vectors"

# Every classic parser/MAP packet shows for these programs (not SIIT).
CLASSIC = ["l2l3l4", "nanukproto", "map_l2fwd"]

# The frozen five SIIT presets, drawn from the committed vectors: a name in
# its group file, a human label, and the expected outcome note.
SIIT_PRESETS = [
    ("udp46", "udp46_len25_ttl64", "IPv4->IPv6 UDP (RFC 6052 64:ff9b::/96) — sent"),
    ("udp64", "udp64_len25_ttl64", "IPv6->IPv4 UDP — sent"),
    ("edge", "edge_eamt_dst_46", "IPv4->IPv6, dst hits the EAMT (192.0.2.1) — sent"),
    ("icmp46", "icmp46_len25_ttl64", "IPv4->IPv6 ICMP echo — sent"),
    ("negative", "neg_v4_ttl_expired", "TTL=1 — dropped (ttl_expired), no ICMP error"),
]


def siit_presets() -> list[dict]:
    out = []
    for group, name, note in SIIT_PRESETS:
        vecs = json.loads((VECTORS / f"{group}.json").read_text())
        vec = next(v for v in vecs if v["name"] == name)
        out.append({
            "name": name, "hex": vec["in"], "note": note, "programs": ["siit"],
        })
    return out


def nk_tunnel() -> bytes:
    nk = (struct.pack(">H", 0x4E4B) + bytes([1 << 4])
          + (0x0ABCDE).to_bytes(3, "big") + struct.pack(">H", 0x0800))
    inner = bytes(IP(dst="10.0.0.2") / UDP(dport=4242) / Raw(b"hi"))
    eth = bytes.fromhex("aabbccddee01") + bytes(6) + struct.pack(">H", 0x88B5)
    return eth + nk + inner


PRESETS = [
    ("plain_ipv4_udp", Ether(dst=DMAC) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi"),
     "Ethernet / IPv4 / UDP"),
    ("single_vlan", Ether(dst=DMAC) / Dot1Q(vlan=100) / IP() / UDP(dport=4789),
     "one 802.1Q tag"),
    ("qinq", Ether(dst=DMAC) / Dot1Q(vlan=200) / Dot1Q(vlan=300) / IP() / UDP(dport=53),
     "stacked VLANs (QinQ)"),
    ("ipv4_options", Ether(dst=DMAC) / IP(options=b"\x01\x01\x01\x01") / UDP(dport=53),
     "IPv4 with options (IHL > 5)"),
    ("ipv4_tcp", Ether(dst=DMAC) / IP() / TCP(dport=80), "TCP: accepted, no UDP header"),
    ("arp", Ether(dst=DMAC) / ARP(pdst="10.0.0.1"), "unknown EtherType: accept"),
    ("runt_frame", bytes(10), "10 bytes: header violation"),
    ("non_v4_version", Ether(dst=DMAC, type=0x0800) / Raw(b"\x60" + bytes(39)),
     "IPv4 EtherType but version 6: drop"),
    ("nk_tunnel", nk_tunnel(), "the invented nanukproto tunnel (beat 3)"),
]

entries = [
    {"name": n, "hex": bytes(p).hex(), "note": note, "programs": CLASSIC}
    for n, p, note in PRESETS
]
entries += siit_presets()

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(entries, indent=2) + "\n")
print(f"wrote {OUT} ({len(entries)} presets)")
