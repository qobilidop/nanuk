# Playground v2: assembly-level ISS + live level-diffing

Date: 2026-07-11
Status: approved (UX + architecture chosen interactively; UI details and
testing delegated to Claude by mandate)

## What v2 is

The v1 playground shows three synchronized *static* views of a program
(eDSL | IR | asm) with provenance highlighting, and runs packets against
the IR interpreter. v2 makes execution itself inspectable: run a packet,
then **scrub through the execution step by step, watching the same
moment at two levels at once** — the IR interpreter and a new Python
**instruction-set simulator (ISS)** executing the assembled 32-bit
words. The two levels are genuinely independent implementations, so the
live diff between them is honest: a divergence is a real compiler or
ISS bug, not decoration.

Chosen UX: **step-scrubber debugger**. Execution is total and bounded
by the step-budget watchdog, so both levels record a *complete* trace
up front; the UI scrubs a recorded trace — no interactive Python
stepping, no async debugger protocol.

Scope: **both engines** (parser ISA and MAP ISA), including the
composed PP→MAP pipeline run the playground already performs.

## Why an ISS (and why at the encoding level)

The ISS is the **fourth implementation** of Nanuk semantics (Sail/C
emulator, Amaranth RTL, IR interp, ISS) and slots into the existing
mirror-with-tripwire methodology:

- It executes **assembled 32-bit words**, not asm text and not IR — the
  same artifact the emulator and RTL consume. That makes it the
  browser-runnable stand-in for the golden model (the C emulator cannot
  run under Pyodide).
- Its tripwire is a **differential CI test vs `nanuk-emu`** over the
  demo corpus plus random packets, the same pattern the RTL cosim uses.
- Against the IR interp it enables the flagship feature: two
  independent executions of the same program, diffed step by step.

## Architecture

### New package: `nanuk-isa` at `spec/isa/`

A new uv project, package `nanuk_isa`, **zero runtime dependencies**
(no scapy, no protobuf). It lives under `spec/` because Sail owns
semantics and encodings; this package is the Python mirror of that
layer and belongs next to its owner.

Moved out of `nanuk_spec` (git mv; all call sites updated; **no
re-export shims**): `_asm_core.py`, `encoding.py`, `asm.py`,
`map_encoding.py`, `map_asm.py`. `nanuk-spec` gains a path dependency
on `nanuk-isa` and keeps only the spec-rig pieces: harnesses, testkit,
pcap I/O (the scapy side). The scapy boundary is untouched:
`nanuk-spec` still never ships.

New in `nanuk_isa`:

- `iss.py` — parser-ISA ISS. Decodes and executes 32-bit words,
  mirroring `exec.sail` exactly: same error codes, same step
  accounting (check-budget-before-execute), same watchdog, all-zeros
  illegal. State: pc, 4 GPRs + zero reg, cursor, hdr_present/offset,
  SMD, steps, verdict/error.
- `iss_map.py` — MAP-ISA ISS. Frame buffer with 32B headroom, signed
  hdr-relative addressing, synchronous LOOKUP against injected
  exact-match tables (same table shape the bridge already builds),
  CSUMUPD, SEND(bitmap, delta) / DROP.
