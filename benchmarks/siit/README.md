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

## Status (Plan A + Jool replay landed)

**95** dispositioned RFC clauses · **70** committed vectors across 8 groups ·
**124** Jool graybox fixtures replayed. All four legs landed. The 70 vectors
pass byte-for-byte on all four software levels (reference, interp, ISS, golden
Sail emulator) **and** through the Amaranth RTL core in cosim; PP symex
enumerates 26 feasible parser paths, each with a witness reproduced on both
interp and the emulator. The Jool graybox replay (leg 4) classifies all 124
fixtures: **22 pass**, **2 divergence**, **100 out-of-scope**, 0 unclassified
(see [`jool-replay.md`](jool-replay.md)).

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

## Where the artifacts land

- **Vectors:** [`vectors/`](vectors/) (committed JSON+hex).
- **Plan B — Jool replay:** fetch/harness under this directory; Jool clone in
  gitignored `third_party/` (never committed). Divergence counts are published
  in [`audit.md`](audit.md).
- **Plan C — demo tiers:** browser first — `siit` joins the playground program
  selector (deep link `?program=siit`) with v4/v6 preset packets, reusing the
  existing before/after frame view. SimBricks second — a new scenario in
  [`../e2e/`](../e2e/): v4-only guest ↔ `nanuk_switch` running siit ↔ v6-only
  guest, beats being ping across families then iperf UDP/TCP.
