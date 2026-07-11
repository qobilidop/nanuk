# Web Playground v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The nanuk playground at `qobilidop.github.io/nanuk/play/` — three synchronized panes (eDSL | IR | asm) with state-level hover provenance, a packet panel running `interp()` in-browser via Pyodide on CI-built wheels of the real packages — plus a hand-written landing page at `/` and the composed GitHub Pages deploy.

**Architecture:** Vite + Svelte 5 + TypeScript SPA in `web/` (base `/nanuk/play/`); CodeMirror 6 panes with a Decoration-based highlight layer; a Python bridge (`web/py/bridge.py`) loaded into Pyodide that compiles eDSL source → IR → asm and builds the provenance map; wheels of `nanuk-ir` + `nanuk-lang` built by `uv build` into static assets; deploy workflow composes landing + SPA into one Pages artifact. Spec: `docs/superpowers/specs/2026-07-11-playground-v1-design.md`.

**Tech Stack:** Vite 7, Svelte 5, TypeScript (strict), CodeMirror 6, Pyodide 0.28.x (pinned; npm package for Node tests, CDN at runtime), vitest, uv, pytest.

## Global Constraints

- Base path is exactly `/nanuk/play/` (Vite `base`); the landing owns `/`. Nothing hardcodes the origin — use `import.meta.env.BASE_URL` for asset fetches.
- Pyodide pinned to one version in ONE place (`web/src/lib/pyodide-version.ts`); npm `pyodide` dependency pinned to the same version. At execution time check the latest stable 0.28.x and use it consistently.
- scapy never appears in `web/` dependencies; `nanuk-spec` is never installed in Pyodide. Presets are baked hex in `web/public/presets.json` (committed), generated offline by `web/scripts/gen_presets.py` (which runs in the devcontainer where scapy exists).
- Wheels are build artifacts, never committed: `web/public/wheels/` is gitignored.
- Provenance line numbers are 1-based inclusive `[start, end]` everywhere (Python and TS).
- The bridge's JSON shapes are the contract; they are defined in Task 1 (`types.ts`) and Task 3 (bridge) and MUST match field-for-field.
- Python-side commands run in the devcontainer (`./dev.sh bash -lc '...'`); npm commands run on the host in `web/` (Node ≥ 22).
- Commit style: repo convention (imperative sentence, no prefixes), trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## File Map

```
web/
  index.html                  Vite entry (SPA shell)
  package.json / tsconfig.json / vite.config.ts / svelte.config.js
  src/main.ts                 mounts App
  src/App.svelte              layout, runtime init, compile loop, URL params
  src/lib/types.ts            CompileResult/RunResult/Provenance TS types (the contract)
  src/lib/pyodide-version.ts  the single version pin
  src/lib/py.ts               initRuntime(): Pyodide + wheels + bridge, typed calls
  src/lib/runtime-browser.ts  browser-side loader (CDN import, asset fetches)
  src/lib/stores.ts           hoveredState store
  src/lib/params.ts           URL query param parsing (pure)
  src/lib/panes/highlight.ts  CM6 decoration field + hover helpers (pure-ish)
  src/lib/panes/CodePane.svelte  generic editor pane
  src/lib/PacketPanel.svelte  hex input, preset chips, run button
  src/lib/ResultView.svelte   verdict badge + tables
  src/programs/l2l3l4.py      default editor program (asset, ?raw)
  src/programs/nanukproto.py  second example program (asset, ?raw)
  tests/pyodide.test.ts       Node integration smoke (wheels + bridge in Pyodide)
  src/lib/params.test.ts, src/lib/panes/highlight.test.ts  unit tests
  py/pyproject.toml           uv project: nanuk-ir + nanuk-lang path deps, pytest
  py/bridge.py                compile_source()/run_packet() + IR renderer + provenance
  py/tests/test_render.py, py/tests/test_bridge.py, py/tests/test_presets.py
  scripts/build_wheels.sh     uv build → public/wheels/ + manifest.json + bridge copy
  scripts/gen_presets.py      corpus → public/presets.json (committed)
  public/presets.json         committed, generated
  site/index.html, site/shared.css   landing page + shared look tokens
.github/workflows/pages.yml   build (PR + main) / deploy (main)
CONTRIBUTING.md
```

---

### Task 1: Web scaffold — Vite + Svelte + TS + the type contract

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/svelte.config.js`, `web/index.html`, `web/src/main.ts`, `web/src/App.svelte`, `web/src/lib/types.ts`, `web/src/lib/pyodide-version.ts`, `web/src/vite-env.d.ts`
- Modify: `.gitignore`
- Test: build + typecheck are the test at this stage

**Interfaces:**
- Produces: the `web/` npm project every later task builds in; `types.ts` — the JSON contract all later tasks import: `CompileResult`, `RunResult`, `ParseResultJson`, `StateProvenance`, `OpProvenance`; `PYODIDE_VERSION` constant.

- [ ] **Step 1: Scaffold the npm project**

Create `web/package.json`:

```json
{
  "name": "nanuk-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "svelte-check --tsconfig ./tsconfig.json && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "devDependencies": {
    "@sveltejs/vite-plugin-svelte": "^6.0.0",
    "@codemirror/commands": "^6.10.0",
    "@codemirror/lang-python": "^6.3.0",
    "@codemirror/language": "^6.12.0",
    "@codemirror/state": "^6.6.0",
    "@codemirror/view": "^6.41.0",
    "codemirror": "^6.0.2",
    "pyodide": "0.28.3",
    "svelte": "^5.55.0",
    "svelte-check": "^4.4.0",
    "typescript": "^5.9.0",
    "vite": "^7.3.0",
    "vitest": "^3.2.0"
  }
}
```

(At execution: `npm install` from `web/`; if a listed version doesn't resolve, take the latest compatible and keep `pyodide` an exact pin. Check the latest stable Pyodide 0.28.x and pin it here AND in `pyodide-version.ts`.)

Create `web/vite.config.ts`:

```ts
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  base: '/nanuk/play/',
  plugins: [svelte()],
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'tests/**/*.test.ts'],
    testTimeout: 180_000,
  },
});
```

Create `web/svelte.config.js`:

```js
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
export default { preprocess: vitePreprocess() };
```

Create `web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "types": ["svelte", "vite/client"],
    "verbatimModuleSyntax": true
  },
  "include": ["src/**/*.ts", "src/**/*.svelte", "tests/**/*.ts"]
}
```

Create `web/src/vite-env.d.ts`:

```ts
/// <reference types="vite/client" />
declare module '*.py?raw' {
  const src: string;
  export default src;
}
```

- [ ] **Step 2: Entry files and the type contract**

Create `web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>nanuk playground</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

Create `web/src/main.ts`:

```ts
import { mount } from 'svelte';
import App from './App.svelte';

mount(App, { target: document.getElementById('app')! });
```

Create `web/src/App.svelte` (placeholder; replaced in Task 6):

```svelte
<h1>nanuk playground</h1>
<p>Scaffold OK — UI lands in a later task.</p>
```

Create `web/src/lib/pyodide-version.ts`:

```ts
// The ONE place the Pyodide version is pinned. Keep in lockstep with the
// exact "pyodide" pin in package.json.
export const PYODIDE_VERSION = '0.28.3';
export const PYODIDE_CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
```

Create `web/src/lib/types.ts` (the bridge JSON contract — Task 3's bridge must emit exactly this):

```ts
/** 1-based inclusive line range. */
export type LineRange = [number, number];

export interface OpProvenance {
  label: string;      // human label, e.g. "eth.dst" or "dispatch eth.ethertype"
  ir_line: number;    // 1-based line in ir_text
  asm_lines: number[]; // 1-based lines in asm_text (may be empty: re-anchor mark)
}

export interface StateProvenance {
  name: string;
  edsl: LineRange | null; // null if the state fn wasn't found in the source
  ir: LineRange;
  asm: LineRange;
  ops: OpProvenance[];
}

export interface BridgeError {
  kind: 'syntax' | 'compile' | 'runtime' | 'no_build_ir' | 'bad_hex' | 'no_program';
  message: string;
  line: number | null; // 1-based line in the eDSL source, when known
}

export interface CompileOk {
  ok: true;
  ir_text: string;
  asm_text: string;
  states: StateProvenance[];
}
export interface CompileFail { ok: false; error: BridgeError }
export type CompileResult = CompileOk | CompileFail;

export interface ParseResultJson {
  verdict: 0 | 1 | 2;
  error: number;
  payload_offset: number;
  steps: number;
  hdr_present: number[];
  hdr_offset: number[];
  smd: number[];
}
export interface RunOk { ok: true; result: ParseResultJson }
export interface RunFail { ok: false; error: BridgeError }
export type RunResult = RunOk | RunFail;
```

- [ ] **Step 3: Gitignore the web build artifacts**

Append to the root `.gitignore` under the `# Python` block (new section):

```
# Web (playground)
node_modules/
web/dist/
web/public/wheels/
```

- [ ] **Step 4: Install and verify build + typecheck**

Run (host, in `web/`): `npm install && npm run build`
Expected: svelte-check passes (0 errors), `vite build` emits `web/dist/`. There are no vitest tests yet — that's fine (`npm test` would say "no test files"; don't run it yet).

