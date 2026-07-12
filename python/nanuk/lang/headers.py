"""Standard protocol headers, shipped with the toolchain.

The p4include precedent: P4 ships core.p4/v1model.p4 with the compiler
and tutorial programs import them. Same idea here — these are IANA-shaped
facts, not examples: demo programs (examples/ at the repo root) and the
playground's editor programs import from this module.

Invented protocols (e.g. nanukproto) do NOT belong here; they live with
their example.
"""

from .header import Header

eth = Header("eth", dst=48, src=48, ethertype=16)
vlan = Header("vlan", tci=16, ethertype=16)
ipv4 = Header(
    "ipv4",
    version=4, ihl=4, tos=8, total_len=16, ident=16,
    flags_frag=16, ttl=8, proto=8, csum=16, src=32, dst=32,
)
udp = Header("udp", sport=16, dport=16, length=16, csum=16)

# The matching wire constants (EtherTypes, IP protocol numbers).
ETY_VLAN, ETY_IPV4 = 0x8100, 0x0800
PROTO_UDP = 17
