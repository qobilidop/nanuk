# SIIT application demo — design

Date: 2026-07-18. Status: approved direction; this doc is the spec for the
arc. Companion survey (how this application was chosen, full candidate field):
`docs/notes/2026-07-18-demo-application-survey.md`.

## Goal and framing

Build Nanuk's first **application demo**: a stateless IPv4↔IPv6 translator
(SIIT, RFC 7915 + RFC 6052 addressing + RFC 7757 explicit address mappings)
running on the Nanuk core, conformance-tested from the RFC itself and
cross-validated against Jool.

Framing decision (Bili, 2026-07-18): **we build SIIT, not "a port of Jool."**
The RFC is the spec; our clause-by-clause requirements audit plus an
executable-spec-generated vector suite define "done"; our semantics stay
sovereign where the RFC leaves choices. Jool is an *independent-interpretation
oracle*, not an identity. The deployment story is the hook: stateless SIIT is
the CLAT half of 464XLAT — the translator running in every phone on a
v6-only carrier network — and SIIT-DC in datacenters.

Audience ranking (decided earlier in this arc): students/educators first,
networking community second, general public third. Delivery: browser tier
first, SimBricks tier second, guide chapter later.

Naming caution: stateful **NAT64 (RFC 6146) is out permanently** — per-flow
session state is a standing architectural refusal. The honest name for what
we build is SIIT. Docs must not say "NAT64" for the thing Nanuk does.

## Why this application (from the survey)

- Only surveyed candidate with zero refusals triggered and zero unbuilt ISA
  features needed: stateless by design, exact-match EAMT, +/−20B head delta
  inside the 32B headroom, CSUM + RFC 1624 incremental patching.
- First real-world exercise of the headroom/head-delta doctrine beyond
  nanukproto.
- Teaching density: v4/v6 header duality, pseudo-header checksums,
  address-family transition, RFC-to-test-suite methodology.
- Precedent that address translation belongs on programmable dataplanes:
  P4-NAT64 (RIPE85). Nanuk's twist: a formally specified ISA underneath.

## Scope

One program (PP + MAP, eDSL + asm twins) in `examples/siit/`, handling both
directions; PP classifies the family, MAP branches.

In scope, first landing:

- IPv4→IPv6 and IPv6→IPv4 for **UDP, TCP, and ICMP echo request/reply**
  (echo keeps the ping beat alive; type 8/0 ↔ 128/129 remap plus
  pseudo-header checksum adjustment — this is NOT the deferred ICMP *error*
  translation).
- Addressing: **RFC 6052 prefix embed/extract** (pool6 prefix baked into the
  program as constants, per the standalone-examples doctrine; default the
  well-known prefix 64:ff9b::/96) **plus EAMT explicit mappings** as
  exact-match table entries.
- v4→v6: head grows 20B net; fresh 40B IPv6 header written over the old
  IPv4 header region + headroom (payload-len = total-len − 4·IHL, next-header
  = proto, hop-limit = TTL − 1). v6→v4: head shrinks 20B net; fresh IPv4
  header incl. full header checksum via CSUM. (Delta sign conventions belong
  to the plan.) L4 checksums patched incrementally
  (pseudo-header delta; RFC 1624 idiom).
- IPv4 options: not translated (per RFC); packets with options are still
  translated (payload found via IHL). Handled, not deferred.
- Totality: every packet reaches an explicit verdict. Non-v4/v6, runts,
  unsupported protocols → explicit pass-or-drop decisions recorded in the
  audit.

Decided edge case: **IPv4 UDP with checksum 0 is dropped** (RFC-sanctioned
option; computing the mandatory v6 checksum needs the full payload, which can
exceed the 256B window — totality-as-guard, documented in the audit; a Jool
divergence if their fixtures assume the forwarding config).

Deferred (parked with triggers):

- **ICMP error translation** (embedded-packet rewrite). Trigger: wanting
  Jool's hand-crafted `b*` fixture groups, or a traceroute-through-Nanuk
  story. Roughly doubles program complexity; the inner packet lies within the
  256B window, so it is feasible when wanted.
- **Fragmentation** (fragment-header handling, DF/PMTU semantics). Trigger:
  same audit sections; needs design attention around window limits.
- **TAYGA live differential** as a second external oracle. Trigger: wanting
  an oracle for cases Jool's static fixtures don't cover.
- **Performance rung** via RFC 8219 benchmarking methodology. Trigger:
  paper/PPA work.

