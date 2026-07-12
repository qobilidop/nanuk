# Contributing to nanuk

## Environment

Everything Python/Sail runs in the dev container:

    devcontainer up --workspace-folder .
    ./dev.sh bash            # or prefix any command with ./dev.sh

The web playground additionally needs Node ≥ 22 on the host (`cd web`).

## Test matrix

| Suite | Command (from repo root) |
|---|---|
| Sail models + emulators (parser + MAP) | `./dev.sh bash -lc 'cmake -S spec/sail -B spec/sail/build && cmake --build spec/sail/build && ctest --test-dir spec/sail/build'` |
| SW (nanuk package + bridge: isa, ir, lang, pcap rig, playground) | `./dev.sh bash -lc 'cd sw/python && uv sync && NANUK_COSIM=1 uv run pytest tests ../../web/py/tests'` |
| HW (Amaranth cores + cosim vs ISS/golden models) | `./dev.sh bash -lc 'cd hw/amaranth && uv sync && NANUK_COSIM=1 uv run pytest tests'` |
| playground SPA | `cd web && npm test && npm run build` (host; `web/scripts/build_wheels.sh` first) |
| lint (ruff, whole repo) | `./dev.sh bash -lc 'cd sw/python && uv run ruff check ../..'` |
| IR schema (buf lint + breaking vs main; v0 dev-phase: intentional breaks pass CI with `[ir-breaking]` in the commit message; regen gencode: `scripts/gen.py` from sw/python) | `./dev.sh bash -lc 'buf lint spec/proto && buf breaking spec/proto --against ".git#branch=main,subdir=spec/proto"'` |
| API docs (pdoc, both packages → hw/amaranth/build/api, deployed at /api/; runs from hw/amaranth — the only env that imports everything) | `./dev.sh bash -lc 'cd hw/amaranth && uv sync --group docs && uv run pdoc nanuk nanuk_amaranth nanuk.ir.symex "!nanuk.isa._asm_core" "!nanuk.testkit" -o build/api'` |
| SimBricks e2e (not in CI) | `demo/run_beats12.sh` and `demo/run_beat3.sh` (host; needs Docker) |

Run a single SW layer with `uv run pytest tests/isa` (or `tests/ir`,
`tests/lang`, `tests/golden`) from `sw/python/`; the RTL suite runs
from `hw/amaranth/`.

`NANUK_COSIM=1` enables the suites that need the built `nanuk-emu` /
`nanuk-map-emu` golden models.

## Conventions

- Commits: imperative sentence, no type prefixes (see `git log`).
- Design docs live in `docs/superpowers/specs/`, implementation plans in
  `docs/superpowers/plans/` — read the relevant spec before changing a
  subsystem. Decision records and lab notes: `docs/notes/`.
- Licensing: code is Apache-2.0; `docs/notes/` content is CC-BY-4.0
  (raw material for the future book, which inherits the license). scapy
  (GPL-2.0-only) is a dev/test-only dependency and must never be shipped
  in a distributed artifact (wheels, the playground bundle, releases).
