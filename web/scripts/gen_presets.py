"""Generate web/public/presets.json: the demo corpus + the nanukproto
tunnel as {name, hex, note}. Runs offline in the devcontainer (scapy
lives there); only hex strings ship — scapy never enters the bundle."""

import json
import pathlib
import struct

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw

DMAC = "aa:bb:cc:dd:ee:01"
OUT = pathlib.Path(__file__).resolve().parents[1] / "public" / "presets.json"


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

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(
    [{"name": n, "hex": bytes(p).hex(), "note": note} for n, p, note in PRESETS],
    indent=2,
) + "\n")
print(f"wrote {OUT} ({len(PRESETS)} presets)")
