# Lab notes: playground v2 — asm-level ISS + step-scrubber level-diffing

Date: 2026-07-11 · Spec: `docs/superpowers/specs/2026-07-11-playground-v2-design.md`
· Plan: `docs/superpowers/plans/2026-07-11-playground-v2.md`

## What was built

- **`nanuk-isa`** (`spec/isa/`): the assemblers, encodings, and shared
  `_asm_core` extracted out of `nanuk-spec` into a dependency-free
  package (no scapy, no protobuf), plus the two new **instruction-set
  simulators** (`iss.py`, `iss_map.py`) that decode and execute the
  assembled 32-bit words — the fourth implementation of nanuk semantics
  after the Sail/C golden models, the RTL cores, and the IR interps.
  Reserved encoding bits are enforced (nonzero → ILLEGAL), and both ISS
  record a complete per-step trace (the step budget bounds it).
- **`assemble_with_lines`**: both assemblers also return a word→source-
  line map; word index doubles as instruction index, which later made
  register annotations a plain array lookup (`bindings[pc]`).
- **Interp trace hooks**: `interp`/`interp_map` accept a `trace` list and
  record one event per executed IR event, stamped with the cumulative
  step counter. Because the interps mirror the lowering cost model
  instruction-for-instruction, the step counter is a shared clock: interp
  event N covers ISS steps `(prev.steps_after, steps_after]` exactly.
- **Annotated lowerings**: `to_asm_annotated`/`to_map_asm_annotated`
  return per-instruction `{register: value-name}` snapshots. MAP's
  last-use liveness makes register reuse visible in the UI.
- **Bridge trace API**: every run returns per-step records joining the
  ISS step (pc, asm line, regs as hex strings — 64-bit values overflow
  JS numbers) with the covering interp event (state, IR line, label,
  values), plus a divergence verdict computed in Python. Composed MAP
  runs trace both phases; the PP phase runs against a baked, assembled
  l2l3l4 rig.
- **Debugger strip** under the panes: transport + slider with a phase
  boundary tick, agreement badge (red = jump-to-divergence = "file a
  nanuk bug"), IR/ASM state cards, execution-line highlighting in the
  IR/asm panes via a second CM6 decoration field (independent of hover,
  so scrubbing and hovering never fight), state-fn line in the eDSL
  pane, and the parser-cursor byte highlighted in a packet hex view.

## Verification

- ISS vs golden emulators: corpus + random packets + random-**words**
  decoder fuzz (the fuzz leg exercises illegal/reserved paths against
  the Sail decode's totality), all result fields including `steps`;
  MAP adds frame-byte-identical comparison over all four demos.
- ISS vs interp (`lang/tests/test_iss_parity.py`, pure Python,
  ungated): step-exact across both engines AND the alignment invariant
  at every event boundary. **Passed first try** — the cost-model
  mirroring discipline (interp ↔ lowering, established in the interp
  satellite) paid for itself; there was no divergence to debug.
- Full local gate + cloud CI green; Playwright drive of the built SPA:
  scrub, highlight movement, state-card updates, phase crossing, deep
  link, zero console errors.

## Gotchas caught en route

- The Svelte 5 narrowing gotcha from v1 struck again: `runOut?.kind ===
  'map' && runOut.result…` in an inline `$derived` narrows to `never`;
  use `$derived.by` with a local. (Recorded in the v1 notes; now twice
  confirmed.)
- Playwright strict mode: `.badge` collided between the result view and
  the debugger — scope locators to `.debugger`.
- ERR_PC_RANGE is unreachable from any real program: the step budget
  (256) is smaller than imem (1024 words), so a runaway pc exhausts the
  budget or hits an all-zeros ILLEGAL word first. The ISS still mirrors
  the check order (budget, then pc range, then decode) and a unit test
  drives the machine state directly.
- Step-accounting corner: a budget halt executes nothing (no trace
  record, no interp event); an op that error-halts mid-execution has
  already been counted and gets both. Keeping those symmetric between
  ISS and interp is what makes `steps == len(trace)` hold on every
  path.
- The cursor view is a separate hex `<code>` render, not the textarea —
  you can't highlight inside a textarea. At halt the cursor often
  equals the packet length (fully consumed): that's the payload offset,
  not a bug; the caption says so.
