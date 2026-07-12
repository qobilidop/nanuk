# Single-package refactor: `nanuk`

**Date:** 2026-07-11
**Status:** approved (discussion with Bili, this session)

## Problem

The Python side of Nanuk is six separate uv projects — `nanuk-isa`,
`nanuk-spec`, `nanuk-ir`, `nanuk-lang`, `nanuk-hw`,
`nanuk-playground-bridge` — each with its own `pyproject.toml` and
`uv.lock`, cross-wired with editable `tool.uv.sources` path deps. Nothing
is published to PyPI; the split buys dependency isolation only for the
Pyodide playground bundle, at the cost of six lockfiles, path-dep
plumbing, six CI sync/test invocations, and cross-package refactors that
touch multiple project roots.

## Decision

One package, `nanuk`, in a `python/` subtree. Four submodules named as
descending abstraction levels:

```
nanuk.lang   # protocol-level eDSL            (what you write)
nanuk.ir     # portable parser-level IR        (what tools exchange)
nanuk.isa    # the architectural contract      (encodings, asm, ISS ×2)
nanuk.rtl    # the implementation below it     (Amaranth cores + switch + sim utils)
```

Decisions reached in discussion, with rationale:

- **`python/` subtree, not repo root.** Repo root stays language-neutral
  (`python/ spec/ hw/ web/ docs/ ...`). Costs `uv --project python` (or a
  `cd`) when invoking from root; `lang/` and `compiler/` disappear
  entirely, so the root shrinks overall.
- **`nanuk.rtl`, not `hw`/`chip`.** `lang → ir → isa → rtl` are all
  names of abstraction levels; `hw` names a substance, `chip` a physical
  artifact that doesn't exist. The `hw/` repo dir survives as the
  hardware workbench (export.py, simbricks/, RTL build outputs).
- **`nanuk.ir` stays whole** (proto + validate + lower + interp + symex).
  A `nanuk.compiler` split fails the boundary test: validate/interp/symex
  are IR consumers exactly like lower; `nanuk_lang.parser` imports
  `lower` at module level, so there is no dependency payoff; and "the
  compiler" genuinely spans `lang.compile → ir.lower → isa.asm`.
  Revisit only if lowering grows into a pass pipeline.
- **`nanuk_spec` is demoted out of the package** to unpackaged shared
  test support at `python/tests/support/` (`harness`, `map_harness`,
  `testkit`). Grep-verified: every library-code mention is a docstring;
  the only real importers are tests (hw's declared runtime dep on it was
  stale). This makes the playground-wheel rule structural — shipping
  code cannot import from `tests/` — and removes scapy from package
  metadata entirely.
- **The bridge stays playground-local**: `web/py/bridge.py` unchanged,
  unpackaged; its own pyproject/uv.lock deleted; its tests run from the
  root env.
- **`nanuk.integration.*` is a reserved convention, not a package.**
  When integration glue becomes library code (e.g. simbricks), it goes
  under `nanuk.integration.<name>`. Nothing qualifies today — the
  simbricks material is workbench scripts.

## Target layout

```
python/
  pyproject.toml          # the one Python project
  uv.lock
  scripts/
    gen.py                # protobuf gencode regen (was compiler/gen.py)
  nanuk/
    __init__.py           # docstring only — MUST NOT import submodules
    lang/                 # was lang/nanuk_lang
    ir/                   # was compiler/nanuk_ir (incl. nanuk_ir.proto, nanuk_ir_pb2.py)
    isa/                  # was spec/isa/nanuk_isa
    rtl/                  # was hw/nanuk_hw
  tests/
    support/              # was spec/python/nanuk_spec  (harness, map_harness, testkit)
    lang/                 # was lang/tests (incl. golden/)
    ir/                   # was compiler/tests (incl. irbuild.py helper)
    isa/                  # was spec/isa/tests
    spec/                 # was spec/python/tests (harness + differential rigs)
    rtl/                  # was hw/tests (conftest sys.path hack deleted)
web/py/
  bridge.py               # unchanged; pyproject.toml + uv.lock deleted
  tests/                  # stays; run via the root env
```

Deleted: `lang/`, `compiler/`, `spec/isa/`, `spec/python/` (dirs become
empty), six pyprojects, six lockfiles. `spec/` keeps only Sail
(`parser-model`, `map-model`, `parser-test`, `map-test`, `emulator`).
`hw/` keeps `export.py`, `simbricks/`, build outputs.

## pyproject (sketch)