## Test architecture (the heart of the demo)

Four legs, strongest claim last:

1. **RFC requirements audit** — `benchmarks/siit/audit.md`, Jool-7915-README
   genre meets `benchmarks/coverage.md` genre: RFC 7915 clause by clause,
   each classified tested / out-of-scope-first-landing (with trigger) /
   refused (with rationale) / not-a-requirement. This is the book-chapter
   seed and the scope ledger.
2. **Executable spec + generated vectors** — a reference SIIT translator
   (~200 lines) in `nanuk.testkit` (scapy stays dev-only by construction;
   testkit never ships). A combinatorial generator (direction × protocol ×
   field variations, pktgen-style) runs every input through the reference
   translator and emits committed vector files under `benchmarks/siit/`
   (generated-but-committed, presets.json precedent, regen script). Our
   fixtures are byte-exact with no exception masks — we control all
   nondeterminism (fragment-ID policy fixed by decision).
3. **In-house differential replay** — every vector through golden emulator,
   ISS, and interp; all levels must agree with each other AND with the
   reference translator, byte-for-byte on the output frame. RTL cosim leg in
   `hw/amaranth` over the siit corpus (new-opcode lesson does not apply — no
   new opcodes — but application corpora join cosim regardless). Symex
   enumerates program paths and invents witness packets; witnesses join the
   corpus (spec-coverage from the generator × program-coverage from symex).
4. **Jool graybox replay (independent-interpretation oracle)** — the only
   check that can catch a shared misreading of the RFC, since legs 1–3 are
   all authored from one reading. Pinned-commit fetch script clones Jool into
   gitignored `third_party/` (recon-clone convention; **zero GPL bytes in our
   tree**); harness extracts `.pkt` pairs (raw L3 — wrap in our Eth framing),
   mirrors the suite's Jool config (pool6, EAMT) into our tables/constants,
   applies their byte-exception masks (IPv4 ID randomness, ToS quirk), filters
   to in-scope groups (pktgen udp/tcp/icmp-ping now; `7915/` letters join as
   deferrals land), and reports **pass / divergence / out-of-scope** per
   fixture. Divergences are documented findings in the audit with rationale,
   not failures. CI: network-fetch with cache; replay job may be
   skippable offline like NANUK_COSIM.

## Demo tiers

- **Browser (first)**: `siit` joins the playground program selector with v4
  and v6 preset packets; deep link `?program=siit`. The money shot is the
  existing before/after frame view: same payload, different internet. No new
  UI panels required for the first landing.
- **SimBricks (second)**: new scenario in `benchmarks/e2e/` — v4-only QEMU
  guest ↔ `nanuk_switch` running siit ↔ v6-only guest; beats: ping across
  address families (needs ICMP echo), then iperf UDP/TCP. Guest netcfg (static
  routes toward the translator, EAMT-mapped addresses) lives in the scenario.
- **Guide chapter (later)**: "from RFC to conformance suite" — the audit,
  the executable spec, the divergence log as narrative.

## Program-level notes (for the plan, not re-decided here)

- PP: reuse l2l3l4-style parsing; PP verdict/md tells MAP the family and
  header offsets; md header-present bitmap convention as established.
- MAP: EAMT tables (v4→v6 and v6→v4 directions; table ids and key layouts
  decided in the plan), 6052 embed/extract via ld/st + ALU, checksum work via
  CSUM + incremental patch, SEND with ∓20 delta.
- Step budget and 4-GPR pressure look comfortable (~30–60 ops path) but the
  plan must include canary assertions like the benchmark suite's.
- Fixture framing: Jool `.pkt` files are L3-only; our harness owns an
  Ethernet framing convention for replay (constant MACs from testkit).

## Success criteria

1. Audit exists and every RFC 7915 clause is dispositioned.
2. Generated vector suite passes byte-exact on emulator, ISS, interp, and
   RTL cosim (all agreeing with the reference translator).
3. Jool replay runs over every in-scope fixture; each result is pass or a
   documented divergence with rationale; the counts are published in the
   audit. (Divergences are findings, not failures — but none may go
   unexplained.)
4. Playground runs siit end-to-end with presets; deep link works.
5. SimBricks beat: ping v4-guest↔v6-guest through the translator, then iperf.
6. No ISA, core-interface, or Sail changes anywhere in the arc.

## Non-goals

Stateful NAT64 (refused), ICMP error translation and fragmentation (deferred
with triggers above), multicast, performance claims, P4 frontend tie-ins.
