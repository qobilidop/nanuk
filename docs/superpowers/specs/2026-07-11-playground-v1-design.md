# Web Playground v1 — Design

**Date:** 2026-07-11
**Status:** Approved design; implementation plan to follow.
**Parent:** [IR interpreter + playground design](2026-07-11-ir-interpreter-playground-design.md) (decisions 2–3 there govern; this doc fixes the v1 sub-decisions) · [Project design](2026-07-11-nanuk-project-design.md)

## What v1 is

A static single-page app: three synchronized CodeMirror panes — **eDSL
(editable) | IR (read-only) | asm (read-only)** — with state-level hover
provenance across all three, plus a packet panel that runs the IR
interpreter on real packets, all in the browser via Pyodide executing the
*actual repo code* (`nanuk-lang`, `nanuk-ir` wheels; no rewrite, no third
implementation).

Deliberately **not** in v1 (deferred, per parent doc / decisions below):
line-level source spans (needs an IR schema addition — its own future
decision), assembly-level ISS + live level-diffing (v2 flagship),
step-trace view (v2, needs an interp trace hook), packet-lab pane,
Playwright browser e2e.

## Tech stack (chosen from first principles, not inherited)

| Choice | Rationale |
|---|---|
| Vite + **Svelte 5** + TypeScript | One interactive page, growing state (v2 diffing/traces): declarative reactivity with near-zero runtime. Astro rejected — it's a content-site framework and this site has no content pages. |
| **CodeMirror 6** | Modular, small; its `Decoration` API is exactly the provenance-highlighting primitive. Monaco rejected as IDE-weight. |
| **Pyodide**, pinned version, official CDN | Documented least-friction delivery; version pinned for reproducibility. |
| Repo code as **wheels** | CI runs `uv build` on `compiler/` and `lang/`; wheels land in the site's static assets; micropip installs them + `protobuf` (from Pyodide's package index) at page load. `nanuk-spec` is not shipped — v1 needs only eDSL→IR (`nanuk-lang`) and IR→asm-text + `interp()` (`nanuk-ir`). This keeps scapy structurally out of the bundle (see the scapy licensing decision: GPL code must never enter a distributed artifact). |

## Layout and deploy

- `web/` directory in the nanuk monorepo (lockstep with the code it
  executes — single-source-of-truth extends to the website).
- GitHub Pages at `qobilidop.github.io/nanuk` via an Actions workflow:
  build wheels → `npm ci` + `vite build` → `upload-pages-artifact` →
  deploy. Triggers on pushes to main touching `web/`, `lang/`, or
  `compiler/`; PRs touching those paths run the build (not the deploy) so
  bit-rot surfaces at review time.
- A custom domain is a possible future step; no v1 impact.

## Components

- `web/src/App.svelte` — layout: three panes + packet panel + status bar
  (Pyodide load progress, compile status).
- `web/src/lib/panes/` — the three CodeMirror panes; shared highlight
  logic (a Svelte store of the hovered state name → `Decoration` ranges
  per pane).
- `web/src/lib/py.ts` — Pyodide bootstrap and typed bridge:
  `init(): Promise<void>`, `compile(source: string): CompileResult`,
  `run(packetHex: string): ParseResult`. All calls proxy to `bridge.py`;
  results cross as JSON.
- `web/py/bridge.py` — the Python side, loaded into Pyodide at init:
  - `compile(source)`: exec the eDSL source in a fresh namespace; call its
    `build_ir()` (convention: the editor program defines it, like
    `examples/nanukproto/parse.py`); `validate()`; render the IR pane text
    itself, state-by-state (so line ranges are known exactly); run
    `to_asm()`; build the provenance map. Returns
    `{ok, ir_text, asm_text, provenance, error}`.
  - `run(packet_hex)`: `interp(program, bytes.fromhex(...))` on the last
    good compile; returns the full `InterpResult` as a dict.
- `web/src/lib/PacketPanel.svelte` — hex input (validated inline), preset
  chips, Run button, result view: verdict badge, `payload_offset`,
  `steps`, header table (present/offset per hdr_id), SMD slot table.
- `web/public/presets.json` — the demo corpus + nanukproto tunnel packets
  as `{name, hex}`; generated offline by `web/scripts/gen_presets.py`
  (scapy runs at generation time in the devcontainer; only hex strings
  ship, keeping the GPL boundary).

## Provenance model (v1: state-level + op-level by name)

- Unit of correlation is the **parser state**. The bridge extracts, per
  state: eDSL line range (via `ast` — the `@p.state`-decorated function
  defs in the user source), IR pane line range (bridge renders that text,
  so ranges are exact), asm line range (from `name:` labels in `to_asm`
  output). Hovering a state's lines in any pane highlights its ranges in
  the other two.
- Op-level IR↔asm within a state: matched by `debug_name` — the lowering
  already emits it as the instruction comment (`; eth.dst`). No
  cost-model mirroring, no schema change.
- The default editor content is a self-contained l2l3l4 program
  (headers + states in one source, `build_ir()` at the bottom).

## Error handling

- Compile-side (`CompileError`, `ValidationError`, `LowerError`, plus any
  Python exception from user code): banner under the eDSL pane with the
  message verbatim; last good compile stays live in the other panes.
- Run-side: none possible beyond hex validation (interpreter semantics
  are total; error verdicts are results, not failures — the result view
  shows verdict=error with its code like any other outcome).
- Pyodide load failure: status bar error with a retry.

## Testing

- `web/py/tests/` — pytest over the bridge's pure logic (provenance
  extraction, IR text rendering, result serialization) against the real
  packages; runs in CI alongside the other suites.
- The deploy workflow's build step is itself the site's regression check
  (type errors, bundle breakage) on every touched PR.
- Browser e2e: out of scope for v1.
