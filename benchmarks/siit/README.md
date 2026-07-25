# SIIT — stateless IPv4↔IPv6 translation

Nanuk's first application demo: a stateless IPv4↔IPv6 translator
(RFC 7915 + RFC 6052 addressing + RFC 7757 explicit mappings) running on the
Nanuk core, conformance-tested **from the RFC itself** and cross-validated
against Jool as an independent-interpretation oracle.

We build **SIIT, not a port of Jool** — the RFC is the spec, our semantics stay
sovereign where it leaves choices, and every such choice is recorded in the
audit. The artifact is the **SIIT translator**, never "NAT64": stateful NAT64
(RFC 6146) is a standing architectural refusal. Stateless SIIT is the CLAT half
of 464XLAT (the translator in every phone on a v6-only carrier) and SIIT-DC in
datacenters — that is the deployment hook.

Design: [`docs/superpowers/specs/2026-07-18-siit-demo-design.md`](../../docs/superpowers/specs/2026-07-18-siit-demo-design.md).
Plan: [`docs/superpowers/plans/2026-07-18-siit-a-core.md`](../../docs/superpowers/plans/2026-07-18-siit-a-core.md).

## Status (Plans A, B, C all landed)

**95** dispositioned RFC clauses · **70** committed vectors across 8 groups ·
**124** Jool graybox fixtures replayed. All four conformance legs landed, and
both demo tiers are live. The 70 vectors pass byte-for-byte on all four
software levels (reference, interp, ISS, golden Sail emulator) **and**
through the Amaranth RTL core in cosim; PP symex enumerates 26 feasible
parser paths, each with a witness reproduced on both interp and the emulator.
The Jool graybox replay (leg 4) classifies all 124 fixtures: **22 pass**,
**2 divergence**, **100 out-of-scope**, 0 unclassified (see
[`jool-replay.md`](jool-replay.md)). The playground runs SIIT live in the
browser (`?program=siit`, 5 presets read from the committed vectors); the
SimBricks scenario clears 4 beats — ping across address families 10/10, an
iperf UDP leg reconciled per-datagram at three layers, a TTL=1 negative
gate, and **real kernel TCP terminated across the address-family boundary**
(the once-recorded UDP-surplus open question is resolved, and iperf TCP
graduated from future work to a passing beat — see the demo-tier section).

## Four legs, strongest claim last

1. **RFC requirements audit** — [`audit.md`](audit.md). **Landed.** RFC 7915
   §1/§4/§5 (plus the delegated RFC 6052/7757 addressing and blanket §6–§11
   rows) walked clause by clause — **95 dispositioned clauses**, each
   tested / deferred(trigger) / refused(rationale) / not-a-requirement. This is
   the scope ledger and the book-chapter seed. Vectors cite its stable IDs
   (e.g. `7915-4.1-tos`) in their `rfc` field.
2. **Executable spec + generated vectors** — **Landed.** A reference translator
   (`sw/python/nanuk/testkit/siit_ref.py`, stdlib-only) is the oracle. A
   combinatorial generator (direction × protocol × field variation) runs every
   input through it and emits **70 committed, scapy-free vectors** under
   [`vectors/`](vectors/) (see counts below). Byte-exact, no exception masks —
   we control all nondeterminism (fragment-ID policy is fixed by decision).
3. **In-house differential replay** — **Landed.** Every vector through the
   golden emulator, ISS, and interp; all levels agree with each other **and**
   with the reference translator, byte-for-byte on the output frame. RTL cosim
   joins over the siit corpus (all 70 pass first try against the chained ISS
   oracle); symex enumerates the parser's program paths (26 feasible, each with
   a witness) and its witness packets join the corpus.
4. **Jool graybox replay (independent-interpretation oracle)** — **Landed.**
   The only leg that can catch a shared misreading of the RFC, since legs 1–3
   are authored from one reading. A pinned-commit fetch clones Jool into
   gitignored `third_party/` (**zero GPL bytes in our tree**); the harness
   ([`jool_replay.py`](../../sw/python/nanuk/testkit/jool_replay.py)) extracts
   `.pkt` pairs, mirrors Jool's real `/40` pool6 + `/24<->/120` EAMT config
   into a reference `SiitConfig`, applies Jool's own byte-exception masks, and
   classifies every fixture as pass / divergence / out-of-scope. Results in
   [`jool-replay.md`](jool-replay.md): **124 fixtures — 22 pass, 2 divergence,
   100 out-of-scope, 0 unclassified.** The one genuine send-vs-send byte
   divergence is always-DF=1 (`7915-5.1-df`); the out-of-scope set is
   fragmentation, ICMP-error translation/generation, extension headers,
   forward-all, and one hairpin (`7757-hairpin`), each cited to its audit row.
   Divergences are documented findings, not failures. The replay tests are
   gated on `NANUK_JOOL=1` + the clone (CI does not fetch).

   **Program vs reference scope.** These are **reference-level** claims: the
   reference translator implements RFC 6052 all six prefix lengths and RFC 7757
   prefix EAMT, so it expresses Jool's config exactly. The Nanuk **program**
   (hand asm + twins + committed vectors) implements RFC 6052 `/96` +
   EAMT-exact only, so program-level conformance covers the subset of fixtures
   whose config that subset expresses; the reference carries the rest. The asm,
   twins, and vectors are unchanged by this leg.

## Vector groups