- [ ] **Step 5: Commit**

```bash
git add web/ .gitignore
git commit -m "Scaffold the playground SPA: Vite + Svelte 5 + TS, typed bridge contract

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Bridge project + IR text renderer

**Files:**
- Create: `web/py/pyproject.toml`, `web/py/bridge.py` (renderer half), `web/py/tests/test_render.py`

**Interfaces:**
- Consumes: `nanuk_ir` (proto, `to_asm`), `nanuk_lang.programs.l2l3l4.build_ir` (in tests).
- Produces: `render_ir(program) -> RenderedIr` where `RenderedIr` has `text: str`, `states: list[RenderedState]`; `RenderedState` has `name: str`, `ir_range: tuple[int, int]`, `ops: list[RenderedOp]`; `RenderedOp` has `label: str`, `ir_line: int`, `asm_count: int` (how many asm instruction lines the op lowers to — the ordered-walk provenance basis). Task 3 consumes all of these.

- [ ] **Step 1: Create the uv project**

Create `web/py/pyproject.toml`:

```toml
[project]
name = "nanuk-playground-bridge"
version = "0.1.0"
description = "Python side of the nanuk playground: compile/run/provenance inside Pyodide"
requires-python = ">=3.12"
dependencies = [
    "nanuk-ir",
    "nanuk-lang",
]

[dependency-groups]
dev = [
    "pytest>=8",
]

[tool.uv.sources]
nanuk-ir = { path = "../../compiler", editable = true }
nanuk-lang = { path = "../../lang", editable = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["."]
```

- [ ] **Step 2: Write the failing renderer tests**

Create `web/py/tests/test_render.py`:

```python
"""The IR pane text renderer: deterministic text, exact per-state line
ranges, and per-op asm emission counts that mirror the v0 lowering
(ext/shl/adv/stmd/sethdr = 1, re-anchor mark = 0, dispatch = 2 per case
+ 1 for the default, goto/halt = 1)."""

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.lower import to_asm
from nanuk_lang.programs.l2l3l4 import build_ir

from bridge import render_ir


def test_renders_all_states_with_exact_ranges():
    program = build_ir()
    rendered = render_ir(program)
    lines = rendered.text.splitlines()
    assert [s.name for s in rendered.states] == [st.name for st in program.states]
    for state in rendered.states:
        start, end = state.ir_range
        assert lines[start - 1] == f"{state.name}:"          # 1-based
        assert all(lines[i].startswith("    ") for i in range(start, end))


def test_op_labels_and_lines():
    rendered = render_ir(build_ir())
    start = rendered.states[0]
    labels = [op.label for op in start.ops]
    # start: mark eth, extract eth.dst, smd, extract ethertype, advance,
    # dispatch (the dispatch is one op entry; its cases are lines within it)
    assert labels[0] == "mark eth"
    assert "eth.dst" in labels[1]
    lines = rendered.text.splitlines()
    for op in start.ops:
        assert lines[op.ir_line - 1].strip() != ""


def test_asm_counts_sum_to_real_instruction_count():
    # The ordered-walk provenance only works if per-op counts exactly
    # partition each state's asm block. Cross-check against to_asm.
    program = build_ir()
    rendered = render_ir(program)
    asm_lines = to_asm(program).splitlines()
    for st, rst in zip(program.states, rendered.states):
        label_idx = asm_lines.index(f"{st.name}:")
        n = 0
        for line in asm_lines[label_idx + 1:]:
            if not line.startswith("    "):
                break
            n += 1
        assert sum(op.asm_count for op in rst.ops) == n, st.name


def test_reanchor_mark_costs_zero_asm_lines():
    program = build_ir()
    rendered = render_ir(program)
    body = next(s for s in rendered.states if s.name == "ipv4_body")
    reanchor = body.ops[0]
    assert reanchor.label == "mark ipv4 (re-anchor)"
    assert reanchor.asm_count == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./dev.sh bash -lc 'cd web/py && uv sync --quiet && uv run --group dev pytest tests/test_render.py -q'`
Expected: FAIL — `ModuleNotFoundError: No module named 'bridge'` (pytest adds `web/py` to `sys.path` via rootdir conftest behavior only if the module is importable; create `web/py/tests/conftest.py` containing `import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))` as part of this step so the import path is explicit).

- [ ] **Step 4: Implement the renderer half of the bridge**

Create `web/py/bridge.py`:

```python
"""nanuk playground bridge: runs inside Pyodide (and under pytest).

Renders the IR pane text deterministically (so per-state line ranges are
known exactly), tracks per-op asm emission counts mirroring the v0
lowering, and (Task 3) exposes compile_source()/run_packet() as the
JSON API consumed by web/src/lib/py.ts. Line numbers are 1-based
inclusive everywhere."""

from dataclasses import dataclass, field

from nanuk_ir import nanuk_ir_pb2 as ir


@dataclass
class RenderedOp:
    label: str
    ir_line: int
    asm_count: int


@dataclass
class RenderedState:
    name: str
    ir_range: tuple[int, int]
    ops: list[RenderedOp] = field(default_factory=list)


@dataclass
class RenderedIr:
    text: str
    states: list[RenderedState]


def _value_name(names: dict[int, str], value_id: int) -> str:
    return names.get(value_id, f"v{value_id}")


def render_ir(program: ir.Program) -> RenderedIr:
    lines: list[str] = []
    states: list[RenderedState] = []
    for st in program.states:
        start_line = len(lines) + 1
        lines.append(f"{st.name}:")
        rstate = RenderedState(name=st.name, ir_range=(start_line, start_line))
        names: dict[int, str] = {}
        for op in st.ops:
            match op.WhichOneof("op"):
                case "extract":
                    e = op.extract
                    name = e.debug_name or f"v{e.value_id}"
                    names[e.value_id] = name
                    lines.append(
                        f"    v{e.value_id} = extract(boff={e.bit_offset}, "
                        f"w={e.width})  ; {name}"
                    )
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "shift":
                    sh = op.shift
                    name = f"{_value_name(names, sh.src_value_id)} << {sh.amount}"
                    names[sh.value_id] = name
                    lines.append(
                        f"    v{sh.value_id} = v{sh.src_value_id} << {sh.amount}"
                    )
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "advance":
                    adv = op.advance
                    if adv.WhichOneof("amount") == "const_bytes":
                        lines.append(f"    advance {adv.const_bytes}")
                        label = f"advance {adv.const_bytes}"
                    else:
                        label = f"advance {_value_name(names, adv.value_id)}"
                        lines.append(f"    advance v{adv.value_id}")
                    rstate.ops.append(RenderedOp(label, len(lines), 1))
                case "mark":
                    m = op.mark
                    disp = m.debug_name or f"hdr{m.hdr_id}"
                    if m.emit_sethdr:
                        lines.append(f"    mark hdr[{m.hdr_id}]  ; {disp}")
                        rstate.ops.append(RenderedOp(f"mark {disp}", len(lines), 1))
                    else:
                        lines.append(f"    mark (re-anchor)  ; {disp}")
                        rstate.ops.append(
                            RenderedOp(f"mark {disp} (re-anchor)", len(lines), 0)
                        )
                case "emit_smd":
                    s = op.emit_smd
                    name = _value_name(names, s.value_id)
                    lines.append(f"    smd[{s.slot}] = v{s.value_id}  ; {name}")
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
        _render_terminator(st.terminator, lines, rstate, names)
        rstate.ir_range = (start_line, len(lines))
        states.append(rstate)
        lines.append("")
    return RenderedIr(text="\n".join(lines).rstrip() + "\n", states=states)


def _render_terminator(
    term: ir.Terminator,
    lines: list[str],
    rstate: RenderedState,
    names: dict[int, str],
) -> None:
    match term.WhichOneof("kind"):
        case "halt":
            verdict = "drop" if term.halt.drop else "accept"
            lines.append(f"    halt {verdict}")
            rstate.ops.append(RenderedOp(f"halt {verdict}", len(lines), 1))
        case "goto":
            lines.append(f"    goto {term.goto.target_state}")
            rstate.ops.append(
                RenderedOp(f"goto {term.goto.target_state}", len(lines), 1)
            )
        case "dispatch":
            d = term.dispatch
            name = _value_name(names, d.value_id)
            lines.append(f"    dispatch v{d.value_id}  ; {name}")
            rstate.ops.append(RenderedOp(f"dispatch {name}", len(lines), 0))
            for case_ in d.cases:
                lines.append(f"        {case_.match:#06x} -> {case_.target_state}")
                rstate.ops.append(
                    RenderedOp(
                        f"{name} == {case_.match:#x} -> {case_.target_state}",
                        len(lines),
                        2,  # MOVI + BEQ
                    )
                )
            # default: one more line, then the default terminator's own cost
            match d.default.WhichOneof("kind"):
                case "halt":
                    verdict = "drop" if d.default.halt.drop else "accept"
                    lines.append(f"        default -> halt {verdict}")
                    rstate.ops.append(
                        RenderedOp(f"default -> halt {verdict}", len(lines), 1)
                    )
                case "goto":
                    target = d.default.goto.target_state
                    lines.append(f"        default -> goto {target}")
                    rstate.ops.append(
                        RenderedOp(f"default -> goto {target}", len(lines), 1)
                    )