```toml
[project]
name = "nanuk"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["protobuf>=7.35"]        # the one required dep (nanuk.ir gencode)

[project.optional-dependencies]
rtl = ["amaranth[builtin-yosys]>=0.5"]   # heavy; never enters Pyodide

[project.scripts]
nanuk-asm = "nanuk.isa.asm:main"
nanuk-map-asm = "nanuk.isa.map_asm:main"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.15.21", "scapy>=2.5", "z3-solver>=4.16.0.0",
       "grpcio-tools>=1.82"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]                        # makes `tests.support` importable

[tool.hatch.build.targets.wheel]
packages = ["nanuk"]
```

Notes:
- `nanuk/__init__.py` contains a docstring only. Importing `nanuk.lang`
  in Pyodide must never trigger an amaranth import; `nanuk.rtl` is the
  only module allowed to import amaranth, and only in its own files.
- pytest keeps the default (prepend) import mode: no test-basename
  collisions exist across the merged dirs, and `tests/ir/irbuild.py`
  stays importable as a sibling. `pythonpath = ["."]` (relative to
  `python/`) makes `from tests.support import testkit` work as a
  namespace package; no `__init__.py` files in the test tree.
- The stale deps die here: hw's runtime dep on nanuk-spec, spec's
  runtime scapy (now dev-group only).

## Import rewrite (mechanical)

`nanuk_isa → nanuk.isa`, `nanuk_ir → nanuk.ir`, `nanuk_lang →
nanuk.lang`, `nanuk_hw → nanuk.rtl`, `nanuk_spec → tests.support` —
applied repo-wide to code, including docstring cross-references in
library code. Exceptions: `nanuk_ir.proto` and `nanuk_ir_pb2` keep their
names (the gencode module name is tied to the proto filename; the proto
package `nanuk.ir.v0` already matches the new layout). Historical
records — `guide/notes/`, `docs/superpowers/plans|specs/` — are NOT
rewritten; they describe the repo as it was.

## Touch points outside python/

- **`web/scripts/build_wheels.sh`**: build one wheel (`cd python && uv
  build --wheel`) instead of three; manifest format unchanged (a list,
  now length 1). The dependency-first `.sort()` in `runtime-browser.ts`
  becomes a no-op; leave it. micropip still resolves protobuf from PyPI
  off the wheel's metadata.
- **`web/py/tests`**: conftest already inserts `web/py` on sys.path for
  `import bridge`; runs via `uv run --project python pytest
  ../web/py/tests` (needs the root env only).
- **`hw/export.py`**: drop the sys.path hack; `from nanuk.rtl.core
  import ...`; invoked as `uv run --project ../python python export.py`
  (env must have the `rtl` extra).
- **`hw/simbricks/build_component.sh`, `build_and_run.sh`**: replace
  `cd hw && uv sync/uv run` with the python project equivalents
  (`uv sync --extra rtl` once, `uv run --project ...`).
- **`.github/workflows/ci.yml`**: the six per-package blocks collapse to
  one sync + one pytest (with `NANUK_COSIM=1`) + one ruff + the bridge
  tests.
- **`.github/workflows/pages.yml`**: path filters `lang/**`,
  `compiler/**` → `python/**`.
- **`ruff.toml`**: pb2 exclude path → `python/nanuk/ir/nanuk_ir_pb2.py`.
- **Living docs**: README (layout table, test commands), CONTRIBUTING
  (test matrix collapses to a few rows).

## Path-depth invariants to verify

- `tests/support/harness.py` resolves the emulator as
  `parents[3]/build/nanuk-emu` — same depth as the old location
  (`spec/python/nanuk_spec/harness.py`), so it holds; verify by test.
- `scripts/gen.py` paths rewritten for its new home.
- `web/py/tests/test_map_bridge.py` reads `parents[2]/src/programs/...`
  — unmoved, still valid.

## Migration approach

One branch, one mechanical migration commit (git mv + import rewrite +
new pyproject/lock + plumbing rewire), follow-up commits for living
docs. Green at every commit. Rejected alternatives: compatibility shims
(throwaway plumbing, worse interim state), workspace-first two-phase
(everything rewritten twice).

## Verification gate

From the devcontainer or a host with uv + the built emulator:

1. `cd python && uv sync --extra rtl` — lock resolves, one env.
2. `NANUK_COSIM=1 uv run pytest tests ../web/py/tests -q` — full suite
   including cosim and bridge.
3. `uv run ruff check ..` from `python/` — clean.
4. `web/scripts/build_wheels.sh` — one wheel + manifest; then `cd web &&
   npm test` — the SPA's tests pass against the new wheel.
5. `uv run nanuk-asm --help`, `uv run python scripts/gen.py` (idempotent
   gencode), `uv run --extra rtl python ../hw/export.py /tmp/t.v` if
   feasible locally.

## Out of scope

- Renaming the `hw/` workbench dir (revisit if it bugs anyone).
- Splitting `nanuk.ir`; creating `nanuk.integration` (conventions
  recorded above, triggered by future occupants).
- Rewriting historical lab notes/plans/specs.
