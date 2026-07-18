# Appendix: The Doctrines

Nanuk accumulated a set of named design rules — the load-bearing decisions the rest
of the book keeps leaning on. This is a one-screen index: each doctrine in two
sentences, with a pointer to the design doc that governs it under
`docs/superpowers/specs/`. They are the vocabulary; the chapters are the arguments.

## Zero-copy / headroom

The packet stays in place: instead of detaching headers into a vector and
reserializing them (the PHV/deparser model), Nanuk edits bytes where they lie,
addressing them by offset, with 32 bytes of headroom and a signed head-delta applied
once at `SEND`. This is why there is no deparser and why length-changing edits (tunnel
push/pop, the SIIT ±20-byte swap) are staged rather than committed until the frame is
sent. *Governing doc:* `2026-07-12-deparser-editor-doctrine.md`.

## Two-processor minimum

Three engines is too many (nothing to reassemble in a zero-copy machine) and one is
too few (a unified ISA carries the cross product of two conflicting machine-state
disciplines); two sibling ISAs is the minimum that makes the PP→MAP stage boundary
*architectural* rather than a software convention. The contrast pairs — bit machine
vs. byte machine, cursor vs. header-relative, read-only vs. read-write, HALT-verdict
vs. SEND-delta — are the curriculum, visible only because there are two ISAs to lay
side by side. *Governing doc:* `2026-07-12-single-isa-doctrine.md`.

## No deparser, by construction

No real target makes the deparser a third programmable processor; in a zero-copy
machine, deparsing degenerates to edit ops (in-place stores plus a head-delta), so
Nanuk builds none. The eDSL gets no deparser construct at all — a construct whose only
job is P4 resemblance fails the education-first test — and the parked P4 frontend
would lower P4 deparser blocks the eBPF way. *Governing doc:*
`2026-07-12-deparser-editor-doctrine.md`.

## Mirror-with-tripwire

Where semantics are duplicated across layers — Sail, interpreter, ISS, RTL; the
encoding mirrored in Sail and Python; a constant baked into both the PP and MAP sides
— the duplication is deliberate and each copy is pinned to the others by a tripwire
test that fails the instant they drift. Cross-layer constant duplication is a
*feature*: the same golden words live in the Sail decode test and the Python encoding
test, and a divergence surfaces as a caught failure rather than a silent
inconsistency. *Governing doc:* `2026-07-12-single-isa-doctrine.md` (which names the
cost and the tripwire that makes the duplication safe).

## Totality and step budgets

Every ISA and IR behavior is defined — out-of-bounds reads, over-depth parses,
illegal instructions, all of it — because you cannot differentially test against an
oracle that shrugs, and parse loops are statically bounded (a 256-step budget, bounded
header stacks). Totality is what makes the golden model, the decoder fuzz, and the
symbolic executor possible at all; bounded iteration is what makes the hardware and
the solver tractable. *Governing doc:* `2026-07-11-nanuk-project-design.md`
(methodology, "total, deterministic semantics" and "bounded iteration by
construction").

## Examples are content; headers are toolchain

Example programs are editor seed text, standalone documents a human reads, never
imported by the runtime; anything a program needs at runtime ships separately as a
library asset. `examples/` lives at the repo root as flat, unpackaged content, the
wheel carries none of it, and a tripwire holds the browser bridge's rig identical to
the example it mirrors. *Governing doc:* `2026-07-11-examples-as-content-design.md`.

## Build-output ownership

Generated artifacts that must be readable or shippable — the conformance vectors, the
baked presets, the generated protobuf binding — are *committed*, produced by a checked-in
regeneration script, and guarded by a CI check that the working tree stays clean after
regeneration. The rule keeps generated data reviewable and diff-able (an empty
`git diff` after regen is the proof a change was backward-compatible) rather than
materialized invisibly at build time. *Governing doc:* `2026-07-13-benchmark-suite-design.md`
(the generated-but-committed corpus, with the `presets.json` precedent from the
playground design).

## The refusal ledger

A binding suite that only ever *adds* requirements will grow Nanuk into a bad Tofino,
so the suite must state, on the record, what it *refuses* — hashing, per-flow state,
ternary/TCAM, queue signals, per-copy processing — each with the programs that would
demand it and the rationale for keeping it out. The negative set is half the coverage
proof, not an appendix: a documented refusal is a design stance, where an undocumented
gap is just a limitation. *Governing doc:* `2026-07-13-benchmark-suite-design.md` (the
negative set); the per-clause application form is `benchmarks/coverage.md` and, for
SIIT, `benchmarks/siit/audit.md`.
