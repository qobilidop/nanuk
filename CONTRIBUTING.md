# Contributing to nanuk

## Environment

Everything Python/Sail runs in the dev container:

    devcontainer up --workspace-folder .
    ./dev.sh bash            # or prefix any command with ./dev.sh

The web playground additionally needs Node ≥ 22 on the host (`cd web`).

## Test matrix

| Suite | Command (from repo root) |
|---|---|
| Sail model + emulator | `./dev.sh bash -lc 'cmake -B build && cmake --build build && ctest --test-dir build'` |
| spec (assembler, pcap rig) | `./dev.sh bash -lc 'cd spec/python && uv sync && uv run pytest'` |
| hw (RTL + cosim) | `./dev.sh bash -lc 'cd hw && uv sync && NANUK_COSIM=1 uv run pytest tests'` |
| lang (eDSL) | `./dev.sh bash -lc 'cd lang && uv sync && NANUK_COSIM=1 uv run --group dev pytest tests'` |
| compiler (IR, interp, differential) | `./dev.sh bash -lc 'cd compiler && uv sync && NANUK_COSIM=1 uv run --group dev pytest tests'` |
| playground bridge | `./dev.sh bash -lc 'cd web/py && uv sync && uv run --group dev pytest tests'` |
| playground SPA | `cd web && npm test && npm run build` (host; `web/scripts/build_wheels.sh` first) |

`NANUK_COSIM=1` enables the suites that need the built `nanuk-emu` golden model.

## Conventions

- Commits: imperative sentence, no type prefixes (see `git log`).
- Design docs live in `docs/superpowers/specs/`, implementation plans in
  `docs/superpowers/plans/` — read the relevant spec before changing a
  subsystem. Decision records and lab notes: `guide/notes/`.
- Licensing: code is Apache-2.0; `guide/` content is CC-BY-4.0. scapy
  (GPL-2.0-only) is a dev/test-only dependency and must never be shipped
  in a distributed artifact (wheels, the playground bundle, releases).
