# siit — RFC 7915 SIIT translator on the Nanuk core

A stateless IP/ICMP translator (SIIT, RFC 7915): IPv4 frames come out as
IPv6 and vice versa, headers rewritten in place, L4 checksums patched
incrementally, addresses mapped by RFC 7757 explicit entries with the
RFC 6052 well-known prefix `64:ff9b::/96` as the fallback. This is the
realest program in the examples tree: a middlebox function that ISPs
actually deploy, running on the same two engines as every other example.

Why it matters: stateless SIIT is the phone-side half of 464XLAT
(RFC 6877). The CLAT in an IPv6-only mobile handset is exactly this
function — every IPv4-only app on such a phone gets its packets through a
translator shaped like this one, and the network-side PLAT undoes it at
scale. The demo config (`DEMO_SIIT`) is one EAMT pair
`192.0.2.1 ↔ 2001:db8:1::c001` over the well-known prefix.

## The pieces

- `parse.asm` (PP) — dispatches EtherType, walks the v4/v6 header, records
  the header map (ids: 0 eth, 1 ipv4, 2 ipv6, 3 udp, 4 tcp, 5 icmpv4,
  6 icmpv6, plus 7 = L4-start alias), and writes a header-present bitmap to
  `md[1]`. It refuses what parsing itself can see — runts, truncated
  headers, non-IP EtherTypes; every value decision is left to the MAP.
- `translate.asm` (MAP) — the value half of the ingress ledger (header
  checksum, fragments, zero UDP checksum, ICMP non-echo, unsupported L4,
  TTL) and the actual translation: Ethernet relocated ±20 bytes with only
  the EtherType changed, the new IP header built field by field per the
  frozen RFC 7915 mappings, RFC 1624 incremental checksum patches, and
  SEND with a head delta (`40 − IHL` one way, `−20` the other).

## The table plane

LOOKUP keys and actions are ≤ 64 bits, so the 128-bit v6 side splits:

| table | key | action |
|---|---|---|
| `t0` | v4 addr (32b) | EAMT v6 addr, high 64 |
| `t1` | v4 addr (32b) | EAMT v6 addr, low 64 |
| `t2` | v6 addr, **low 64** | EAMT v4 addr (32b) |

`testkit.siit_tables()` builds all three from a `SiitConfig`. EAMT entries
must be distinct in their low 64 bits (a documented demo constraint;
general prefix EAMT is the LPM trigger). Precedence is RFC 7757's: EAMT
first for v4→v6; for v6→v4 the pool6 prefix extracts directly and
everything else consults `t2`, miss = untranslatable → drop. The table IS
the policy: re-pair the EAMT and the same program translates a different
customer.

## Scope and oracles

The program's contract is the committed conformance corpus
`benchmarks/siit/vectors/` — 70 vectors generated from the executable
spec `nanuk.testkit.siit_ref` and replayed byte-for-byte on the golden
emulators by `sw/python/tests/test_siit_program.py`. Trailing frame bytes
beyond the IP datagram (e.g. Ethernet minimum-frame padding) pass through
to the output verbatim, unchanged — the reference was amended to this
after an earlier version wrongly stripped them; see disposition
`7915-framing-trailer` in
[`benchmarks/siit/audit.md`](../../benchmarks/siit/audit.md) and the lead
section of
[the lab notes](../../docs/notes/2026-07-18-siit-core-lab-notes.md). The
`edge_min_frame_46` vector exercises it and passes.

What's deliberately out of scope (each dispositioned in
[`benchmarks/siit/audit.md`](../../benchmarks/siit/audit.md)): fragment
translation, ICMP error translation, IPv6 extension headers,
general-prefix EAMT. VLAN tags are outside this arc's framing convention
(the parser handles untagged frames only) — a design-time scope decision
in the demo design spec, not an audit disposition. Design and staging:
[the SIIT demo design spec](../../docs/superpowers/specs/2026-07-18-siit-demo-design.md)
and [the part-A plan](../../docs/superpowers/plans/2026-07-18-siit-a-core.md).