- Both record a **full trace**: per step — pc, asm source line (from
  the assembler's word→line map, a small assembler extension), register
  file, cursor (parser), and effects (hdr/SMD writes; frame writes,
  lookups, delta).

The playground ships **three wheels**: `nanuk-isa`, `nanuk-ir`,
`nanuk-lang`.

No disassembler in v2: the UI always has the asm text and the
word→line map, so the ISS reports line numbers, not re-rendered text.

### Interp trace hook

`nanuk_ir.interp` / `interp_map` grow an **optional trace recorder**
(off by default; existing callers and the symex chassis untouched).
Per executed IR op: op identity (rendered-walk index, which provenance
maps to `ir_line`/`asm_lines`), owning state fn, values written,
cumulative steps.

### Alignment: the step counter is the shared clock

The interp mirrors the lowering's cost model instruction-for-
instruction (the interp satellite's founding design call), so every
interp tick corresponds to exactly one executed asm instruction.
Traces align **by step index** — no fuzzy matching. Zero-cost IR ops
(re-anchor marks) attach to the following step. This alignment
invariant is itself asserted in tests; if the cost model ever changes
(v0.x dispatch accelerator), the parity tests break loudly.

**Divergence detection** compares *architectural* state at each aligned
step — cursor, hdr arrays, SMD, frame bytes and delta (MAP), then the
full final result struct (all 7 parser fields; MAP result including
transmitted frame). Register contents are **display-only, never
diffed**: the value→register correspondence is the lowering's choice,
not semantics.

**Value→register mapping** (display): `lower.py` / `lower_map.py`
export per-op register-binding snapshots (after this op, which GPR
holds which named IR value). MAP's last-use liveness regalloc makes
register reuse visible in the UI. The bridge merges these into the
trace JSON.

### Bridge contract

`run_packet` / the composed MAP run always return traces (budget-
bounded, so small; no pagination, no opt-in flag). The bridge computes
alignment and divergence **in Python** — single source of truth — and
ships a ready-to-render JSON: per-step aligned records + divergence
verdict + value→reg annotations. `types.ts` grows matching types.
Composed runs return a two-phase trace (PP then MAP) with the
PP-verdict gate as the phase boundary; gated runs return the PP trace
plus a gate notice.

## UI

A **debugger strip** below the three panes, shown once a run produces
a trace:

- Transport: ⏮ ◀ ▶ ⏭, a step slider, "step N / M" readout, play
  button (~5 steps/s), ←/→ keys when focused.
- **Agreement badge**: green "levels agree" / red "diverged at step N"
  with jump-to-step; diverged fields highlighted in the state cards.
  (In a healthy build this is always green; red means "file a Nanuk
  bug" and the UI says so.)
- Two state cards: **IR** (current state fn, current op label, named
  values) and **ASM** (pc, current line, r0–r3 annotated with the
  value names they hold, cursor, steps used / budget).
- Composed runs: one slider spanning PP then MAP with a phase tick
  mark; state cards switch content per phase.

Execution highlighting: a second CM6 decoration class (full-line
background, distinct from hover's) marks the executing line in IR and
asm panes; the eDSL pane marks the current state fn's range. The
packet-hex panel highlights the byte under the parser cursor during
the PP phase. The existing hover-provenance machinery is reused via a
parallel "exec" store ({irLine, asmLine, edslRange}); the
scroll-to-highlight discipline (never scroll the pane being pointed
at) carries over.

Scrubber resets to step 0 on each new run; the v1 result view stays
as-is (final outcome always visible), so the scrubber is exploration,
not the only readout.

## Error handling

- ISS error codes are the emulator's, mirrored constants with tripwire
  tests (cross-layer duplication is doctrine, not debt).
- Illegal/undecodable word → same terminal error verdict as the
  emulator, trace preserved up to the fault.
- Bridge failures in trace assembly → existing `BridgeError` shape
  (`runtime`).
- A genuine divergence still renders both traces (that's the debugging
  story), with the red badge.

## Testing

1. `spec/isa` unit tests (dependency-free): encode/decode round-trips
   (moved with the assemblers), per-instruction ISS vectors, watchdog
   and error-code edges.
2. Differential **ISS vs `nanuk-emu`** in the `nanuk-spec` suite
   (which already builds/drives the emulator and owns the scapy
   corpus): demo corpus + random packets, full result contract, both
   engines, composed pipeline included.
3. **ISS vs interp** step-exact parity + alignment invariant in
   `lang/` tests over l2l3l4, nanukproto, and the MAP demos (corpus +
   tunnel packets): same step counts, same architectural state at
   every step, zero divergences.
4. Bridge tests (`web/py`): trace JSON shape, alignment, value→reg
   annotations, gated-run shape.
5. Node Pyodide integration test: trace round-trip through real
   Pyodide + wheels.
6. Playwright drive before shipping (the v1 lesson: drive the real
   page, check boundingBoxes and console): scrub, watch highlights
   move, composed-run phase switch, deep link still works.

CI: `spec/isa` joins the ruff sweep and the pytest matrix;
`build_wheels.sh` adds the third wheel; pages workflow otherwise
unchanged.

## Parked (with triggers)

- Disassembler / raw encoding view in the UI (trigger: an "edit asm
  directly" playground mode, which `nanuk-isa` now makes possible).
- Deep-link `?step=N` (trigger: someone wants to share a mid-trace
  moment).
- MAP-phase byte-touch highlighting in the packet panel beyond the
  parser cursor (trigger: cheap once frame-view highlighting exists).
- Register-value *diffing* against IR values (would require semantic
  value tracking through regalloc; display-only mapping covers the
  pedagogy).
