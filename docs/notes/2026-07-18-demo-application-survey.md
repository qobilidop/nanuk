# Demo application survey — porting a notable dataplane project to Nanuk

Date: 2026-07-18. Status: survey complete; **decision: Jool SIIT is the first
application demo**. This note captures the full candidate field so future demos
(and future ISA extensions) can start from here instead of re-surveying.

## The brief

Find an application demo shaped like "Nanuk runs the core of a notable
open-source dataplane project, validated against that project's own tests."
Katran was the motivating example, not a requirement. Criteria, in Bili's
words:

1. Ideally a notable open-source project of its own.
2. Plus if it has a comprehensive test suite / benchmarks we can leverage.
3. Realistic for us to implement.
4. Highly relevant to programmable data planes.
5. Simpler is better — this is an educational project.

Method: two parallel web surveys (L4 load-balancer family; general dataplane
applications), each scoring candidates against the criteria and against
Nanuk's actual capability envelope — exact-match tables only (LPM/counters
planned, unbuilt), ALU add/sub/and/or/xor + shifts-left + immediates,
byte-granular hdr-relative load/store in the 256B window, 32B headroom with
signed head-delta at SEND, ones-complement range CSUM, 256-step budget; and
the standing refusals: no hash instruction, no per-flow data-plane state, no
replication with per-copy processing, no queue signals, no mid-packet splice.

## Decision: Jool SIIT

[Jool](https://github.com/NICMx/Jool) (GPL-2.0, NIC Mexico, maintained since
~2010) is the reference open-source SIIT/NAT64 implementation for Linux —
stateless IPv4↔IPv6 translation per RFC 7915/6052, the mechanism underneath
SIIT-DC and (with stateful NAT64) 464XLAT. Why it won, criterion by criterion:

- **Notable**: the standards-reference implementation of an IETF-standardized
  mechanism, in production wherever v6-only networks meet the v4 internet.