```

Also create `web/py/tests/conftest.py`:

```python
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./dev.sh bash -lc 'cd web/py && uv run --group dev pytest tests/test_render.py -q'`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add web/py/
git commit -m "Render the IR pane text with exact ranges and lowering emission counts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Bridge compile/run API + provenance assembly

**Files:**
- Modify: `web/py/bridge.py` (append the API half)
- Create: `web/py/tests/test_bridge.py`
- Modify: `.github/workflows/ci.yml` (add the bridge suite)
- Modify: `docs/superpowers/specs/2026-07-11-playground-v1-design.md` (provenance mechanism note)

**Interfaces:**
- Consumes: Task 2's `render_ir`; `nanuk_lang` (user source imports it), `nanuk_ir.validate/lower/interp`.
- Produces: module-level `compile_source(source: str) -> str` and `run_packet(packet_hex: str) -> str`, both returning JSON strings matching `types.ts` (`CompileResult` / `RunResult`). Task 5's `py.ts` calls exactly these two names via `pyodide.globals.get`.

- [ ] **Step 1: Write the failing tests**

Create `web/py/tests/test_bridge.py`:

```python
"""compile_source/run_packet: the JSON API the SPA consumes."""

import json

from nanuk_lang.programs import l2l3l4  # noqa: F401  (env sanity)

import bridge

GOOD = """\
from nanuk_lang import Header, Parser

eth = Header("eth", dst=48, src=48, ethertype=16)

def make_parser():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(eth, hdr_id=0)
        s.smd(s.extract(eth.dst), slot=0)
        s.advance(eth.byte_len)
        s.accept()

    return p

def build_ir():
    return make_parser().build_ir()
"""


def compile_ok(source: str) -> dict:
    out = json.loads(bridge.compile_source(source))
    assert out["ok"], out
    return out


def test_compile_good_source():
    out = compile_ok(GOOD)
    assert "start:" in out["ir_text"]
    assert "start:" in out["asm_text"]
    (state,) = out["states"]
    assert state["name"] == "start"
    # eDSL range covers the decorated function (def line through body)
    lo, hi = state["edsl"]
    assert GOOD.splitlines()[lo - 1].strip().startswith("@p.state")
    assert lo < hi
    # asm range starts at the label line
    assert out["asm_text"].splitlines()[state["asm"][0] - 1] == "start:"
    # ordered-walk op mapping: every op's asm lines are inside the state range
    for op in state["ops"]:
        for ln in op["asm_lines"]:
            assert state["asm"][0] < ln <= state["asm"][1]


def test_ops_partition_the_asm_block():
    out = compile_ok(GOOD)
    (state,) = out["states"]
    claimed = [ln for op in state["ops"] for ln in op["asm_lines"]]
    lo, hi = state["asm"]
    assert sorted(claimed) == list(range(lo + 1, hi + 1))  # label line excluded


def test_syntax_error_reports_line():
    out = json.loads(bridge.compile_source("def broken(:\n"))
    assert not out["ok"]
    assert out["error"]["kind"] == "syntax"
    assert out["error"]["line"] == 1


def test_missing_build_ir():
    out = json.loads(bridge.compile_source("x = 1\n"))
    assert not out["ok"]
    assert out["error"]["kind"] == "no_build_ir"


def test_user_exception_reports_edsl_line():
    src = "raise RuntimeError('boom')\n\ndef build_ir():\n    pass\n"
    out = json.loads(bridge.compile_source(src))
    assert not out["ok"]
    assert out["error"]["kind"] == "runtime"
    assert out["error"]["line"] == 1
    assert "boom" in out["error"]["message"]


def test_compile_error_kind():
    src = (
        "from nanuk_lang import Header\n"
        "h = Header('bad', x=3)\n"  # 3 bits: not whole bytes -> CompileError
        "def build_ir():\n    pass\n"
    )
    out = json.loads(bridge.compile_source(src))
    assert not out["ok"]
    assert out["error"]["kind"] == "compile"


def test_run_before_compile_and_bad_hex():
    bridge._LAST_PROGRAM = None
    out = json.loads(bridge.run_packet("aabb"))
    assert not out["ok"] and out["error"]["kind"] == "no_program"
    compile_ok(GOOD)
    out = json.loads(bridge.run_packet("zz"))
    assert not out["ok"] and out["error"]["kind"] == "bad_hex"


def test_run_returns_full_parse_result():
    compile_ok(GOOD)
    out = json.loads(bridge.run_packet("aa bb cc dd ee 01" + " 00" * 8))
    assert out["ok"]
    r = out["result"]
    assert r["verdict"] == 0
    assert r["payload_offset"] == 14
    assert r["smd"][:3] == [0xAABB, 0xCCDD, 0xEE01]
    assert len(r["hdr_present"]) == 16 and len(r["smd"]) == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./dev.sh bash -lc 'cd web/py && uv run --group dev pytest tests/test_bridge.py -q'`
Expected: FAIL — `AttributeError: module 'bridge' has no attribute 'compile_source'`

- [ ] **Step 3: Implement the API half**

Append to `web/py/bridge.py`:

```python
# --- JSON API (called from web/src/lib/py.ts via pyodide.globals) -----------

import ast
import json
import traceback

from nanuk_ir.interp import interp
from nanuk_ir.lower import LowerError, to_asm
from nanuk_ir.validate import ValidationError, validate

_EDSL_FILENAME = "<edsl>"
_LAST_PROGRAM = None


def _err(kind: str, message: str, line: int | None = None) -> str:
    return json.dumps({"ok": False, "error": {"kind": kind, "message": message, "line": line}})


def _edsl_line(exc: BaseException) -> int | None:
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == _EDSL_FILENAME:
            return frame.lineno
    return None


