# Contributing to nanuk

## Environment

Everything Python/Sail runs in the dev container:

    devcontainer up --workspace-folder .
    ./dev.sh bash            # or prefix any command with ./dev.sh

The web playground additionally needs Node ≥ 22 on the host (`cd web`).

## Test matrix

| Suite | Command (from repo root) |
|---|---|
| Sail models + emulators (parser + MAP) | `./dev.sh bash -lc 'cmake -B build && cmake --build build && ctest --test-dir build'` |
| isa (assemblers, encodings, ISS ×2) | `./dev.sh bash -lc 'cd spec/isa && uv sync && uv run --group dev pytest'` |
| spec (pcap rig, MAP harness, ISS differential) | `./dev.sh bash -lc 'cd spec/python && uv sync && uv run pytest'` |
| hw (RTL cores + cosim + fuzz) | `./dev.sh bash -lc 'cd hw && uv sync && NANUK_COSIM=1 uv run pytest tests'` |
| lang (eDSL) | `./dev.sh bash -lc 'cd lang && uv sync && NANUK_COSIM=1 uv run --group dev pytest tests'` |
| compiler (IR, interps, symex, differential) | `./dev.sh bash -lc 'cd compiler && uv sync && NANUK_COSIM=1 uv run --group dev pytest tests'` |
| playground bridge | `./dev.sh bash -lc 'cd web/py && uv sync && uv run --group dev pytest tests'` |
| playground SPA | `cd web && npm test && npm run build` (host; `web/scripts/build_wheels.sh` first) |

| lint (ruff, all packages) | `./dev.sh bash -lc 'cd spec/python && uv sync && uv run ruff check . ../isa ../../hw ../../lang ../../compiler ../../web'` |
| SimBricks e2e (not in CI) | `hw/simbricks/run_beats12.sh` and `hw/simbricks/run_beat3.sh` (host; needs Docker) |

`NANUK_COSIM=1` enables the suites that need the built `nanuk-emu` /
`nanuk-map-emu` golden models.

## Conventions

- Commits: imperative sentence, no type prefixes (see `git log`).
- Design docs live in `docs/superpowers/specs/`, implementation plans in
  `docs/superpowers/plans/` — read the relevant spec before changing a
  subsystem. Decision records and lab notes: `guide/notes/`.
- Licensing: code is Apache-2.0; `guide/` content is CC-BY-4.0. scapy
  (GPL-2.0-only) is a dev/test-only dependency and must never be shipped
  in a distributed artifact (wheels, the playground bundle, releases).