- **Tests**: the *only* candidate found with true golden frame-level fixtures:
  the graybox suite (`test/graybox/test-suite/`) holds **262 raw `.pkt`
  input/expected pairs, compared byte-for-byte**, organized by RFC 7915
  section. Implementation-agnostic raw bytes — replayable through any
  frame-in/frame-out harness. (Fixtures are GPL-2.0 data; acquisition must
  respect the repo's no-GPL-in-tree boundary.)
- **Realistic**: the best fit of ~25 candidates. The SIIT core is stateless by
  design, triggers zero refusals, and demands zero unbuilt ISA features. The
  v4→v6 header swap is a net +20B prepend (inside the 32B headroom — finally a
  real-world application exercising the head-delta doctrine beyond nanukproto);
  address translation is bit ops (RFC 6052 prefix embed/extract) plus
  exact-match EAMT entries; checksums are CSUM plus the RFC 1624 incremental
  patch idiom.
- **Dataplane-relevant**: SIIT/NAT64 is a canonical XDP-class workload.
- **Simple**: ~30–60 packet-path ops, everything in the first ~80 bytes.
  Teaching density is high: v4/v6 header duality, pseudo-header checksums,
  address-family transition, and "conformance test = RFC section" as a
  structure.

Scoped subset (recognizably Jool, honestly reduced): EAMT-exact + RFC 6052
addressing, UDP/TCP (+ ICMP echo, needed for any ping demo), no fragments, and
ICMP *error* translation (rewriting the embedded inner packet) deferred —
skipped fixtures stay labeled by RFC section.

## Ranked shortlist (both surveys merged, simplicity-weighted)

| # | Candidate (subset) | Notable | Tests reusable | Feasible today | Education | Simplicity | Blocker / cost |
|---|---|---|---|---|---|---|---|
| 1 | **Jool SIIT** (EAMT-exact + 6052, UDP/TCP) | med-high | **262 raw golden `.pkt` pairs** (GPL-2.0) | **yes — zero refusals, zero new ISA** | very high | ~30–60 ops | GPL fixture acquisition needs care |
| 2 | MPLS LSR (VPP `src/vnet/mpls/`, swap/push/pop) | high (MPLS itself) | good — `test_mpls.py` scapy in/out, Apache-2.0 | yes — exact-match-native, 4B push/pop | high | **~10–20 ops (smallest)** | protocol subsystem, not a named app |
| 3 | XDP DNS responder (NLnet XDPeriments "dns-says-no") | med (famous blog series, 50★ repo) | **none — hand-author goldens** | yes — in-place rewrite + csum patch | very high (only L7 candidate) | ~25–40 ops | fails the test-leverage criterion |
| 4 | VPP NAT44 *static* subset | high ("NAT") | good — scapy tests, Apache-2.0 | static only; dynamic = state refusal | high (RFC 1624 star idiom) | ~25–40 ops | visibly reduced vs "real NAT" |
| 5 | xdp-filter (xdp-tools ACL) | high (in distros) | behavioral scripts only | yes; counters are the planned T2 | medium (thin) | ~10–20 ops | exercises little of Nanuk |
| 6 | katran stateless subset (GUE encap) | very high (5.3k★, Meta prod) | 100+ golden cases (GPL-2.0) | needs **hash instruction**; churn only statistical without LRU | high | ~8–10 ops but heavy | fixture fidelity pins Maglev hash + prime ring (modulo) |
| 7 | glb-director (GitHub, BSD-3) | high | **official pcap-in/pcap-out mode** | needs **SipHash specifically** | high | 5 ops + SipHash | irreducible crypto in the datapath |
| 8 | Beamer (NSDI'18 stateless LB) | paper high, repos dead | none | needs any-hash only; cleanest LB fit | very high per line | ~5 ops | no tests, no living project |
| 9 | VPP SRv6 End/End.DX4 | high (RFC 8986) | good — scapy, Apache-2.0 | needs **register-indexed load**; encap needs >32B headroom | high | ~20–35 ops | two capability gaps |
| 10 | Tunnel gateways (VXLAN/Geneve/GRE/IPIP, VPP) | high | good — per-plugin scapy | decap yes; **VXLAN encap needs 50B headroom** (IPIP's 20B fits) | high | ~15–30 ops | famous mode blocked by headroom |

Evaluated and passed over (reasons): DPDK l3fwd (no functional goldens; famous
mode needs LPM), OVS microflow subset (fame ≫ fit; tests entangled with OVS
tooling; multi-output = replication refusal), Gatekeeper DDoS (per-flow state +
LPM), Suricata XDP bypass (data-plane flow-table writes), dnsdist/Knot-XDP
(L7 proxy state / full server), Cilium LB (conntrack structural), IPVS/DPVS
(conntrack is the design), loxilb (conntrack core), Polycube (unmaintained),
Maglev (paper only), SwitchML/NetCache (aggregation state, replication, hash),
P4-tutorials/xdp-tutorial (already our benchmark corpora; toy-tier as apps).

## ISA-extension implications (what would unlock what)

Findings worth keeping even though no extension is needed for SIIT:

- **One hash instruction** (`hash rd, hdr, off, len`, jhash/CRC-class) unlocks
  the entire L4 LB family (katran subset, Beamer, ECMP anywhere). It is the
  single most valuable not-yet-taken instruction, and an LB demo is exactly
  the demand that would justify overturning that refusal. Byte-for-byte katran
  fixture replay additionally wants their exact hash + prime-modulo ring;
  Beamer accepts any hash. Nanuk's current ALU cannot synthesize a hash (no
  right shift, no multiply; Toeplitz-by-iteration busts the step budget).
- **Headroom is the binding constraint on encap gateways**: VXLAN needs 50B,
  GRE-over-Eth 38B, SRv6 T.Encaps ≥64B — all exceed 32B. Headroom is a core
  parameter, so this is a knob not a redesign, but it is a *contract* change.
  IPIP/6in4 (20B) fits today.
- **Register-indexed loads** (`ld rd, hdr, roff`) unlock SRv6 `End`
  (segment[SL] at base + SL·16). Currently offsets are immediate-only.
- **LPM (planned T3)** gates everything router-flavored (l3fwd's famous mode,
  general EAMT prefixes beyond exact entries, VIP prefixes).
- **Counters (planned T2)** are what xdp-filter wants for per-rule stats.
- **The per-flow-state refusal is cheap**: it cleanly excludes the
  conntrack/dynamic-NAT/DDoS-rate-limit family, and every surveyed family has
  a respectable stateless member. No candidate made a case for overturning it.

## Future-demo shortlist (if we ever want a second one)

1. **MPLS LSR** — cheapest possible add; exact-match-native; pairs naturally
   with the existing `mpls_sp` parser example; Apache-2.0 tests.
2. **XDP DNS responder** — maximum charm per line ("the switch answers DNS"),
   L7 story the suite otherwise lacks; costs hand-authored goldens.
3. **katran or Beamer** — *the* justification vehicle for a hash instruction,
   if that capability ever earns its way in.

## Sources

Jool: <https://github.com/NICMx/Jool>, graybox suite under
`test/graybox/test-suite/` (branch `main`), intro
<https://nicmx.github.io/Jool/en/intro-jool.html>. VPP (MPLS/NAT44/SRv6/tunnel
plugins + `test/test_*.py`): <https://github.com/FDio/vpp>. xdp-tools:
<https://github.com/xdp-project/xdp-tools>. XDPeriments:
<https://github.com/NLnetLabs/XDPeriments> and
<https://blog.nlnetlabs.nl/journeying-into-xdp-part-1-augmenting-dns/>.
katran fixtures:
<https://github.com/facebookincubator/katran/tree/main/katran/lib/testing>.
glb-director tests:
<https://github.com/github/glb-director/tree/master/src/glb-director/tests>.
Beamer: <https://github.com/Beamer-LB>,
<https://www.usenix.org/conference/nsdi18/presentation/olteanu>. Maglev:
<https://www.usenix.org/conference/nsdi16/technical-sessions/presentation/eisenbud>.
Gatekeeper: <https://github.com/AltraMayor/gatekeeper>. OVS:
<https://github.com/openvswitch/ovs>. DPDK l3fwd:
<https://doc.dpdk.org/guides/sample_app_ug/l3_forward.html>. loxilb:
<https://github.com/loxilb-io/loxilb>. DPVS: <https://github.com/iqiyi/dpvs>.
