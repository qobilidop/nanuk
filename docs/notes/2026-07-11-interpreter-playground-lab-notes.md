# Lab notes — 2026-07-11 — IR interpreter, playground v1, going public

Decision records and lessons from the satellite sprint that took Nanuk from
"stages done" to a live site. Raw material for the guide; terse by design.

## One cost model, three jobs

The IR interpreter (`compiler/nanuk_ir/interp.py`, ~190 lines: five ops,
three terminators) mirrors the v0 lowering's instruction emission exactly —
ext/shl/adv/stmd/sethdr = 1, re-anchor mark = 0, dispatch = 2 per case
tried, then the default's own cost — with budget checked *before* each
instruction and the failing instruction counted, per `exec.sail`'s
check-then-fetch order. Payoff: `interp(IR)` vs `emulate(lower(IR))`
compares **all seven** ParseResult fields, including `steps` and budget
exhaustion. The differential rig (synthetic + random packets, then the real
l2l3l4/nanukproto programs over the demo corpus) passed with zero
divergences on the first run.

The same emission counts then got a third job: the playground's op-level
IR↔asm provenance is an ordered walk pairing each rendered IR op with its
known instruction count — no comment matching, no schema change. One cost
model now serves the lowering, the interpreter's step parity, and the UI's
highlighting; if v0.x adds a dispatch accelerator, all three move together.

Deliberate coupling, honestly documented, beats accidental divergence.
Error codes 3 (illegal) and 4 (pc range) are structurally impossible at IR
level; 5 (SMD range) is excluded statically by `validate()` — the IR's
totality story is the ISA's, minus the errors the abstraction makes
unrepresentable.

## scapy stays, with one hard boundary

scapy is GPL-2.0-only against Nanuk's Apache-2.0 (formally incompatible),
but its use here — packet building in tests, `rdpcap` in the harness,
installed from PyPI, never vendored — triggers no distribution obligation.
Decision: keep it, dev/test-scoped, with one rule: **scapy never enters a
distributed artifact**. The playground honors this by construction: it
ships only `nanuk-ir` + `nanuk-lang` wheels (nanuk-spec never loads), and
preset packets are baked to hex offline by `web/scripts/gen_presets.py`.
Escape hatches if ever pressed: hand-rolled builders (pedagogically
on-brand) > `os_ken.lib.packet` (Apache-2.0, scapy-like) > dpkt (BSD).
tshark-as-subprocess is licensing-clean for any future dissection oracle —
process invocation is not linking.

## Site architecture: tool per path, artifact as the composition unit

One Pages site, three paths, no framework owning the whole: `/` is a
hand-written landing page (zero dependencies — a page with no components,
routing, or content pipeline is better without a framework), `/play/` is
the playground SPA, `/book/` is reserved. The deploy workflow composes
independent builds into one artifact. Two contracts make it hold: every
build bakes its base path, and the playground reads initial state from
query params (`?program=…&preset=…`) so any future book toolchain embeds
live panes via iframe, Compiler-Explorer-style. The book itself: **same
repo** when it comes (Crafting Interpreters / Fuzzing Book / blog_os all
keep book and code together; the `guide/` + CC-BY-4.0 split anticipated
this). The paper, by contrast, goes in a separate private repo: double-blind
hygiene, publisher copyright separation, frozen-snapshot lifecycle.

## Playground stack, chosen twice

First pass inherited sail-xisa's Astro+Svelte scaffolding; challenged to
re-derive from first principles, Astro fell out (it's a content-site
framework; the playground is one interactive island with no content pages)
while Svelte 5 + CodeMirror 6 survived on merit (CM6's `Decoration` API
*is* the provenance-highlighting primitive; Monaco is IDE-weight for a
10 MB-Pyodide page). Vite + TypeScript around it. Lesson: inherited stacks
deserve the same scrutiny as proposed ones — half of sail-xisa's choices
transferred, half didn't.

## Pyodide facts worth remembering

- Versioning moved to CPython-aligned numbers: npm + CDN are `314.0.2`
  (≙ Python 3.14), not `0.28.x`. Pin in ONE place.
- `uv build` wheels + `micropip.install` just worked; protobuf resolved
  from PyPI as a wheel dependency automatically. Install dependency-first
  (nanuk-ir before nanuk-lang).
- The npm `pyodide` package runs in Node: the CI integration test writes
  wheels into the Pyodide FS and installs via `emfs:` paths — the real
  wheels-in-Pyodide risk is exercised on every Pages build, ~2s warm.
- The bridge (`web/py/bridge.py`) is a plain module: same file runs under
  pytest (13 tests in CI) and inside Pyodide via `runPython`.

## Svelte 5 + CodeMirror gotchas

- svelte-check needs `@tsconfig/svelte` and a `<script>` block in every
  component (a script-less `.svelte` file gets no generated types).
- Top-level `$state(null)` narrows to `null` inside inline `$derived`
  expressions (instance script = function body to TS); `$derived.by`
  closures reset the narrowing.
- Cross-pane scroll-to-highlight: the hover store carries
  `{name, origin}` and panes only scroll when they are *not* the origin —
  scrolling the pane under the cursor yanks the text away. Dedupe hover
  events at the store, or mousemove spams every subscriber.

## Verify by driving, not by curling

curl proved the deploy served files; driving the live site with headless
Playwright found what curl never could: the pane grid's auto row sized
itself to editor content, silently growing past the viewport — pushing the
compile-error banner off-screen while `waitForSelector` still reported it
"visible" (visible = has a box, **not** in-viewport; check `boundingBox()`
against the viewport). Fix: `grid-template-rows: minmax(0, 1fr)` so
editors scroll internally. The same drive verified the semantics on-screen:
qinq's headers at 0/18/22/42 with last-tag-wins TCI=300 — the browser
showing golden-model-correct offsets is the whole stack working at once.

## EuroP4: the deadline that wasn't

Investigated for a possible paper: the venue appears dormant (last edition
Oct 2024 with ICNP; no 2025 edition found; not on ICNP 2026's workshop
list). Decision: decouple writing from venue — draft a tech report → arXiv
when the material is strongest (MLIR and the symbolic executor both
strengthen it), watch p4-announce and CoNEXT'26 for a real CFP. Scope fact
for later: EuroP4's historical CFPs explicitly include alternatives to P4
and education — Nanuk qualifies without a P4 frontend; a P4-concept-mapping
section is cheap insurance.