def _edsl_ranges(source: str, state_names: set[str]) -> dict[str, tuple[int, int]]:
    """Line ranges of @p.state-decorated functions whose names are states."""
    ranges: dict[str, tuple[int, int]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name in state_names:
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            else:
                start = node.lineno
            ranges[node.name] = (start, node.end_lineno or node.lineno)
    return ranges


def _asm_ranges(asm_text: str, state_names: list[str]) -> dict[str, tuple[int, int]]:
    lines = asm_text.splitlines()
    starts = {line.rstrip(":"): i + 1 for i, line in enumerate(lines) if line.endswith(":")}
    ranges: dict[str, tuple[int, int]] = {}
    for name in state_names:
        start = starts[name]
        end = start
        for i in range(start, len(lines)):
            if lines[i].startswith("    "):
                end = i + 1
            else:
                break
        ranges[name] = (start, end)
    return ranges


def compile_source(source: str) -> str:
    global _LAST_PROGRAM
    try:
        code = compile(source, _EDSL_FILENAME, "exec")
    except SyntaxError as e:
        return _err("syntax", str(e.msg), e.lineno)
    namespace: dict = {}
    try:
        exec(code, namespace)
    except (ValidationError, LowerError) as e:
        return _err("compile", str(e), _edsl_line(e))
    except Exception as e:  # CompileError included: nanuk_lang may not be imported yet
        kind = "compile" if type(e).__name__ == "CompileError" else "runtime"
        return _err(kind, f"{type(e).__name__}: {e}", _edsl_line(e))
    build_ir = namespace.get("build_ir")
    if not callable(build_ir):
        return _err("no_build_ir", "the program must define a build_ir() function")
    try:
        program = build_ir()
        validate(program)
        asm_text = to_asm(program, check=False)
    except (ValidationError, LowerError) as e:
        return _err("compile", str(e), _edsl_line(e))
    except Exception as e:
        kind = "compile" if type(e).__name__ == "CompileError" else "runtime"
        return _err(kind, f"{type(e).__name__}: {e}", _edsl_line(e))

    rendered = render_ir(program)
    names = [st.name for st in program.states]
    edsl = _edsl_ranges(source, set(names))
    asm = _asm_ranges(asm_text, names)
    states = []
    for rstate in rendered.states:
        a_lo, _ = asm[rstate.name]
        cursor = a_lo + 1  # first instruction line after the label
        ops = []
        for op in rstate.ops:
            asm_lines = list(range(cursor, cursor + op.asm_count))
            cursor += op.asm_count
            ops.append({"label": op.label, "ir_line": op.ir_line, "asm_lines": asm_lines})
        states.append({
            "name": rstate.name,
            "edsl": list(edsl[rstate.name]) if rstate.name in edsl else None,
            "ir": list(rstate.ir_range),
            "asm": list(asm[rstate.name]),
            "ops": ops,
        })
    _LAST_PROGRAM = program
    return json.dumps({
        "ok": True,
        "ir_text": rendered.text,
        "asm_text": asm_text,
        "states": states,
    })


def run_packet(packet_hex: str) -> str:
    if _LAST_PROGRAM is None:
        return _err("no_program", "compile a program first")
    cleaned = "".join(packet_hex.split())
    try:
        packet = bytes.fromhex(cleaned)
    except ValueError:
        return _err("bad_hex", "packet must be hex bytes (whitespace allowed)")
    result = interp(_LAST_PROGRAM, packet, check=False)
    return json.dumps({
        "ok": True,
        "result": {
            "verdict": result.verdict,
            "error": result.error,
            "payload_offset": result.payload_offset,
            "steps": result.steps,
            "hdr_present": result.hdr_present,
            "hdr_offset": result.hdr_offset,
            "smd": result.smd,
        },
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./dev.sh bash -lc 'cd web/py && uv run --group dev pytest tests -q'`
Expected: 13 passed (4 render + 9 bridge)

- [ ] **Step 5: Wire the bridge suite into CI and note the provenance mechanism in the spec**

In `.github/workflows/ci.yml`, append to `runCmd` after the compiler line:

```yaml
            (cd web/py && uv sync --quiet && uv run --group dev pytest tests -q)
```

In `docs/superpowers/specs/2026-07-11-playground-v1-design.md`, replace the sentence beginning "Op-level IR↔asm within a state: matched by `debug_name`" with:

```
- Op-level IR↔asm within a state: an ordered walk — the bridge renders IR
  ops in program order and pairs each with its known lowering emission
  count (ext/shl/adv/stmd/sethdr = 1, re-anchor mark = 0, dispatch case =
  2, default = its own cost), the same cost model the interpreter's
  differential tests already validate. Deterministic; no schema change.
```

- [ ] **Step 6: Commit**

```bash
git add web/py/ .github/workflows/ci.yml docs/superpowers/specs/2026-07-11-playground-v1-design.md
git commit -m "Bridge compile/run JSON API with state- and op-level provenance

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Example programs + preset packets

**Files:**
- Create: `web/src/programs/l2l3l4.py`, `web/src/programs/nanukproto.py`, `web/scripts/gen_presets.py`, `web/public/presets.json` (generated then committed), `web/py/tests/test_presets.py`

**Interfaces:**
- Consumes: bridge `compile_source`/`run_packet` (tests), scapy (generation only, devcontainer).
- Produces: the two `?raw` program assets Task 6 imports; `presets.json` schema `[{ "name": str, "hex": str, "note": str }]` consumed by Task 7's PacketPanel.

- [ ] **Step 1: Write the example program assets**

Create `web/src/programs/l2l3l4.py` — the default editor content. Body identical to `lang/nanuk_lang/programs/l2l3l4.py` lines 10–79 (the imports through `build_ir()`), with the module docstring replaced by this header comment and `build()`/`__main__` omitted:

```python
# The nanuk demo parser: Ethernet -> 802.1Q (incl. QinQ) -> IPv4 (with
# options) -> UDP. Edit me and watch the IR and assembly panes follow.
#
# Header ids: eth=0 vlan=1 ipv4=2 udp=3
# SMD slots:  0-2 DMAC | 3 outermost-last VLAN TCI | 4 UDP dport
```

(then verbatim: the `from nanuk_lang import Header, Parser` import, the four `Header` declarations, the constants, `make_parser()` exactly as in the source file, and the `build_ir()` function.)

Create `web/src/programs/nanukproto.py` — body identical to `examples/nanukproto/parse.py` lines 5–90 minus the `build()` function and `__main__` block, with this header comment:

```python
# nanukproto: the invented tunnel protocol from demo beat 3, layered on
# the standard L2/L3/L4 parse. Adding a protocol is one Header + 3 states.
```

- [ ] **Step 2: Test that the shipped sources compile via the bridge**

Create `web/py/tests/test_presets.py`:

```python
"""The shipped example programs compile through the bridge verbatim, and
every preset packet produces the expected verdict on both programs."""

import json
import pathlib

import bridge

WEB = pathlib.Path(__file__).resolve().parents[2]
PROGRAMS = WEB / "src" / "programs"
PRESETS = WEB / "public" / "presets.json"

# name -> (verdict on l2l3l4, verdict on nanukproto); 0=accept 1=drop 2=error
EXPECTED = {
    "plain_ipv4_udp": (0, 0),
    "single_vlan": (0, 0),
    "qinq": (0, 0),
    "ipv4_options": (0, 0),
    "ipv4_tcp": (0, 0),
    "arp": (0, 0),
    "runt_frame": (2, 2),
    "non_v4_version": (1, 1),
    "nk_tunnel": (0, 0),  # l2l3l4: unknown EtherType -> accept; nanukproto: parsed
}


def _compile(path: pathlib.Path) -> None:
    out = json.loads(bridge.compile_source(path.read_text()))
    assert out["ok"], out


def test_presets_expected_verdicts():
    presets = json.loads(PRESETS.read_text())
    assert {p["name"] for p in presets} == set(EXPECTED)
    for program in ("l2l3l4.py", "nanukproto.py"):
        _compile(PROGRAMS / program)
        for preset in presets:
            out = json.loads(bridge.run_packet(preset["hex"]))
            assert out["ok"]
            want = EXPECTED[preset["name"]][0 if program == "l2l3l4.py" else 1]
            assert out["result"]["verdict"] == want, (program, preset["name"])
```

- [ ] **Step 3: Write the preset generator and generate**

Create `web/scripts/gen_presets.py`:

```python
"""Generate web/public/presets.json: the demo corpus + the nanukproto
tunnel as {name, hex, note}. Runs offline in the devcontainer (scapy
lives there); only hex strings ship — scapy never enters the bundle."""

import json
import pathlib
import struct

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Dot1Q, Ether
from scapy.packet import Raw

DMAC = "aa:bb:cc:dd:ee:01"
OUT = pathlib.Path(__file__).resolve().parents[1] / "public" / "presets.json"


def nk_tunnel() -> bytes:
    nk = (struct.pack(">H", 0x4E4B) + bytes([1 << 4])
          + (0x0ABCDE).to_bytes(3, "big") + struct.pack(">H", 0x0800))
    inner = bytes(IP(dst="10.0.0.2") / UDP(dport=4242) / Raw(b"hi"))
    eth = bytes.fromhex("aabbccddee01") + bytes(6) + struct.pack(">H", 0x88B5)
    return eth + nk + inner


PRESETS = [
    ("plain_ipv4_udp", Ether(dst=DMAC) / IP(dst="10.0.0.1") / UDP(dport=53) / Raw(b"hi"),
     "Ethernet / IPv4 / UDP"),
    ("single_vlan", Ether(dst=DMAC) / Dot1Q(vlan=100) / IP() / UDP(dport=4789),
     "one 802.1Q tag"),
    ("qinq", Ether(dst=DMAC) / Dot1Q(vlan=200) / Dot1Q(vlan=300) / IP() / UDP(dport=53),
     "stacked VLANs (QinQ)"),
    ("ipv4_options", Ether(dst=DMAC) / IP(options=b"\x01\x01\x01\x01") / UDP(dport=53),
     "IPv4 with options (IHL > 5)"),
    ("ipv4_tcp", Ether(dst=DMAC) / IP() / TCP(dport=80), "TCP: accepted, no UDP header"),
    ("arp", Ether(dst=DMAC) / ARP(pdst="10.0.0.1"), "unknown EtherType: accept"),
    ("runt_frame", bytes(10), "10 bytes: header violation"),
    ("non_v4_version", Ether(dst=DMAC, type=0x0800) / Raw(b"\x60" + bytes(39)),
     "IPv4 EtherType but version 6: drop"),
    ("nk_tunnel", nk_tunnel(), "the invented nanukproto tunnel (beat 3)"),
]

OUT.write_text(json.dumps(
    [{"name": n, "hex": bytes(p).hex(), "note": note} for n, p, note in PRESETS],
    indent=2,
) + "\n")
print(f"wrote {OUT} ({len(PRESETS)} presets)")
```

Run: `./dev.sh bash -lc 'cd lang && uv run python ../web/scripts/gen_presets.py'`
Expected: `wrote .../web/public/presets.json (9 presets)`

- [ ] **Step 4: Run the tests**

Run: `./dev.sh bash -lc 'cd web/py && uv run --group dev pytest tests -q'`
Expected: 14 passed. (If `nk_tunnel` or `non_v4_version` verdicts differ, trust the bridge output and re-check EXPECTED against `lang/tests` corpus behavior — the golden model is the arbiter, not this table.)

- [ ] **Step 5: Commit**

```bash
git add web/src/programs/ web/scripts/gen_presets.py web/public/presets.json web/py/tests/test_presets.py
git commit -m "Ship example programs and baked preset packets for the playground

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Wheels + Pyodide runtime + Node integration smoke test

**Files:**
- Create: `web/scripts/build_wheels.sh`, `web/src/lib/py.ts`, `web/src/lib/runtime-browser.ts`, `web/tests/pyodide.test.ts`

**Interfaces:**
- Consumes: Task 1 types + `PYODIDE_VERSION`/`PYODIDE_CDN`; Task 3 bridge globals `compile_source`/`run_packet`; Task 4 program assets.
- Produces: `initRuntime(opts: InitOpts): Promise<NanukRuntime>` in `py.ts` where `NanukRuntime = { compile(source: string): CompileResult; run(packetHex: string): RunResult }` and `InitOpts = { loadPyodide: LoadPyodideFn; wheelUrls: string[]; bridgeSource: string; onStatus?: (msg: string) => void }`; `initBrowserRuntime(onStatus): Promise<NanukRuntime>` in `runtime-browser.ts` (fetches manifest/bridge, CDN-imports Pyodide). Task 6 calls `initBrowserRuntime`.

- [ ] **Step 1: The wheel build script**

Create `web/scripts/build_wheels.sh` (mark executable):

```bash
#!/usr/bin/env bash
# Build nanuk-ir + nanuk-lang wheels into web/public/wheels/ with a
# manifest, and copy the bridge next to them. Run via uv (devcontainer
# or any host with uv).
set -euo pipefail
WEB="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(dirname "$WEB")"
OUT="$WEB/public/wheels"
rm -rf "$OUT" && mkdir -p "$OUT"
(cd "$REPO/compiler" && uv build --wheel --out-dir "$OUT" --quiet)
(cd "$REPO/lang" && uv build --wheel --out-dir "$OUT" --quiet)
cp "$WEB/py/bridge.py" "$WEB/public/bridge.py"
(cd "$OUT" && ls *.whl | python3 -c \
  'import json,sys; print(json.dumps({"wheels": sys.stdin.read().split()}))' \
  > "$OUT/manifest.json")
echo "wheels: $(cat "$OUT/manifest.json")"
```

Run: `./dev.sh bash -lc 'web/scripts/build_wheels.sh'`
Expected: `wheels: {"wheels": ["nanuk_ir-0.1.0-py3-none-any.whl", "nanuk_lang-0.1.0-py3-none-any.whl"]}`

- [ ] **Step 2: The runtime core (`py.ts`)**

Create `web/src/lib/py.ts`:

```ts
import type { CompileResult, RunResult } from './types';

// Matches both the npm package's and the CDN module's loadPyodide.
export type LoadPyodideFn = (opts?: { indexURL?: string }) => Promise<any>;

export interface InitOpts {
  loadPyodide: LoadPyodideFn;
  indexURL?: string;
  /** Wheel URLs or emfs: paths, dependency-first (nanuk-ir before nanuk-lang). */
  wheelUrls: string[];
  bridgeSource: string;
  onStatus?: (msg: string) => void;
}

export interface NanukRuntime {
  compile(source: string): CompileResult;
  run(packetHex: string): RunResult;
}

export async function initRuntime(opts: InitOpts): Promise<NanukRuntime> {
  const status = opts.onStatus ?? (() => {});
  status('loading Python runtime…');
  const py = await opts.loadPyodide({ indexURL: opts.indexURL });
  status('installing nanuk packages…');
  await py.loadPackage('micropip');
  const micropip = py.pyimport('micropip');
  for (const url of opts.wheelUrls) {
    await micropip.install(url); // resolves protobuf from PyPI as a dep
  }
  status('loading bridge…');
  py.runPython(opts.bridgeSource);
  const compileFn = py.globals.get('compile_source');
  const runFn = py.globals.get('run_packet');
  status('ready');
  return {
    compile: (source) => JSON.parse(compileFn(source)) as CompileResult,
    run: (packetHex) => JSON.parse(runFn(packetHex)) as RunResult,
  };
}
```

- [ ] **Step 3: The browser loader**

Create `web/src/lib/runtime-browser.ts`:

```ts
import { PYODIDE_CDN } from './pyodide-version';
import { initRuntime, type NanukRuntime } from './py';

const BASE = import.meta.env.BASE_URL;

export async function initBrowserRuntime(
  onStatus: (msg: string) => void,
): Promise<NanukRuntime> {
  const [{ loadPyodide }, manifest, bridgeSource] = await Promise.all([
    import(/* @vite-ignore */ `${PYODIDE_CDN}pyodide.mjs`),
    fetch(`${BASE}wheels/manifest.json`).then((r) => r.json()),
    fetch(`${BASE}bridge.py`).then((r) => r.text()),
  ]);
  const wheelUrls = (manifest.wheels as string[])
    .sort() // nanuk_ir before nanuk_lang, dependency-first
    .map((w) => new URL(`${BASE}wheels/${w}`, location.origin).href);
  return initRuntime({
    loadPyodide,
    indexURL: PYODIDE_CDN,
    wheelUrls,
    bridgeSource,
    onStatus,
  });
}
```

- [ ] **Step 4: The Node integration smoke test**

Create `web/tests/pyodide.test.ts`:

```ts
// The one true integration risk: wheels + protobuf + bridge inside a real
// Pyodide. Runs in Node via the npm pyodide package; needs network (PyPI
// for protobuf, CDN for micropip). ~1-2 min cold.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadPyodide } from 'pyodide';
import { initRuntime } from '../src/lib/py';

const WEB = join(__dirname, '..');
const WHEELS = join(WEB, 'public', 'wheels');

describe.skipIf(process.env.NANUK_SKIP_PYODIDE === '1')('pyodide integration', () => {
  it('compiles and runs the default program end to end', async () => {
    const py = await loadPyodide();
    py.FS.mkdir('/wheels');
    const names = readdirSync(WHEELS).filter((f) => f.endsWith('.whl')).sort();
    expect(names.length).toBe(2);
    for (const name of names) {
      py.FS.writeFile(`/wheels/${name}`, readFileSync(join(WHEELS, name)));
    }
    const runtime = await initRuntime({
      loadPyodide: async () => py, // reuse the loaded instance
      wheelUrls: names.map((n) => `emfs:/wheels/${n}`),
      bridgeSource: readFileSync(join(WEB, 'py', 'bridge.py'), 'utf8'),
    });
    const source = readFileSync(join(WEB, 'src', 'programs', 'l2l3l4.py'), 'utf8');
    const compiled = runtime.compile(source);
    expect(compiled.ok).toBe(true);
    if (!compiled.ok) return;
    expect(compiled.states.map((s) => s.name)).toContain('udp_hdr');

    const presets = JSON.parse(readFileSync(join(WEB, 'public', 'presets.json'), 'utf8'));
    const plain = presets.find((p: any) => p.name === 'plain_ipv4_udp');
    const run = runtime.run(plain.hex);
    expect(run.ok).toBe(true);
    if (run.ok) {
      expect(run.result.verdict).toBe(0);
      expect(run.result.smd.slice(0, 3)).toEqual([0xaabb, 0xccdd, 0xee01]);
    }
  });
});
```

- [ ] **Step 5: Run it**

Run (host, in `web/`, wheels built in Step 1): `npm test`
Expected: 1 passed (allow ~2 min). If micropip fails resolving `protobuf>=7.35` from PyPI (no pure wheel), pin the fallback explicitly: `await micropip.install('protobuf')` before the wheel loop in `py.ts` and re-run; if that also fails, STOP and raise the issue rather than vendoring.

- [ ] **Step 6: Commit**

```bash
git add web/scripts/build_wheels.sh web/src/lib/py.ts web/src/lib/runtime-browser.ts web/tests/pyodide.test.ts
git commit -m "Load the real nanuk wheels into Pyodide behind a typed runtime API

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Panes, provenance highlighting, compile loop

**Files:**
- Create: `web/src/lib/stores.ts`, `web/src/lib/panes/highlight.ts`, `web/src/lib/panes/highlight.test.ts`, `web/src/lib/panes/CodePane.svelte`, `web/src/app.css`
- Modify: `web/src/App.svelte` (replace placeholder), `web/src/main.ts` (import css)

**Interfaces:**
- Consumes: Task 5 `initBrowserRuntime`; Task 1 types; Task 4 `?raw` program assets.
- Produces: `CodePane` props `{ title: string; doc: string; editable: boolean; python: boolean; ranges: { name: string; range: LineRange }[]; onEdit?: (doc: string) => void }`; `hoveredState: Writable<string | null>` store; `lineRangesToRegions(doc, ranges)` helper. Task 7 slots `PacketPanel` into App's `<aside>`.

- [ ] **Step 1: Stores and the pure highlight helpers with a failing test**

Create `web/src/lib/stores.ts`:

```ts
import { writable } from 'svelte/store';

/** Name of the parser state under the cursor in any pane, or null. */
export const hoveredState = writable<string | null>(null);
```

Create `web/src/lib/panes/highlight.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { lineRangesToRegions, stateAtLine } from './highlight';

const ranges = [
  { name: 'start', range: [1, 3] as [number, number] },
  { name: 'vlan', range: [5, 6] as [number, number] },
];

describe('provenance range helpers', () => {
  it('maps doc lines to char regions', () => {
    const doc = 'a\nbb\nccc\n\ndd\ne\n';
    const regions = lineRangesToRegions(doc, ranges);
    expect(regions[0]).toEqual({ name: 'start', from: 0, to: 8 }); // "a\nbb\nccc"
    expect(regions[1]).toEqual({ name: 'vlan', from: 10, to: 14 }); // "dd\ne"
  });
  it('finds the state at a line', () => {
    expect(stateAtLine(ranges, 2)).toBe('start');
    expect(stateAtLine(ranges, 4)).toBeNull();
    expect(stateAtLine(ranges, 6)).toBe('vlan');
  });
  it('clamps ranges past the end of the doc', () => {
    const regions = lineRangesToRegions('a\nb\n', [{ name: 's', range: [1, 99] }]);
    expect(regions[0].to).toBe(3);
  });
});
```

Run (host, `web/`): `npm test -- highlight`
Expected: FAIL — `highlight.ts` doesn't exist.

- [ ] **Step 2: Implement `highlight.ts`**

Create `web/src/lib/panes/highlight.ts`:

```ts
import { StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, type DecorationSet } from '@codemirror/view';
import type { LineRange } from '../types';

export interface NamedRange { name: string; range: LineRange }
export interface Region { name: string; from: number; to: number }

/** Convert 1-based inclusive line ranges to character regions of `doc`. */
export function lineRangesToRegions(doc: string, ranges: NamedRange[]): Region[] {
  const lines = doc.split('\n');
  const starts: number[] = [0];
  for (const line of lines) starts.push(starts[starts.length - 1] + line.length + 1);
  const lastLine = lines.length;
  const docLen = doc.length;
  return ranges.map(({ name, range: [lo, hi] }) => {
    const l = Math.max(1, Math.min(lo, lastLine));
    const h = Math.max(l, Math.min(hi, lastLine));
    return {
      name,
      from: Math.min(starts[l - 1], docLen),
      to: Math.min(starts[h - 1] + lines[h - 1].length, docLen),
    };
  });
}

export function stateAtLine(ranges: NamedRange[], line: number): string | null {
  for (const { name, range: [lo, hi] } of ranges) {
    if (line >= lo && line <= hi) return name;
  }
  return null;
}

export const setHighlightRegion = StateEffect.define<Region | null>();

const stateHighlight = Decoration.mark({ class: 'cm-state-hl' });

export const highlightField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(setHighlightRegion)) {
        deco = e.value && e.value.to > e.value.from
          ? Decoration.set([stateHighlight.range(e.value.from, e.value.to)])
          : Decoration.none;
      }
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});
```

Run: `npm test -- highlight`
Expected: 3 passed

- [ ] **Step 3: The pane component**

Create `web/src/lib/panes/CodePane.svelte`:

```svelte
<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { EditorState } from '@codemirror/state';
  import { EditorView, keymap, lineNumbers } from '@codemirror/view';
  import { defaultKeymap } from '@codemirror/commands';
  import { python } from '@codemirror/lang-python';
  import { hoveredState } from '../stores';
  import {
    highlightField, lineRangesToRegions, setHighlightRegion, stateAtLine,
    type NamedRange,
  } from './highlight';

  let {
    title, doc, editable, python: isPython, ranges, onEdit,
  }: {
    title: string; doc: string; editable: boolean; python: boolean;
    ranges: NamedRange[]; onEdit?: (doc: string) => void;
  } = $props();

  let host: HTMLDivElement;
  let view: EditorView;

  onMount(() => {
    view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc,
        extensions: [
          lineNumbers(),
          keymap.of(defaultKeymap),
          ...(isPython ? [python()] : []),
          ...(editable ? [] : [EditorState.readOnly.of(true)]),
          highlightField,
          EditorView.updateListener.of((u) => {
            if (u.docChanged && onEdit) onEdit(u.state.doc.toString());
          }),
          EditorView.domEventHandlers({
            mousemove(event, v) {
              const pos = v.posAtCoords({ x: event.clientX, y: event.clientY });
              hoveredState.set(
                pos == null ? null : stateAtLine(ranges, v.state.doc.lineAt(pos).number),
              );
            },
            mouseleave() { hoveredState.set(null); },
          }),
        ],
      }),
    });
    return () => view.destroy();
  });

  // External doc replacement (IR/asm panes after recompile).
  $effect(() => {
    if (view && !editable && view.state.doc.toString() !== doc) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: doc } });
    }
  });

  // Apply the shared hover highlight to this pane's matching region.
  const unsub = hoveredState.subscribe((name) => {
    if (!view) return;
    const region = name
      ? lineRangesToRegions(view.state.doc.toString(), ranges)
          .find((r) => r.name === name) ?? null
      : null;
    view.dispatch({ effects: setHighlightRegion.of(region) });
  });
  onDestroy(unsub);
</script>

<section class="pane">
  <header>{title}</header>
  <div class="editor" bind:this={host}></div>
</section>

<style>
  .pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  header {
    font: 600 0.75rem/1.8 var(--font-ui); text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--fg-muted);
    border-bottom: 1px solid var(--border); padding: 0 0.5rem;
  }
  .editor { flex: 1; overflow: auto; }
  .editor :global(.cm-editor) { height: 100%; font-size: 0.85rem; }
  .editor :global(.cm-state-hl) { background: var(--hl); }
</style>
```

- [ ] **Step 4: App layout + compile loop**

Create `web/src/app.css`:

```css
@import '../site/shared.css';

html, body, #app { height: 100%; margin: 0; }
body { font-family: var(--font-ui); background: var(--bg); color: var(--fg); }
```

Replace `web/src/App.svelte`:

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import CodePane from './lib/panes/CodePane.svelte';
  import PacketPanel from './lib/PacketPanel.svelte';
  import { initBrowserRuntime } from './lib/runtime-browser';
  import type { NanukRuntime } from './lib/py';
  import type { CompileOk, BridgeError } from './lib/types';
  import type { NamedRange } from './lib/panes/highlight';
  import { parseParams } from './lib/params';
  import l2l3l4Src from './programs/l2l3l4.py?raw';
  import nanukprotoSrc from './programs/nanukproto.py?raw';

  const params = parseParams(location.search);
  let runtime: NanukRuntime | null = $state(null);
  let status = $state('starting…');
  let source = $state(params.program === 'nanukproto' ? nanukprotoSrc : l2l3l4Src);
  let compiled: CompileOk | null = $state(null);
  let compileError: BridgeError | null = $state(null);

  function recompile(src: string) {
    if (!runtime) return;
    const result = runtime.compile(src);
    if (result.ok) { compiled = result; compileError = null; }
    else compileError = result.error;
  }

  let timer: ReturnType<typeof setTimeout>;
  function onEdit(src: string) {
    source = src;
    clearTimeout(timer);
    timer = setTimeout(() => recompile(src), 300);
  }

  onMount(async () => {
    try {
      runtime = await initBrowserRuntime((s) => (status = s));
      recompile(source);
    } catch (e) {
      status = `failed to load: ${e}`;
    }
  });

  const edslRanges = $derived<NamedRange[]>(
    compiled?.states.filter((s) => s.edsl)
      .map((s) => ({ name: s.name, range: s.edsl! })) ?? [],
  );
  const irRanges = $derived<NamedRange[]>(
    compiled?.states.map((s) => ({ name: s.name, range: s.ir })) ?? [],
  );
  const asmRanges = $derived<NamedRange[]>(
    compiled?.states.map((s) => ({ name: s.name, range: s.asm })) ?? [],
  );
</script>

<div class="app">
  <header class="top">
    <a class="brand" href="/nanuk/">nanuk</a>
    <span class="title">playground</span>
    <span class="status" class:ready={status === 'ready'}>{status}</span>
  </header>
  <main>
    <div class="panes">
      <div class="edsl-col">
        <CodePane title="eDSL (Python)" doc={source} editable python
          ranges={edslRanges} {onEdit} />
        {#if compileError}
          <div class="banner" role="alert">
            <strong>{compileError.kind}</strong>
            {#if compileError.line}(line {compileError.line}){/if}:
            {compileError.message}
          </div>
        {/if}
      </div>
      <CodePane title="nanuk IR" doc={compiled?.ir_text ?? ''} editable={false}
        python={false} ranges={irRanges} />
      <CodePane title="assembly" doc={compiled?.asm_text ?? ''} editable={false}
        python={false} ranges={asmRanges} />
    </div>
    <aside>
      <PacketPanel {runtime} ready={compiled !== null}
        initialPacket={params.packet} initialPreset={params.preset} />
    </aside>
  </main>
</div>

<style>
  .app { height: 100%; display: flex; flex-direction: column; }
  .top {
    display: flex; align-items: baseline; gap: 0.6rem;
    padding: 0.4rem 0.8rem; border-bottom: 1px solid var(--border);
  }
  .brand { font-weight: 700; color: var(--accent); text-decoration: none; }
  .status { margin-left: auto; font-size: 0.8rem; color: var(--fg-muted); }
  .status.ready { color: var(--ok); }
  main { flex: 1; display: flex; min-height: 0; }
  .panes {
    flex: 1; display: grid; grid-template-columns: 1.2fr 1fr 1fr;
    gap: 1px; background: var(--border); min-width: 0;
  }
  .edsl-col { display: flex; flex-direction: column; min-width: 0; background: var(--bg); }
  .banner {
    padding: 0.5rem 0.8rem; font-size: 0.85rem;
    background: var(--err-bg); color: var(--err); border-top: 1px solid var(--border);
  }
  aside { width: 20rem; border-left: 1px solid var(--border); overflow: auto; }
</style>
```

Update `web/src/main.ts` to import the stylesheet:

```ts
import './app.css';
import { mount } from 'svelte';
import App from './App.svelte';

mount(App, { target: document.getElementById('app')! });
```

Note: `App.svelte` imports `PacketPanel` and `params.ts` which land in Task 7. To keep this task buildable, create the two stubs now exactly as follows — `web/src/lib/params.ts` with `export interface Params { program: string | null; preset: string | null; packet: string | null } export function parseParams(_search: string): Params { return { program: null, preset: null, packet: null }; }` and `web/src/lib/PacketPanel.svelte` with a `<script lang="ts">let { runtime, ready, initialPacket, initialPreset } = $props();</script><p class="ph">packet panel: Task 7</p>` body. Task 7 replaces both.

- [ ] **Step 5: Verify build + tests, then look at it**

Run (host, `web/`): `NANUK_SKIP_PYODIDE=1 npm test && npm run build`
Expected: highlight tests pass, svelte-check clean, build succeeds.

Then: `npm run dev` and open `http://localhost:5173/nanuk/play/`. Verify: three panes render, default program compiles after Pyodide loads (status walks through loading→ready), hovering a state function in the eDSL pane highlights the matching IR and asm blocks (and vice versa), editing the source recompiles after 300 ms, an introduced syntax error shows the banner with a line number.

- [ ] **Step 6: Commit**

```bash
git add web/src/
git commit -m "Three synchronized panes with state-level hover provenance

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Packet panel, result view, URL params

**Files:**
- Create: `web/src/lib/ResultView.svelte`, `web/src/lib/params.test.ts`
- Replace: `web/src/lib/PacketPanel.svelte`, `web/src/lib/params.ts` (Task 6 stubs)

**Interfaces:**
- Consumes: `NanukRuntime.run`, `presets.json` (fetched at `BASE_URL`), `ParseResultJson`.
- Produces: final `PacketPanel` with props `{ runtime: NanukRuntime | null; ready: boolean; initialPacket: string | null; initialPreset: string | null }`; `parseParams(search: string): Params` reading `program`, `preset`, `packet`.

- [ ] **Step 1: params with a failing test**

Create `web/src/lib/params.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { parseParams } from './params';

describe('parseParams', () => {
  it('reads program, preset, packet', () => {
    expect(parseParams('?program=nanukproto&preset=qinq&packet=aabb')).toEqual({
      program: 'nanukproto', preset: 'qinq', packet: 'aabb',
    });
  });
  it('rejects unknown program names', () => {
    expect(parseParams('?program=evil').program).toBeNull();
  });
  it('handles empty search', () => {
    expect(parseParams('')).toEqual({ program: null, preset: null, packet: null });
  });
});
```

Run: `npm test -- params` — Expected: FAIL (stub returns nulls for the first case).

- [ ] **Step 2: Implement params**

Replace `web/src/lib/params.ts`:

```ts
export interface Params {
  program: string | null; // validated: 'l2l3l4' | 'nanukproto'
  preset: string | null;  // preset name, resolved against presets.json later
  packet: string | null;  // raw hex, validated by the bridge on run
}

const PROGRAMS = new Set(['l2l3l4', 'nanukproto']);

export function parseParams(search: string): Params {
  const q = new URLSearchParams(search);
  const program = q.get('program');
  return {
    program: program && PROGRAMS.has(program) ? program : null,
    preset: q.get('preset'),
    packet: q.get('packet'),
  };
}
```

Run: `npm test -- params` — Expected: 3 passed.

- [ ] **Step 3: ResultView**

Create `web/src/lib/ResultView.svelte`:

```svelte
<script lang="ts">
  import type { ParseResultJson } from './types';
  let { result }: { result: ParseResultJson } = $props();

  const VERDICTS = ['accept', 'drop', 'error'] as const;
  const ERRORS = ['none', 'header violation', 'step budget', 'illegal', 'pc range', 'smd range'];
  const hex = (v: number) => '0x' + v.toString(16).padStart(4, '0');
</script>

<div class="result">
  <p>
    <span class="badge v{result.verdict}">{VERDICTS[result.verdict]}</span>
    {#if result.verdict === 2}<span class="err">{ERRORS[result.error]}</span>{/if}
    <span class="meta">payload@{result.payload_offset} · {result.steps} steps</span>
  </p>
  <table>
    <caption>headers</caption>
    <tbody>
      {#each result.hdr_present as present, id}
        {#if present}
          <tr><td>hdr[{id}]</td><td>offset {result.hdr_offset[id]}</td></tr>
        {/if}
      {/each}
    </tbody>
  </table>
  <table>
    <caption>SMD</caption>
    <tbody>
      {#each result.smd as slot, i}
        <tr class:zero={slot === 0}><td>[{i}]</td><td>{hex(slot)}</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .result { font-size: 0.85rem; }
  .badge { padding: 0.1rem 0.5rem; border-radius: 999px; font-weight: 700; color: #fff; }
  .badge.v0 { background: var(--ok); }
  .badge.v1 { background: var(--warn); }
  .badge.v2 { background: var(--err); }
  .err { color: var(--err); margin-left: 0.4rem; }
  .meta { color: var(--fg-muted); margin-left: 0.4rem; }
  table { width: 100%; border-collapse: collapse; margin-top: 0.6rem; }
  caption { text-align: left; font-weight: 600; color: var(--fg-muted); }
  td { border-top: 1px solid var(--border); padding: 0.15rem 0.3rem; font-family: var(--font-mono); }
  tr.zero td { color: var(--fg-muted); }
</style>
```

- [ ] **Step 4: PacketPanel**

Replace `web/src/lib/PacketPanel.svelte`:

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import type { NanukRuntime } from './py';
  import type { ParseResultJson, BridgeError } from './types';
  import ResultView from './ResultView.svelte';

  let { runtime, ready, initialPacket, initialPreset }: {
    runtime: NanukRuntime | null; ready: boolean;
    initialPacket: string | null; initialPreset: string | null;
  } = $props();

  interface Preset { name: string; hex: string; note: string }
  let presets: Preset[] = $state([]);
  let packetHex = $state(initialPacket ?? '');
  let result: ParseResultJson | null = $state(null);
  let error: BridgeError | null = $state(null);

  onMount(async () => {
    presets = await fetch(`${import.meta.env.BASE_URL}presets.json`).then((r) => r.json());
    if (!initialPacket && initialPreset) {
      const p = presets.find((p) => p.name === initialPreset);
      if (p) packetHex = p.hex;
    }
  });

  function run() {
    if (!runtime) return;
    const out = runtime.run(packetHex);
    if (out.ok) { result = out.result; error = null; }
    else { error = out.error; result = null; }
  }
</script>

<div class="panel">
  <h2>packet</h2>
  <div class="chips">
    {#each presets as p}
      <button class="chip" title={p.note}
        onclick={() => { packetHex = p.hex; if (ready) run(); }}>{p.name}</button>
    {/each}
  </div>
  <textarea rows="4" bind:value={packetHex}
    placeholder="hex bytes, e.g. aabbccddee01…" spellcheck="false"></textarea>
  <button class="run" disabled={!ready || !packetHex.trim()} onclick={run}>
    Run packet
  </button>
  {#if error}<p class="error">{error.message}</p>{/if}
  {#if result}<ResultView {result} />{/if}
</div>

<style>
  .panel { padding: 0.8rem; display: flex; flex-direction: column; gap: 0.6rem; }
  h2 { margin: 0; font-size: 0.75rem; text-transform: uppercase;
       letter-spacing: 0.08em; color: var(--fg-muted); }
  .chips { display: flex; flex-wrap: wrap; gap: 0.3rem; }
  .chip { font-size: 0.75rem; padding: 0.15rem 0.5rem; border-radius: 999px;
          border: 1px solid var(--border); background: none; color: var(--fg);
          cursor: pointer; }
  .chip:hover { border-color: var(--accent); color: var(--accent); }
  textarea { font-family: var(--font-mono); font-size: 0.8rem;
             background: var(--bg-inset); color: var(--fg);
             border: 1px solid var(--border); border-radius: 4px; padding: 0.4rem; }
  .run { padding: 0.4rem; border-radius: 4px; border: none; font-weight: 600;
         background: var(--accent); color: #fff; cursor: pointer; }
  .run:disabled { opacity: 0.5; cursor: default; }
  .error { color: var(--err); font-size: 0.85rem; margin: 0; }
</style>
```

- [ ] **Step 5: Verify + manual check**

Run: `NANUK_SKIP_PYODIDE=1 npm test && npm run build` — Expected: 6 unit tests pass, build clean.
Then `npm run dev`: preset chips appear and fill the hex box, Run shows verdict/tables (try `qinq` → accept with VLAN header row; `runt_frame` → error badge "header violation"). Check `http://localhost:5173/nanuk/play/?program=nanukproto&preset=nk_tunnel` opens nanukproto with the tunnel packet prefilled and running it accepts with hdr[5] present.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/
git commit -m "Packet panel with preset chips, result view, and URL-param embedding

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Landing page + shared stylesheet

**Files:**
- Create: `web/site/shared.css`, `web/site/index.html`

**Interfaces:**
- Consumes: nothing. Produces: the `/` page and the CSS custom properties (`--bg`, `--bg-inset`, `--fg`, `--fg-muted`, `--border`, `--accent`, `--ok`, `--warn`, `--err`, `--err-bg`, `--hl`, `--font-ui`, `--font-mono`) that Task 6/7 styles already reference; Task 9 copies `web/site/*` to the artifact root.

- [ ] **Step 1: The shared tokens**

Create `web/site/shared.css`:

```css
/* nanuk site look tokens — the ONLY styling shared between the
   hand-written landing page and the playground SPA. */
:root {
  --font-ui: system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Code", Menlo, monospace;
  --bg: #ffffff;
  --bg-inset: #f6f8fa;
  --fg: #1f2328;
  --fg-muted: #59636e;
  --border: #d1d9e0;
  --accent: #0969da;
  --ok: #1a7f37;
  --warn: #9a6700;
  --err: #d1242f;
  --err-bg: #fff1f2;
  --hl: rgba(9, 105, 218, 0.12);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --bg-inset: #161b22;
    --fg: #e6edf3;
    --fg-muted: #8d96a0;
    --border: #30363d;
    --accent: #4493f8;
    --ok: #3fb950;
    --warn: #d29922;
    --err: #f85149;
    --err-bg: #2d1216;
    --hl: rgba(68, 147, 248, 0.16);
  }
}
```

- [ ] **Step 2: The landing page**

Create `web/site/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>nanuk — a programmable packet processor, from chip to language</title>
  <meta name="description"
    content="nanuk: an educational programmable packet parser — formal Sail ISA,
    golden-model emulator, Python eDSL, protobuf IR, RTL core, end-to-end
    network demo. Try it in the browser." />
  <link rel="stylesheet" href="./shared.css" />
  <style>
    body {
      margin: 0; font-family: var(--font-ui); background: var(--bg);
      color: var(--fg); display: grid; place-items: center; min-height: 100vh;
    }
    main { max-width: 40rem; padding: 2rem; text-align: center; }
    h1 { font-size: 3rem; margin: 0; }
    h1 .bear { margin-right: 0.3rem; }
    .tagline { color: var(--fg-muted); font-size: 1.15rem; margin: 0.5rem 0 2rem; }
    .links { display: flex; gap: 0.8rem; justify-content: center; flex-wrap: wrap; }
    .links a {
      padding: 0.55rem 1.1rem; border-radius: 6px; text-decoration: none;
      font-weight: 600; border: 1px solid var(--border); color: var(--fg);
    }
    .links a.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .links a:hover { border-color: var(--accent); }
    .stack { margin-top: 2.5rem; color: var(--fg-muted); font-size: 0.9rem;
             font-family: var(--font-mono); }
  </style>
</head>
<body>
  <main>
    <h1><span class="bear">🐻‍❄️</span>nanuk</h1>
    <p class="tagline">Building a programmable packet processor,
      from chip to programming language.</p>
    <nav class="links">
      <a class="primary" href="./play/">Playground</a>
      <a href="https://github.com/qobilidop/nanuk">GitHub</a>
    </nav>
    <p class="stack">Sail ISA → golden model → Python eDSL → IR → RTL → real packets</p>
  </main>
</body>
</html>
```

- [ ] **Step 3: Verify locally**

Run (host): `open web/site/index.html` — page renders in light and dark mode (flip system theme), links work (playground link 404s locally; it resolves on the composed artifact).

- [ ] **Step 4: Commit**

```bash
git add web/site/
git commit -m "Hand-written landing page and shared site look tokens

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Pages deploy workflow, CONTRIBUTING.md, full verification

**Files:**
- Create: `.github/workflows/pages.yml`, `CONTRIBUTING.md`
- Modify: `README.md` (site links)

**Interfaces:** none — final assembly.

- [ ] **Step 1: The workflow**

Create `.github/workflows/pages.yml`:

```yaml
name: Pages

on:
  push:
    branches: [main]
    paths: ['web/**', 'lang/**', 'compiler/**', '.github/workflows/pages.yml']
  pull_request:
    paths: ['web/**', 'lang/**', 'compiler/**', '.github/workflows/pages.yml']
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: web/package-lock.json

      - name: Build wheels + bridge assets
        run: web/scripts/build_wheels.sh

      - name: Install, test, build the SPA
        working-directory: web
        run: |
          npm ci
          npm test
          npm run build

      - name: Compose the site artifact
        run: |
          mkdir -p _site
          cp web/site/* _site/
          mkdir -p _site/play
          cp -r web/dist/* _site/play/

      - name: Upload Pages artifact
        if: github.event_name != 'pull_request'
        uses: actions/upload-pages-artifact@v3
        with:
          path: _site

  deploy:
    if: github.event_name != 'pull_request'
    needs: build
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

Note: `npm ci` requires `web/package-lock.json` — confirm it was committed in Task 1 (`git ls-files web/package-lock.json`); if not, commit it now.

- [ ] **Step 2: CONTRIBUTING.md**

Create `CONTRIBUTING.md`:

```markdown
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
```

- [ ] **Step 3: README links**

In `README.md`, under the title line, add:

```markdown
**[Landing page](https://qobilidop.github.io/nanuk/)** ·
**[Playground](https://qobilidop.github.io/nanuk/play/)** — the eDSL, IR,
and assembly, live in your browser.
```

- [ ] **Step 4: Full verification**

Run:
1. `./dev.sh bash -lc 'set -e; cmake -B build >/dev/null && cmake --build build >/dev/null && ctest --test-dir build --output-on-failure | tail -1; (cd spec/python && uv run pytest -q | tail -1); (cd hw && NANUK_COSIM=1 uv run pytest tests -q | tail -1); (cd lang && NANUK_COSIM=1 uv run --group dev pytest tests -q | tail -1); (cd compiler && NANUK_COSIM=1 uv run --group dev pytest tests -q | tail -1); (cd web/py && uv run --group dev pytest tests -q | tail -1)'`
2. `./dev.sh bash -lc 'web/scripts/build_wheels.sh'` then (host) `cd web && npm test && npm run build`
3. Compose locally and eyeball: `mkdir -p /tmp/nanuk-site && cp web/site/* /tmp/nanuk-site/ && mkdir -p /tmp/nanuk-site/play && cp -r web/dist/* /tmp/nanuk-site/play/ && python3 -m http.server -d /tmp/nanuk-site 8888` → check `http://localhost:8888/` and note the playground needs the `/nanuk/play/` base, so full click-through happens post-deploy.

Expected: every suite green; the Pyodide integration test passes with network.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/pages.yml CONTRIBUTING.md README.md
git commit -m "Deploy the composed site to GitHub Pages; add CONTRIBUTING

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

After push (user's call): enable Pages "GitHub Actions" source in repo settings if not already, watch the `Pages` workflow, then click through `https://qobilidop.github.io/nanuk/` → Playground → hover provenance → run `qinq` preset → verify the `?program=nanukproto&preset=nk_tunnel` deep link.

---

## Self-Review

**Spec coverage:** three panes + state provenance (T6), op-level ordered-walk (T2/T3 + spec amendment in T3), packet panel with presets + result view (T4/T7), URL-param embedding contract (T7), Pyodide + wheels of real packages, no nanuk-spec/scapy (T5, gitignored wheels), landing + shared tokens (T8), composed Pages deploy with PR build gate (T9), bridge pytest in CI (T3), CONTRIBUTING side commit (T9). Deliberately out per spec: source spans, ISS/diffing, traces, Playwright.

**Placeholder scan:** Task 4's program assets reference source files by line range instead of duplicating ~150 lines — the referenced files are in-repo and the ranges are exact; acceptable as the single deviation from repeat-the-code (the content must stay verbatim-identical to survive `test_presets.py`). Task 6 defines its two forward stubs inline. No TBDs remain.

**Type consistency:** `compile_source`/`run_packet` names match between bridge (T3), `py.ts` globals lookup (T5), and tests; `CompileResult`/`RunResult`/`ParseResultJson`/`StateProvenance` field names match bridge JSON keys exactly (snake_case retained in TS); `NamedRange`/`lineRangesToRegions`/`stateAtLine`/`setHighlightRegion`/`highlightField` names consistent across T6 files; `PacketPanel` props identical between T6 stub and T7 final; CSS custom properties used in T6/T7 all defined in T8's `shared.css`; wheel sort order (`nanuk_ir` < `nanuk_lang`) satisfies dependency-first in both T5 call sites.

**Risks called out where they bite:** protobuf-in-Pyodide resolution (T5 Step 5 fallback, stop-don't-vendor), `npm ci` lockfile presence (T9 Step 1), preset verdict table subordinate to the golden model (T4 Step 4).
