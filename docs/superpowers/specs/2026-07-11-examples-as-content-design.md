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

nanuk violated this in one spot: the playground imported the l2l3l4
example inside Pyodide (bridge's composed-run rig; nanukproto's header
imports). The fix is to move what was actually toolchain into the
library, at which point examples become pure content and live at the
repo root.

## The decisions

- **`nanuk.lang.headers`**: standard protocol headers (eth/vlan/ipv4/udp
  + IANA wire constants), shipped with the toolchain — the p4include
  pattern. Invented protocols (nanukproto's `nk`) stay with their
  example.
- **The bridge owns its rig**: `_make_pp_parser()` in `web/py/bridge.py`
  is a copy of examples/l2l3l4/parse.py, built from `nanuk.lang.headers`.
  Tripwire: `test_pp_rig_mirrors_l2l3l4_example` holds bridge rig and
  example identical at the assembly level.
- **`examples/` returns to the repo root as flat content** (asm + eDSL
  twins, no `__init__`, no packaging). Nothing that ships imports it.
  Tests import the eDSL twins via the `examples` namespace package:
  pytest `pythonpath = [".", ".."]` (repo root). The wheel carries no
  examples.
- The playground's editor programs (`web/src/programs/`) remain bundled
  text seeds; nanukproto's seed now imports `nanuk.lang.headers`.

## Where demo content goes (the rule going forward)

Reached through the library's API or runtime → ship it in the package.
Read/copied by humans → `examples/` at the repo root. When in doubt, ask
whether Pyodide needs to import it.