Eight groups, per the [plan schema](../../docs/superpowers/plans/2026-07-18-siit-a-core.md):
`udp46` `udp64` `tcp46` `tcp64` `icmp46` `icmp64` (the six translate-and-send
matrices, 6 vectors each = 36), `edge` (addressing / options / boundary, 15),
and `negative` (one vector per drop-verdict reason plus ledger-order overlaps,
19) — **70 vectors** total. Each vector is
`{"name", "rfc", "dir", "in", "verdict", "out", "why"}` — the `rfc` field cites
an audit ID; `why` (on drops) is a reference drop-reason string. A vector cites
one representative audit ID; a `tested(group)` row is exercised by the group's
byte-exact frame (or drop-reason) assertions even when no vector cites its exact
ID — see the vector citation model in [`audit.md`](audit.md).

## Regenerating the vectors

Vectors are generated-but-committed (the `presets.json` precedent):

```
cd sw/python && uv run --no-sync python ../../benchmarks/siit/gen_vectors.py
```

`gen_vectors.py` (Task 3) drives `siit_ref.translate()` over the combinatorial
input space and writes `benchmarks/siit/vectors/<group>.json`. Regenerate and
commit whenever the reference translator or the input matrix changes; CI checks
the tree is clean.

## Demo tiers (Plan C) — landed

**Playground.** `siit` is a program-selector entry (deep link
`?program=siit`), composed like `map_l2fwd`: the SIIT parse-side twin feeding
the SIIT match-action twin, both running live in the browser via the same
Pyodide wheel the rest of the playground uses. Five presets —
`udp46_len25_ttl64`, `udp64_len25_ttl64`, `edge_eamt_dst_46`,
`icmp46_len25_ttl64`, `neg_v4_ttl_expired` — are read straight from the
committed vectors, not generated with scapy. Zero ISA/Sail/RTL/vector/asm
changes; the only cross-cutting fix was a `render_map_ir` gap (no `bin_op`
case — SIIT is the first MAP to use the ISA v0.1 reg-reg ALU) that would have
desynced the IR/asm panes had it shipped unnoticed.

**SimBricks.** A v4-only QEMU guest and a v6-only QEMU guest either side of
`nanuk_switch` running `examples/siit/{parse,translate}.asm` over the
DEMO_SIIT table plane. Four beats, all switch-verified (counters, not client
self-reports):

| beat | result |
|---|---|
| ping (both translations, round trip) | **10/10**, 0% loss |
| iperf UDP | all paced data datagrams delivered exactly once through the translator (receiver log: zero duplicate seqs, positives contiguous); switch-side count reconciles ≥0.9× of iperf's own send count, and the run's frame books close exactly (see below) |
| TTL=1 negative gate | **12/12** loss; switch `dropped=12` |
| iperf TCP (kernel-terminated) | **384 KBytes at ~250 Kbits/sec**, both endpoints agreeing; one TCP connection whose two ends live in different address families — the v4 client sees `198.51.100.2 → 192.0.2.1`, the v6 server sees the same connection as `64:ff9b::c633:6402 → 2001:db8:1::c001`; every SYN/data/ACK/FIN crossed the translator (grew=283 v4→v6, shrunk=246 v6→v4, `core_err=0`) |

**The UDP surplus, resolved (2026-07-25).** The switch-translated count
runs ~3–4× *above* what the iperf client reports sending — the opposite
direction from an overclaim — and this was recorded as an unexplained loose
end at the arc's merge. It is now confirmed, with per-frame evidence from
three independent layers (guest driver TX counter, switch counters, and a
per-datagram receiver log carrying iperf's own application sequence
numbers), to be iperf2's unanswered UDP close phase: with no real iperf
server to ack it, the client emits a barrage of ~200 **distinct**
negative-seq FIN datagrams before giving up. Nothing on the path duplicates
or invents frames — conservation closes exactly (guest TX == switch
translated + switch rx-queue-full drops, zero unexplained), and the beat now
**gates** on the receiver log (zero dups, all paced data datagrams
delivered). Details in the lab-notes addendum.

**How TCP was unlocked (2026-07-25).** The SimBricks base guest kernel ships
`CONFIG_IPV6=n` — no kernel IPv6 stack at all — which is why the v6 side
originally ran a userspace `AF_PACKET` ICMP-echo responder (enough for ping
and UDP, not a TCP peer). `../e2e/build_guest_kernel.sh` now rebuilds the
image's own kernel (same 5.15.93 source, same gem5 patch, same config) with
`CONFIG_IPV6=y` — plus `CONFIG_E1000=y`, because the disk image's module
tree belongs to the stock build and a rebuilt kernel that leans on it boots
with no NIC. The TCP beat boots that kernel (mounted over the image's
bzImage for that beat only; beats 1–3 still run the stock kernel and the
responder), the v6 guest terminates a real kernel TCP/IPv6 iperf server, and
since ND cannot cross the translator (non-echo ICMPv6 is refused), both
sides pin static neighbors — the v6 side toward the RFC 6052-mapped address
of the v4 host.

Full writeup, including the i40e_bm zero-frame NIC bug (worked around with
E1000, reportable upstream to SimBricks) and the `-x` middlebox flood flag,
is in [Part C of the lab notes](../../docs/notes/2026-07-18-siit-core-lab-notes.md).

## Where the artifacts land

- **Vectors:** [`vectors/`](vectors/) (committed JSON+hex).
- **Plan B — Jool replay:** fetch/harness under this directory; Jool clone in
  gitignored `third_party/` (never committed). Divergence counts are published
  in [`audit.md`](audit.md).
- **Plan C — demo tiers:** browser — `siit` in the playground program
  selector (`web/src/programs/siit.py`, `web/py/bridge.py`). SimBricks —
  [`../e2e/nanuk_demo_siit.py`](../e2e/nanuk_demo_siit.py) and
  [`../e2e/run_siit.sh`](../e2e/run_siit.sh).
