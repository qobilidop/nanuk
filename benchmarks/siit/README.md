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

## Four legs, strongest claim last

1. **RFC requirements audit** — [`audit.md`](audit.md). RFC 7915 §1/§4/§5 walked
   clause by clause, each dispositioned tested / deferred(trigger) /
   refused(rationale) / not-a-requirement. This is the scope ledger and the
   book-chapter seed. Vectors cite its stable IDs (e.g. `7915-4.1-tos`) in their
   `rfc` field.
2. **Executable spec + generated vectors** — a reference translator
   (`sw/python/nanuk/testkit/siit_ref.py`, stdlib-only) is the oracle. A
   combinatorial generator (direction × protocol × field variation) runs every
   input through it and emits committed, scapy-free vector files under
   [`vectors/`](vectors/). Byte-exact, no exception masks — we control all
   nondeterminism (fragment-ID policy is fixed by decision).
3. **In-house differential replay** — every vector through the golden emulator,
   ISS, and interp; all levels must agree with each other **and** with the
   reference translator, byte-for-byte on the output frame. RTL cosim joins over
   the siit corpus; symex enumerates program paths and its witness packets join
   the corpus.
4. **Jool graybox replay (independent-interpretation oracle)** — the only leg
   that can catch a shared misreading of the RFC, since legs 1–3 are authored
   from one reading. A pinned-commit fetch clones Jool into gitignored
   `third_party/` (**zero GPL bytes in our tree**); the harness extracts `.pkt`
   pairs, mirrors Jool's pool6/EAMT config into our tables, applies their byte
   masks, and reports pass / divergence / out-of-scope per fixture. Divergences
   are documented findings in the audit (see its "Divergences" list), not
   failures.

## Vector groups

Eight groups, per the [plan schema](../../docs/superpowers/plans/2026-07-18-siit-a-core.md):
`udp46` `udp64` `tcp46` `tcp64` `icmp46` `icmp64` (the six translate-and-send
matrices), `edge` (addressing / options / boundary), and `negative` (every
drop-verdict reason). Each vector is
`{"name", "rfc", "dir", "in", "verdict", "out", "why"}` — the `rfc` field cites
an audit ID; `why` (on drops) is a reference drop-reason string.

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
