# IR Interpreter + Web Playground — Design

**Date:** 2026-07-11
**Status:** IR interpreter implemented (`compiler/nanuk_ir/interp.py` + differential rigs in `compiler/tests/test_differential.py`, `lang/tests/test_interp_parity.py`). Playground: approved, not started.
**Parent:** [Project design](2026-07-11-nanuk-project-design.md) · [Stage 3 IR plan](../plans/2026-07-11-stage3-ir.md)

## Decision 1: interpret the protobuf IR, not "the eDSL"

Since stage 3 there is nothing else to interpret: the eDSL's state functions
execute once at `build_ir()` time and their entire product **is** the IR —
the IR is the first materialized representation of any nanuk program.
Interpreting at IR level is also right strategically:

- serves every frontend (the future P4 subset included), matching the
  hub-and-spoke architecture;
- gives the IR semantics **independent of the lowering**, which turns
  `interp(IR)` vs `emulate(lower(IR))` into a differential test of the
  compiler — a lightweight translation-validation rig;
- is the chassis the symbolic executor reuses (same interpreter skeleton,
  Z3 expressions instead of concrete values, forking at dispatches);
- fills the only altitude gap: assembly-level execution already has two
  implementations (Sail golden model, RTL); IR-level has zero.

Home: `compiler/nanuk_ir/interp.py` (~200 lines; the IR has five ops and
three terminators). Its semantics must mirror ISA totality: bounds
violations, step/dispatch budgets, all defined.

## Decision 2: show the compiled assembly, godbolt-style

Prior art: **Compiler Explorer (godbolt.org)**. Its key trick is not
displaying assembly but the **hover provenance mapping** — point at a source
line, the assembly it produced lights up. nanuk is unusually well prepared:
IR ops already carry `debug_name` and the lowering already emits provenance
comments (`; eth.dst`); extending IR ops with a source span completes the
chain.

The playground goes one better than godbolt: **three synchronized panes —
eDSL | IR | assembly — which is the project's layer cake made interactive.**
Hover a `dispatch` arm and watch it light up as IR cases and as MOVI+BEQ
pairs. This is the book's central diagram, alive.

## Decision 3: execute both levels — staged, then diffed live

- **v1 — IR interpreter only**, running in the browser via **Pyodide**, so
  the playground executes the *actual repo code* (eDSL, validator,
  interpreter, assembler — no rewrite, no third implementation, the
  single-source-of-truth principle extends to the website). Assembly pane
  is display-only. Pyodide's ~10 MB download is acceptable for a
  playground.
- **v2 — add a small Python assembly-level ISS** (~150 lines mirroring the
  Sail semantics, drift-tripwired in CI against the golden model exactly
  like the existing Python encoding mirror), then the flagship feature:
  **run both levels on the same packet and diff them live** — the
  project's differential-testing methodology, demonstrated interactively.
- Compiling the Sail-generated C emulator to WASM stays rejected
  (Emscripten + GMP + Sail runtime complexity — same call sail-xisa made).

## Decision 4 (parked): SimBricks in the browser — no

QEMU full-system x86 guests, multiple native processes over shared-memory
queues, and Verilator binaries are not browser-viable. Web presence for the
e2e demo instead: a **recorded run** (asciinema of `build_and_run.sh`, ping
+ drop-all counter-run), and later a **"SimBricks-lite" packet lab** in the
playground: two virtual hosts, crafted frames, the parser-gated flood
animated — same thesis, interactive. A server-side runner (real SimBricks
behind a web queue) is a service with cost/abuse surface: parked.

## Prior art to mine

| Project | What to take |
|---|---|
| Compiler Explorer (godbolt) | source↔asm hover provenance |
| nand2tetris Web IDE | whole-toolchain-in-browser for a chip-to-language course |
| Ripes / Venus (RISC-V) | browser ISA sims; Ripes' datapath visualization for a future "watch the cursor move" view |
| easy6502 | gold standard for teaching an ISA in a webpage |
| JupyterLite / PyScript | serious Pyodide tooling ships |
| DigitalJS (yosys2digitaljs) | long-term: run the *synthesized netlist* in-browser — endgame is eDSL \| IR \| asm \| **gates**, all executing in one tab |
| WokWi | browser hardware sim; Tiny Tapeout's own frontend (bridge if stage 5 revives) |
| CloudShark-style pcap viewers | packet-lab pane UX |
| sail-xisa playground (Bili's) | Svelte/CodeMirror experience transfers; Astro re-evaluated and dropped for v1 (no content pages — see [playground v1 design](2026-07-11-playground-v1-design.md)) |
