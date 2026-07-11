# Lab notes: M3 — the MAP learns the language, and the MAT arc closes

*2026-07-11. Covers the MAP language stack (MapProgram IR, eDSL, lowering,
step-exact interpreter, playground) and the close of the match-action arc.*

## Sibling symmetry, third time

The M3 thesis was that the parser's language stack would reuse
shape-for-shape for the MAP, and it did: `MapProgram` proto beside
`Program` (parser consumers untouched — the Terminator oneof grew
`send`/`drop` kinds, with each validator rejecting the other engine's
terminators), `validate_map` beside `validate`, `lower_map` beside
`lower`, `interp_map` beside `interp`, `MapProgram`/`MapStateCompiler`
beside `Parser`/`StateCompiler`. Every piece landed green on its first or
second run because the pattern was already proven twice.

Two genuinely new design moves:

- **Lookup is an op, not a terminator.** It mirrors the fused
  LOOKUP-branch-on-miss instruction: hit defines a value and continues in
  the same state; miss transfers control. It's the one op with control
  flow, and the existing no-cross-state-values rule already guarantees the
  miss state can't see stale values.
- **The eDSL binds headers to PP hdr ids** (`mp.header(eth, hdr_id=0)`),
  making the PP→MAP contract visible in the program text, and enforces the
  byte-machine rule at the field level: `ipv4h.version` is a CompileError
  (4-bit field), `ipv4h.ttl` compiles. The engines' split is now a type
  error.

## The lowering grew real register allocation

The parser lowering's no-free discipline (a value lives to end-of-state)
was fine for parsing, but the tunnel push materializes an 11-constant
outer header — straight-line MAP code is register-hungrier. `lower_map`
does last-use liveness: a register frees after its value's final
consumer, and since sources are captured before results allocate,
`addi r0, r0, -1` and `lookup r0, 0, r0, miss` emerge exactly like
hand-written code (rd == rs is well-defined: key read before result
write). The parser lowering stays as-is — its programs never needed it,
and its simplicity is a teaching feature.

`interp_map` mirrors the lowering's cost model instruction-for-
instruction, and the differential rig (interp vs `run_map` of the
assembled lowering) checks ALL fields including `steps` — it passed with
zero divergences over the demos, error paths, and 20 random
program/packet/table trials.

## Parity: the eDSL earns the demos

All four demo programs (l2fwd, ttl, tunnel push, tunnel pop) rewritten in
the eDSL are behaviorally identical to the hand-written .asm over the M1
corpus + tunnel frames (full MapResult diff except steps — instruction
schedules differ; e.g. the eDSL ttl program loads TTL twice where the
hand asm juggles registers). The hand-written .asm files stay as the
ISA-level teaching copies.

## Playground

`build_map_ir()` in the source selects the MAP path: MAP IR rendering
(with lower_map-mirrored asm emission counts, so the three-pane provenance
highlighting works unchanged), and `run_packet` becomes a *composed* run —
the baked l2l3l4 parser gates the packet, then `interp_map` executes with
a demo FDB in the playground control plane. The result panel shows egress
ports, head delta, and the transmitted frame with prepended headroom bytes
highlighted; parser-refused packets surface as "gated." A program selector
joined the header (l2l3l4 / nanukproto / l2-forward-MAP), and the deep-link
contract extends: `/play/?program=map_l2fwd&preset=plain_ipv4_udp`.

scapy boundary held: the bridge's table objects are a local three-field
class; nanuk_spec still never ships.

## Scoreboard (arc close)

420 pytest + 12 ctest + 2 Sail type-checks green across the repo; Pyodide
integration test covers the MAP path; playground builds. The MAT arc is
complete: **PP + MAP, spec → emulator → assembler → RTL → SimBricks →
eDSL → IR → interpreter → playground**, every layer differentially tested
against the layer below.

## Next confident moves (mandate: continue until confidence runs out)

Symbolic executor satellite (entry criterion long met; interp/interp_map
are the chassis; parsers and MAP programs are bounded-loop bitvector
programs — p4v/p4pktgen precedent). NOT autonomous: MLIR (parked,
unconvinced), paper timing (venue watch), Tiny Tapeout (deferred).
