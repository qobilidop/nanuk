# Examples are content; headers are toolchain

**Date:** 2026-07-11
**Status:** approved (discussion with Bili; supersedes the `nanuk.examples`
placement from the single-package refactor earlier the same day)

## The principle

Surveyed playgrounds (Compiler Explorer, rust-playground, Go tour,
TypeScript playground, Svelte REPL) all hold the same line: **example
programs are editor seed text, never imported by the runtime**; anything
programs need at runtime ships separately as a toolchain asset (CE
libraries, rust-playground's baked crates, P4's p4include).

Nanuk violated this in one spot: the playground imported the l2l3l4
example inside Pyodide (bridge's composed-run rig; nanukproto's header
imports). The fix is to move what was actually toolchain into the
library, at which point examples become pure content and live at the
repo root.

## The decisions (as amended same day: standalone examples)

- **Examples are standalone documents.** Each example declares its own
  headers and wire constants — everything readable on one page, matching
  the playground's editor seeds, which were already self-contained.
  (First cut introduced `nanuk.lang.headers`, a p4include-style shared
  header library; Bili reversed it hours later — with examples
  standalone it had no real consumers, so it was deleted rather than
  kept speculatively. If a user-facing header library is ever wanted,
  that argument can resurrect it with actual consumers.)
- **The bridge owns its rig**: `_make_pp_parser()` in `web/py/bridge.py`
  is a self-contained copy of examples/l2l3l4/parse.py. Tripwire:
  `test_pp_rig_mirrors_l2l3l4_example` holds bridge rig and example
  identical at the assembly level.
- **`examples/` lives at the repo root as flat content** (asm + eDSL
  twins, no `__init__`, no packaging, not importable). Nothing that
  ships imports it; the wheel carries no examples. Tests treat the eDSL
  twins as fixtures loaded by path via `tests.support.load.load_example`
  (pytest `pythonpath` stays `["."]`).
- The playground's editor programs (`web/src/programs/`) remain bundled
  text seeds, each standalone.

## Where demo content goes (the rule going forward)

Reached through the library's API or runtime → ship it in the package.
Read/copied by humans → `examples/` at the repo root. When in doubt, ask
whether Pyodide needs to import it.
