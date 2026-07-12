# Playground v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assembly-level ISS for both Nanuk ISAs in a new shipped `nanuk-isa` package, plus a step-scrubber debugger in the playground that walks IR-interp and ISS traces in lockstep with live divergence detection.

**Architecture:** Extract the assemblers/encodings from `nanuk-spec` into dependency-free `spec/isa/` (package `nanuk_isa`); add `iss.py`/`iss_map.py` executing assembled 32-bit words with full trace recording, mirroring `exec.sail` exactly (fourth implementation, tripwired differentially against `nanuk-emu`/`nanuk-map-emu`). The interp grows an optional trace recorder; the step counter is the shared clock (interp mirrors the lowering cost model instruction-for-instruction). The bridge assembles per-step aligned trace JSON with divergence verdicts; the UI adds a debugger strip + execution-line highlighting.

**Tech Stack:** Python 3.12 (uv, hatchling), Sail models as semantic reference, Svelte 5 + CodeMirror 6, Pyodide 314.0.2.

**Spec:** `docs/superpowers/specs/2026-07-11-playground-v2-design.md`

## Global Constraints

- `nanuk-isa` has ZERO runtime dependencies (no scapy, no protobuf). scapy must never enter a shipped wheel.
- ISS semantics mirror the Sail models EXACTLY: same error codes, same step accounting (budget checked before execute, counted at fetch), same field layouts. Cross-layer constant duplication is doctrine (mirror-with-tripwire), not debt — do NOT import constants across layers.
- Trace/step accounting: interp cost model is unchanged; the ISS must agree with the emulator on `steps` for every packet.
- No re-export shims: call sites move to `nanuk_isa.*` imports outright.
- Register values are 64-bit → JSON carries them as hex strings, never numbers.
- Verify in the devcontainer (authoritative): `docker run --rm -v $PWD:/workspace -w /workspace <devcontainer-image> bash -c '<cmd>'`, or on host where pure-Python. Commit per task.
- All error message formats pinned by existing tests must not change.

---

### Task 1: Extract `nanuk-isa` package

**Files:**
- Create: `spec/isa/pyproject.toml`, `spec/isa/nanuk_isa/__init__.py`, `spec/isa/tests/` (move encoding/asm tests here)
- Move (git mv): `spec/python/nanuk_spec/{_asm_core.py,encoding.py,asm.py,map_encoding.py,map_asm.py}` → `spec/isa/nanuk_isa/`
- Move (git mv): `spec/python/tests/{test_encoding.py,test_asm.py,test_map_encoding.py,test_map_asm.py}` → `spec/isa/tests/`
- Modify: `spec/python/pyproject.toml`, plus every import call site (list below), `.github/workflows/ci.yml`, `web/scripts/build_wheels.sh` is Task 11

**Interfaces:**
- Produces: importable `nanuk_isa.asm.assemble`, `nanuk_isa.map_asm.assemble`, `nanuk_isa.encoding.*`, `nanuk_isa.map_encoding.*`, `nanuk_isa._asm_core.*` — signatures unchanged.
- CLI scripts `nanuk-asm`/`nanuk-map-asm` move to nanuk-isa's `[project.scripts]` (`nanuk_isa.asm:main`, `nanuk_isa.map_asm:main`); nanuk-spec drops its script entry.

- [ ] **Step 1: Create the package and move the modules**

`spec/isa/pyproject.toml`:

```toml
[project]
name = "nanuk-isa"
version = "0.1.0"
description = "nanuk ISA v0 mirror: assemblers, encodings, and instruction-set simulators for both engines"
requires-python = ">=3.12"
dependencies = []

[project.scripts]
nanuk-asm = "nanuk_isa.asm:main"
nanuk-map-asm = "nanuk_isa.map_asm:main"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.15.21"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["nanuk_isa"]
```

`git mv` the five modules and four test files. `nanuk_isa/__init__.py`: module docstring only ("Python mirror of the two Nanuk ISAs: assemblers, encodings, ISS. Sail owns the truth (spec/parser-model, spec/map-model); the spec test suites tripwire drift."). Intra-package relative imports (`from . import encoding`, `from ._asm_core import ...`) survive the move unchanged.

- [ ] **Step 2: Update nanuk-spec + dependents**

`spec/python/pyproject.toml`: add `"nanuk-isa"` to dependencies, drop `[project.scripts]`, add `[tool.uv.sources] nanuk-isa = { path = "../isa", editable = true }`.

Update imports in the call sites found by grep (`nanuk_spec.asm|encoding|map_asm|map_encoding|_asm_core` → `nanuk_isa.…`; `from nanuk_spec import asm` → `from nanuk_isa import asm`):
compiler/tests/{test_differential,test_differential_map,test_lower_map,test_standalone}.py, hw/nanuk_hw/sim_util.py, hw/tests/{test_core,test_cosim,test_fuzz,test_map_core,test_map_cosim}.py, lang/tests/{test_compile,test_interp_parity,test_map_parity,test_nanukproto,test_parity,test_symex_parity}.py, spec/python/tests/{test_demo,test_harness,test_map_demo_l2fwd,test_map_demo_ttl,test_map_demo_tunnel,test_map_harness}.py, and the moved test files. Check hw/simbricks/*.sh for `nanuk-asm`/`python -m nanuk_spec.asm` invocations and repoint.

Then re-grep to confirm zero remaining references:
Run: `grep -rn "nanuk_spec\.\(asm\|encoding\|map_asm\|map_encoding\|_asm_core\)\|from nanuk_spec import \(asm\|encoding\|map_asm\|map_encoding\)" --include="*.py" --include="*.sh" . | grep -v __pycache__`
Expected: no output.

- [ ] **Step 3: Wire CI**

`.github/workflows/ci.yml` runCmd: ruff line gains `../isa` (path relative to spec/python: `../isa`); add `(cd spec/isa && uv sync --quiet && uv run --group dev pytest -q)` before the spec/python line.

- [ ] **Step 4: Verify everything is still green**

Run (devcontainer): full ctest + all pytest suites (spec/isa, spec/python, hw with NANUK_COSIM=1, lang, compiler, web/py) + ruff.
Expected: all pass, counts unchanged (tests moved, not removed).

- [ ] **Step 5: Commit** — `v2 (1): extract nanuk-isa — assemblers/encodings leave nanuk-spec`

---

### Task 2: `assemble_with_lines` (both assemblers)

**Files:**
- Modify: `spec/isa/nanuk_isa/_asm_core.py`, `asm.py`, `map_asm.py`
- Test: `spec/isa/tests/test_asm.py`, `spec/isa/tests/test_map_asm.py`

**Interfaces:**
- Produces: `assemble_with_lines(text: str) -> tuple[bytes, list[int]]` in both `nanuk_isa.asm` and `nanuk_isa.map_asm`: same binary as `assemble`, plus `line_map[i]` = 1-based source line of word i.

- [ ] **Step 1: Failing tests**

```python
def test_assemble_with_lines():
    src = "; c\n.equ N 2\nstart:\n    advi N\n    halt accept\n"
    binary, lines = asm.assemble_with_lines(src)
    assert binary == asm.assemble(src)
    assert lines == [4, 5]
```

(MAP twin: `movi r0, 1` / `drop` at lines 2-3 of a 3-line source.)

- [ ] **Step 2: Implement** — in each assembler, factor the word-emission loop into `_assemble_words(text) -> tuple[list[int], list[Line]]`; `assemble` returns `to_binary(words)`; `assemble_with_lines` returns `(to_binary(words), [line.lineno for line in program])`. No behavior change to `assemble`.

- [ ] **Step 3: Tests pass; full spec/isa suite green. Commit** — `v2 (2): assemble_with_lines — word→source-line map from the assemblers`

---

### Task 3: Parser ISS (`nanuk_isa.iss`)

**Files:**
- Create: `spec/isa/nanuk_isa/iss.py`
- Test: `spec/isa/tests/test_iss.py` (unit, dep-free), `spec/python/tests/test_iss_differential.py` (vs nanuk-emu; scapy corpus lives in nanuk-spec)

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class IssStep:
    pc: int              # word index BEFORE execute
    line: int | None     # 1-based asm source line (None without line_map)
    regs: tuple[int, int, int, int]   # AFTER execute
    cursor: int          # AFTER execute
    hdr_present: tuple[int, ...]      # 16, AFTER
    hdr_offset: tuple[int, ...]       # 16, AFTER
    smd: tuple[int, ...]              # 8, AFTER

@dataclass(frozen=True)
class IssResult:   # first 7 fields mirror harness.ParseResult
    verdict: int; error: int; payload_offset: int; steps: int
    hdr_present: list[int]; hdr_offset: list[int]; smd: list[int]
    trace: list[IssStep]

def run_iss(prog: bytes, packet: bytes, *, line_map: list[int] | None = None) -> IssResult
```

`prog` = big-endian 32-bit words (the assembler's output). Constants mirrored locally (doctrine): `BUF_BYTES=256, IMEM_WORDS=1024, NHDR=16, SMD_SLOTS=8, STEP_BUDGET=256`; verdicts 0/1/2; errors 0..5 (`ERR_HDR_VIOLATION=1, ERR_STEP_BUDGET=2, ERR_ILLEGAL=3, ERR_PC_RANGE=4, ERR_SMD_RANGE=5`).

- [ ] **Step 1: Failing unit tests** — assemble tiny programs with `nanuk_isa.asm` and assert against hand-computed results. Required cases:
  - `movi r0, 5; advr r0; halt accept` → payload_offset 5, steps 3, trace lines [1,2,3] (with line_map), regs after step 1 = (5,0,0,0).
  - EXT semantics: packet `b"\xAB\xCD"`, `ext r1, 4, 8; halt accept` → r1 == 0xBC.
  - Each error path: hdr violation (`advi 300` on a 64-byte packet → verdict 2/err 1, partial trace preserved); step budget (`start: jmp start` → err 2, steps == 256, len(trace) == 256); illegal (all-zeros word: `prog=b"\x00\x00\x00\x00"` → err 3, steps 1; also `movi r0, 0` alone → run-off fetches the implicit zero word at pc 1 → err 3 at step 2); pc range (err 4 is unreachable from a real program within budget 256 < imem 1024 — test the step-order directly: construct the internal machine, set `pc = 1024`, single-step, assert err 4; and separately assert the budget check precedes the pc check when both apply); smd range (the assembler's encoder rejects slot 7 with 2 units, so build the raw word `(0x0A<<26)|(0<<23)|(1<<21)|(7<<17)` → err 5).
  - Reserved-bits enforcement: `encode_movi("r0", 1) | (1 << 22)` (a reserved bit) → err 3; reg code 5: `(0x04<<26)|(5<<23)` → err 3.
  - SHL truncation: movi + shl by 63 then 1 more shift wraps to 0 (64-bit truncation).
  - STMD MSB-first multiunit: `movi r0, 0x1234; shl r0, r0, 16; stmd 0, r0, 2` → smd == [0x1234, 0, …]? No: value = 0x12340000, nunits 2 writes slots 0,1 MSB-first → smd[0]=0x1234, smd[1]=0x0000. Assert exactly.
  - Zero register: `movi rz, 7; advr rz; halt accept` → cursor 0.
  - Trace snapshot correctness: after `sethdr 3` at cursor 14, step's hdr_present[3]==1 and hdr_offset[3]==14.

- [ ] **Step 2: Implement.** Decoder: per-opcode field extraction with explicit reserved-bit masks (from `spec/parser-model/decode.sail`); any nonzero reserved bit or reg code > 4 → ILLEGAL. Layout table (word[31:26] = opcode):

| op | fields | reserved mask |
|---|---|---|
| 0x01 EXT | rd[25:23] boff[22:12] szm1[11:6] | 0x3F |
| 0x02 ADVI | imm[15:0] | 0x03FF0000 |
| 0x03 ADVR | rs[25:23] | 0x007FFFFF |
| 0x04 MOVI | rd[25:23] imm[15:0] | 0x007F0000 |
| 0x05 SHL | rd[25:23] rs[22:20] sh[19:14] | 0x3FFF |
| 0x06/07 BEQ/BNE | rs[25:23] rt[22:20] tgt[15:0] | 0x000F0000 |
| 0x08 JMP | tgt[15:0] | 0x03FF0000 |
| 0x09 SETHDR | h[3:0] | 0x03FFFFF0 |
| 0x0A STMD | rs[25:23] nm1[22:21] slot[20:17] | 0x0001FFFF |
| 0x0B HALT | drop[0] | 0x03FFFFFE |

Machine: mirrors `exec.sail step()` — order: budget check (steps ≥ 256 → err 2) → pc range (pc ≥ 1024 → err 4) → fetch (`words[pc]` if pc < len(words) else 0) → steps += 1, pc += 1 → decode → execute. Execute clauses mirror `insts.sail` exactly:
  - EXT: pos = cursor*8 + boff; pos + sz > hdr_limit*8 → err 1; else rd = bits (reuse interp.py's byte-slice arithmetic: first/last byte, big-endian chunk, shift, mask). hdr_limit = min(len(packet), 256).
  - ADVI/ADVR: cursor + amt > hdr_limit → err 1 (ADVR: amt = rs & 0xFFFF).
  - MOVI: rd = imm. SHL: rd = (rs << sh) & MASK64. BEQ/BNE/JMP: pc = tgt on take (absolute).
  - SETHDR: present[h]=1, offset[h]=cursor. STMD: n=nm1+1; slot+n > 8 → err 5; else MSB-first 16-bit chunks.
  - HALT: verdict 0/1, halt. ILLEGAL: err 3.
Every executed step appends one IssStep snapshot (including error-halting steps — snapshot state as-at-halt). rz writes discarded, reads 0. On any halt: payload_offset = cursor.

- [ ] **Step 3: Unit tests pass.**

- [ ] **Step 4: Differential vs golden model** — `spec/python/tests/test_iss_differential.py`:

```python
CORPUS = corpus_packets()  # nanuk_spec.testkit
PROG = (REPO / "examples/l2l3l4/parse.asm").read_text()

def test_iss_matches_emulator_on_corpus():
    binary = asm.assemble(PROG)
    for name, pkt in CORPUS:
        want = run_program(binary, pkt)
        got = run_iss(binary, pkt)
        assert (got.verdict, got.error, got.payload_offset, got.steps,
                got.hdr_present, got.hdr_offset, got.smd) == (
                want.verdict, want.error, want.payload_offset, want.steps,
                want.hdr_present, want.hdr_offset, want.smd), name

def test_iss_matches_emulator_random():
    rng = random.Random(0x4E414E)  # deterministic
    binary = asm.assemble(PROG)
    for _ in range(60):
        pkt = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 300)))
        ...same 7-field compare...
```

Also a random-WORDS leg (fuzz the decoder): 40 programs of 8 random 32-bit words each + random 64-byte packets, compare all 7 fields (this exercises illegal/reserved paths against the golden decode). Match exact corpus/demo file paths to what `spec/python/tests/test_demo.py` uses.

- [ ] **Step 5: All green (devcontainer for the emulator legs). Commit** — `v2 (3): parser ISS — the fourth implementation, differentially green vs nanuk-emu`

---

### Task 4: MAP ISS (`nanuk_isa.iss_map`)

**Files:**
- Create: `spec/isa/nanuk_isa/iss_map.py`
- Test: `spec/isa/tests/test_iss_map.py`, `spec/python/tests/test_iss_map_differential.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class MapIssStep:
    pc: int; line: int | None
    regs: tuple[int, int, int, int]           # AFTER execute
    writes: tuple[tuple[int, bytes], ...]      # window writes this step: (window_addr, bytes)
    lookup: tuple[int, int, bool, int] | None  # (table_id, key, hit, action)

@dataclass(frozen=True)
class MapIssResult:  # first 6 fields mirror map_harness.MapResult
    verdict: int; error: int; egress: int; delta: int; steps: int
    frame: bytes | None
    trace: list[MapIssStep]

def run_map_iss(prog: bytes, packet: bytes, pp, tables, ingress: int,
                *, line_map: list[int] | None = None) -> MapIssResult
```

`pp` = ParseResult-shaped (`hdr_present/hdr_offset/smd` lists); `tables` = Table-shaped list (`key_width/action_width/entries` — duck-typed like `interp_map`). Constants mirrored: `HEADROOM_BYTES=32, BUF_BYTES=256, WIN_BYTES=288, N_PORTS=4, N_TABLES=4, IMEM_WORDS=1024, STEP_BUDGET=256`; errors 0..6 as in `map_harness`.

- [ ] **Step 1: Failing unit tests.** Required cases (assemble via `nanuk_isa.map_asm`; pp with hdr 0 present at 0, hdr 1 at 14; 64-byte packet):
  - LD/ST round-trip: `ld r0, 0, 0, 6; st r0, 0, 6, 6; send …` — bytes copied; writes event == (32+6, 6 bytes).
  - h_frame base: `ld r0, h_frame, 0, 1` reads packet[0] (window addr 32).
  - Headroom ST: `st r0, h_frame, -4, 4` writes window addr 28; SEND delta 4 → frame = window[28:32+plen].
  - hdr_absent: LD on hdr 5 (absent) → err 5. Window violation: `ld r0, 0, 500, 1` on a 64-byte packet → err 1 (500 fits the signed 10-bit field, exceeds win_limit); negative reach past headroom: `st r0, h_frame, -33, 1` → err 1.
  - LDMD fields: smd passthrough (field 3), ingress (8), flood (9: ingress 1, 4 ports → 0b1101), hdr_present bitmap (10), defined-zero (12 → 0).
  - ADDI sign-extension: `movi r0, 0; addi r0, r0, -1` → r0 == 2**64 - 1 (encode two's complement, machine sign-extends). Wraparound: addi 1 to all-ones → 0.
  - LOOKUP hit/miss: table {key_width: 48, entries {0xAABBCCDDEE01: 0x4}}; hit → rd = 4, falls through; miss key → rd = 0, pc = miss target; lookup event recorded (tid, key, hit, action). Key masking: entry key stored wider than key_width matches after masking. Table id 3 with no config → miss. rd == rs legal.
  - CSUMUPD: build a 20-byte IPv4 header with known checksum (use the golden bytes from an existing ttl test), zero its checksum, run `csumupd 1, 0`, assert the two written bytes == golden; writes event == (base+10, 2 bytes). IHL < 5 → err 1.
  - SEND range: delta 33 → err 6; delta -plen → err 6; delta -(plen-1) OK. Egress masking: `movi r0, 0xFF; send r0, 0` → egress 0xF.
  - DROP → verdict 1, frame None. Budget: `start: jmp start` → err 2, steps 256.
  - Reserved bits / bad reg → err 3 (e.g. `encode_movi │ (1<<22)`; LOOKUP rs code 5: `(0x09<<26)|(0<<23)|(0<<19)|(5<<16)`).

- [ ] **Step 2: Implement.** Decoder layout (from `spec/map-model/decode.sail`); off/delta fields are 10-bit two's complement (sign-extend after extract):

| op | fields | reserved mask |
|---|---|---|
| 0x01/0x02 LD/ST | r[25:23] h[22:19] off[18:9] nm1[8:6] | 0x3F |
| 0x03 LDMD | rd[25:23] f[22:19] | 0x0007FFFF |
| 0x04 MOVI | rd[25:23] imm[15:0] | 0x007F0000 |
| 0x05 ADDI | rd[25:23] rs[22:20] imm[15:0] | 0x000F0000 |
| 0x06/0x07 BEQ/BNE | rs[25:23] rt[22:20] tgt[15:0] | 0x000F0000 |
| 0x08 JMP | tgt[15:0] | 0x03FF0000 |
| 0x09 LOOKUP | rd[25:23] t[22:19] rs[18:16] tgt[15:0] | 0 |
| 0x0A CSUMUPD | h[25:22] off[21:12] | 0x0FFF |
| 0x0B SEND | rs[25:23] delta[22:13] | 0x1FFF |
| 0x0C DROP | — | 0x03FFFFFF |

Machine mirrors `spec/map-model/{state,exec,insts}.sail`; reuse `interp_map`'s arithmetic shapes (window bytearray, plen_min, win_limit, eff_addr semantics — but Sail's exact branch order: hdr_base < 0 → err 5, else addr < 0 or addr+n > win_limit → err 1). ADDI: `(reg + sext16(imm)) & MASK64`. LOOKUP masks stored keys AND search key to key_width (clamped ≤ 64); iterates entries in insertion order, first match wins; ids ≥ min(len(tables), 4) or key_width 0 → miss. CSUMUPD mirrors interp_map's loop byte-for-byte (including the i==10 pair-zeroing). SEND: `d > 32 or d <= -plen_min` → err 6; frame = window[32-d : 32+plen_min] + packet[256:]. Every step appends a MapIssStep (writes = the ST/CSUMUPD byte-writes of that step, in order).

- [ ] **Step 3: Unit tests pass.**

- [ ] **Step 4: Differential vs nanuk-map-emu** — `spec/python/tests/test_iss_map_differential.py`: for each MAP demo (`map_l2fwd`, `map_ttl`, tunnel push/pop asm sources — same paths and table factories `testkit` provides that the existing `test_map_demo_*.py` use), run corpus + tunnel packets through `run_map` (golden, composed with the l2l3l4 parser binary for pp context) and `run_map_iss`, compare all 6 fields including `frame`. Plus a seeded random-frames leg and a random-words decoder-fuzz leg (compose pp from a fixed accepted parse).

- [ ] **Step 5: All green (devcontainer). Commit** — `v2 (4): MAP ISS — both engines simulate at the encoding level`

---

### Task 5: Interp trace hooks

**Files:**
- Modify: `compiler/nanuk_ir/interp.py`, `compiler/nanuk_ir/interp_map.py`
- Test: `compiler/tests/test_interp_trace.py`

**Interfaces:**
- Produces: `interp(program, packet, *, check=True, trace: list | None = None)` and `interp_map(..., trace: list | None = None)`. When `trace` is a list, the interp appends one record per executed IR event:

```python
@dataclass(frozen=True)
class TraceEvent:          # in interp.py; interp_map reuses it
    state: str
    kind: str              # "op" | "term" | "term_case" | "term_default"
    index: int             # op index for "op"; case index for "term_case"; else 0
    steps_after: int       # cumulative machine steps after this event
    values: dict[str, int] # value_id -> value written by this event (usually 0-1 entries; str keys? no: int keys)
    # parser arch snapshot AFTER the event:
    cursor: int | None
    hdr_present: tuple | None; hdr_offset: tuple | None; smd: tuple | None
    # MAP arch effects of the event:
    writes: tuple | None   # ((window_addr, bytes), ...) — same shape as MapIssStep.writes
    lookup: tuple | None   # (table_id, key, hit, action)
```

(Exact shape: one dataclass with optional fields is acceptable; keep `values` keyed by int value_id.) Event emission points: each `_exec_op` (kind "op", index = position in state.ops), `_exec_terminator` halt/goto/send/drop (kind "term"), each dispatch case tried (kind "term_case", index = case position, EVEN when not taken), the inline default (kind "term_default"). Zero-step events (re-anchor mark, dispatch header) emit NO event. Default `trace=None` → zero behavior change (symex chassis untouched).

- [ ] **Step 1: Failing tests** — run l2l3l4's `build_ir()` (import from `lang`? No — compiler tests build IR via `tests/irbuild.py` helpers; use a small synthetic program):
  - steps_after strictly increasing; last event's steps_after == result.steps for normal halts.
  - A dispatch with 3 cases where the 2nd matches emits 2 term_case events (steps_after +2 each).
  - Budget exhaustion mid-op: trace ends at the last completed event; result.steps == 256.
  - `trace=None` (default): results identical to before (compare against a recorded run).
  - MAP: store op event carries `writes`; lookup miss event carries `lookup` with hit=False and ends the state's events (control transfer).

- [ ] **Step 2: Implement** — thread an optional `_Tracer` through `_Machine`; each exec site calls `m.trace_event(...)` after completing. Snapshots are tuples (immutable copies). ~40 lines per interp.

- [ ] **Step 3: Full compiler + lang suites green (no parity drift). Commit** — `v2 (5): interp trace hooks — recorded IR-level execution, off by default`

---

### Task 6: Lowering reg-binding export

**Files:**
- Modify: `compiler/nanuk_ir/lower.py`, `compiler/nanuk_ir/lower_map.py`
- Test: `compiler/tests/test_lower_regmap.py`

**Interfaces:**
- Produces: `to_asm_annotated(program, *, check=True) -> tuple[str, list[dict[str, str]]]` (and `to_map_asm_annotated`): same text as `to_asm`, plus one `{reg: value_name}` dict per emitted instruction line, in emission order (label/blank lines excluded), reflecting bindings AFTER that instruction. `to_asm` keeps its exact output (delegates or shares the helper).

- [ ] **Step 1: Failing tests** — synthetic parser program: after the `ext` line binding "eth.type" to r0, the dict is `{"r0": "eth.type"}`; dispatch movi/beq lines carry the same dict (r3 never appears — scratch is not a binding). MAP: a program that forces reuse (4 sequential consts stored immediately) shows r0 rebound to the newest name after the old value's last use.

- [ ] **Step 2: Implement** — `_StateLowering.emit` appends `dict(sorted((r, self.names[v]) for v, r in self.regs.items()))`-style snapshot to a bindings list; annotated entry points collect them. (MAP: snapshot after `free_dead`, i.e. bindings that are still live.)

- [ ] **Step 3: Green, including asm-text-unchanged assertions vs `to_asm`. Commit** — `v2 (6): annotated lowering — value→register bindings per instruction`

---

### Task 7: ISS-vs-interp parity + alignment invariant (lang)

**Files:**
- Test: `lang/tests/test_iss_parity.py`
- Modify: `lang/pyproject.toml` (dev group += `nanuk-isa` with uv source `../spec/isa`)

**Interfaces:**
- Consumes: everything from Tasks 2-6. This is the test that proves the flagship diff is honestly green.

- [ ] **Step 1: Write the parity test** (fails only if Tasks 3-6 have bugs — TDD here is the differential itself):

```python
PROGRAMS = [l2l3l4.make_parser, nanukproto.make_parser]   # parser
MAP_PROGRAMS = [map_l2fwd, map_ttl, tunnel_push, tunnel_pop]  # eDSL modules

def check_parser(program_ir, pkt):
    events = []
    r_interp = interp(program_ir, pkt, trace=events)
    asm_text = to_asm(program_ir)
    binary, lines = asm.assemble_with_lines(asm_text)
    r_iss = run_iss(binary, pkt, line_map=lines)
    assert results_7field_equal(r_interp, r_iss)
    assert r_interp.steps == r_iss.steps == len(r_iss.trace)
    for ev in events:  # alignment invariant: arch state agrees at op boundaries
        step = r_iss.trace[ev.steps_after - 1]
        assert (ev.cursor, ev.hdr_present, ev.hdr_offset, ev.smd) == (
            step.cursor, step.hdr_present, step.hdr_offset, step.smd)
```

MAP twin compares (verdict, error, egress, delta, steps, frame) + per-event `writes`/`lookup` vs the ISS steps in the event's span (concatenated writes across the span equal the event's writes). Drive with the full corpus + tunnel packets + 40 seeded random packets per program.

- [ ] **Step 2: Run; fix whatever diverges** (this is where step-accounting bugs surface — the interp's cost model and the lowering's emission order are the invariant; the ISS and hooks must conform).

- [ ] **Step 3: Full lang suite green. Commit** — `v2 (7): level-diff is honest — ISS/interp step-exact parity over all demo programs`

---

### Task 8: Bridge trace API

**Files:**
- Modify: `web/py/bridge.py`, `web/py/pyproject.toml` (deps += `nanuk-isa`, uv source `../../spec/isa`)
- Test: `web/py/tests/test_trace_bridge.py`

**Interfaces:**
- Consumes: Tasks 2-7 APIs.
- Produces: `compile_source` response unchanged in existing fields; internally caches `(words, line_map, reg_bindings)` per program. `run_packet` response gains `"trace"`:

```
trace = {
  "steps": int,                    # total machine steps
  "records": [ per machine step:
    { "step": int, "pc": int, "asm_line": int|null,
      "regs": [hex, hex, hex, hex],          # hex strings "0x…"
      "reg_names": {"r0": name, ...},         # from annotated lowering, this pc's instruction
      "state": str, "ir_line": int,           # owning IR event, via walk mapping
      "op_label": str,
      "values": {name: hex, ...},             # values the covering IR event wrote
      "cursor": int|null                      # parser only
    } ],
  "divergence": null | {"step": int, "what": str},
  "result_match": true|false
}
```

Parser run: `{"ok": true, "kind": "parser", "result": {...unchanged...}, "trace": {...}}`. MAP composed run: `"trace": {"pp": {...}, "map": {...}|null}` (gated → map null; the existing gated result shape is unchanged). Divergence detection in Python: per interp event, compare arch snapshot vs `iss.trace[steps_after-1]` (parser) / span-concatenated writes + lookup (MAP); afterwards compare final result structs; first mismatch populates `divergence`/`result_match`.

Walk mapping (interp event → provenance op index within its state): `"op" i → i`; for a dispatch terminator: header is provenance index `len(ops)`, `"term_case" j → len(ops)+1+j`, `"term_default" → len(ops)+1+len(cases)`; bare `"term" → len(ops)`. Use it to fetch `ir_line`/`label` from the rendered state's ops and `asm_lines` from `_provenance`'s cursor walk; `reg_names` come from zipping emission-order bindings through the same cursor walk (instruction line ↔ binding index).

- [ ] **Step 1: Failing tests** — compile l2l3l4 source (reuse the fixture `test_bridge.py` uses), run the qinq preset packet:
  - `trace.steps == result.steps`; every record's `asm_line` is an instruction line in `asm_text` (starts with 4 spaces); record 0's state == first state.
  - regs are hex strings; `int(r, 16)` round-trips.
  - `divergence is None`, `result_match is True`.
  - MAP: map_l2fwd source + known-MAC packet → `trace.pp` and `trace.map` both present, map trace's lookup step exists; ARP packet (gated) → `trace.map is None`.
  - Non-halting-error case: budget-exhausting program still returns a trace of 256 records.

- [ ] **Step 2: Implement** (~150 lines). Compile path: parser → `to_asm_annotated`, `asm.assemble_with_lines`; MAP → map twins; stash in module globals next to `_LAST_PROGRAM`. Run path: interp with `trace=[]`, `run_iss`/`run_map_iss`, build records, detect divergence. The composed MAP run also builds the PP trace with the baked l2l3l4 program (assembled once, cached with `_PP_IR`).

- [ ] **Step 3: web/py suite green. Commit** — `v2 (8): bridge speaks traces — aligned two-level records + divergence verdict`

---

### Task 9: Debugger UI (parser programs)

**Files:**
- Modify: `web/src/lib/types.ts` (TraceJson/TraceRecord/DivergenceJson types mirroring Task 8), `web/src/lib/py.ts` (RunResult carries trace), `web/src/lib/stores.ts`, `web/src/lib/panes/CodePane.svelte`, `web/src/lib/panes/highlight.ts`, `web/src/App.svelte`, `web/src/lib/ResultView.svelte` (keep result summary as-is)
- Create: `web/src/lib/DebuggerPanel.svelte`
- Test: `web/src/lib/panes/highlight.test.ts` additions; `svelte-check` clean

**Interfaces:**
- Consumes: Task 8 JSON.
- Produces: `execLine` mechanism in CodePane: new prop `execLine: number | null`; `highlight.ts` gains `setExecLine: StateEffect<number | null>` + a line-decoration StateField (`Decoration.line({class: 'cm-exec-hl'})`); an `$effect` dispatches on prop change and scrolls the line to center (scrubber is the origin; scrolling all panes is correct). Distinct CSS: `--exec-hl` background, both themes.

- [ ] **Step 1: Extend types + highlight.ts with tests** — `lineToExecRegion(doc, line)` (char pos of line start; clamp like `lineRangesToRegions`); vitest cases for clamping and the field's set/clear.

- [ ] **Step 2: DebuggerPanel** — props `{ trace: TraceJson, step: number, onStep: (n: number) => void, phase?: 'pp' | 'map' }` presentation-only component:
  - transport row: ⏮ ◀ ▶ ⏭ buttons, `<input type="range" min=0 max={trace.steps-1}>`, "step N / M", play/pause (200 ms interval), ←/→ keydown when the panel has focus.
  - badge: green "levels agree" when `divergence === null && result_match`; else red "diverged at step N — this is a Nanuk bug" + a jump button (`onStep(divergence.step)`).
  - two state cards from `trace.records[step]`: IR card (state, op_label, values table name→hex); ASM card (pc, regs r0-r3 with reg_names annotations, cursor, "step N+1 / 256 budget"). The asm text itself is not echoed — the highlighted asm pane shows it.
- [ ] **Step 3: App wiring** — `currentStep` $state, reset to 0 on each successful run; derive from the current record: `execIrLine` (record.ir_line), `execAsmLine` (record.asm_line), `execEdslRange` (state's edsl range from provenance) → pass `execLine` to IR/asm panes and a range-based variant to the eDSL pane (reuse the state hover region mechanism with a dedicated effect, not the shared hover store — hovering must not fight the scrubber; two independent decoration fields). DebuggerPanel renders below `.panes` (a new grid row: `main` becomes a column flex with the debugger strip after the panes; strip height ~11rem, `overflow-x: auto`). Run flow: PacketPanel already calls `runtime.run` — lift the RunResult into App state (move the result display data up or add a callback prop `onResult`) so App owns trace + step. Keep ResultView/MapResultView rendering the final result as today.

- [ ] **Step 4: PacketPanel cursor highlight** — when a parser trace is loaded, highlight the byte at `records[step].cursor` in the hex view (a `<span class="cursor-byte">`). Prop: `cursorByte: number | null`.

- [ ] **Step 5: Verify** — `npm run check` (svelte-check) + `npm run test` (vitest incl. Task 8-shape fixtures) + `npm run build` all clean. Commit — `v2 (9): step-scrubber debugger — walk a parse at two levels`

---

### Task 10: Composed MAP scrubber

**Files:**
- Modify: `web/src/App.svelte`, `web/src/lib/DebuggerPanel.svelte`, `web/src/lib/PacketPanel.svelte` (cursor highlight only in PP phase)

**Interfaces:** consumes Task 8's `{pp, map}` trace shape.

- [ ] **Step 1: Phase-aware stepping** — for MAP runs the timeline is `pp.steps + map.steps` (map may be null → PP only + "gated" notice in the panel). A tick mark on the slider at the phase boundary (CSS gradient or a positioned marker). Current phase = step < pp.steps ? 'pp' : 'map'; state cards + pane highlighting source from the active phase's records; during the MAP phase the IR/asm panes show the MAP program (they already do — the compiled panes are the MAP program; during the PP phase the panes have nothing to highlight since the baked parser isn't displayed: show the PP phase in the state cards with a "baked l2l3l4 parser" caption and NO pane highlighting; document this in the panel).
- [ ] **Step 2: MAP state card extras** — show `writes` (addr+hex) and `lookup` (table, key, hit→action) of the current record when present, and delta/egress on the final record.
- [ ] **Step 3: svelte-check + vitest + build clean; manual smoke via `npm run dev`. Commit** — `v2 (10): scrubber crosses the pipeline — PP phase, boundary tick, MAP phase`

---

### Task 11: Ship it — wheels, CI, Pyodide integration test

**Files:**
- Modify: `web/scripts/build_wheels.sh` (add `(cd "$REPO/spec/isa" && uv build --wheel --out-dir "$OUT" --quiet)`), `web/tests/pyodide.test.ts`, `.github/workflows/pages.yml` (only if wheel steps are hardcoded there — verify), `ruff.toml` (if per-package config needs the new path — verify)

- [ ] **Step 1: Wheels** — build script adds nanuk-isa; manifest stays `ls`-ordered (nanuk_ir, nanuk_isa, nanuk_lang — dependency-safe since nanuk-isa is dep-free and nanuk-lang needs only nanuk-ir).
- [ ] **Step 2: pyodide.test.ts** — assert the run response now carries `trace.steps > 0` and `divergence === null` for the l2l3l4 + qinq case, and a MAP composed case (`map_l2fwd` + known-MAC packet) has `trace.map.records.length === trace.map.steps`.
- [ ] **Step 3: Full local gate** (devcontainer): everything CI runs (Task 1 Step 3 list) + `web`: `npm run check && npm run test && npm run build` + `NANUK_SKIP_PYODIDE` unset Node test. Push, watch CI + pages deploy green.
- [ ] **Step 4: Commit/push** — `v2 (11): third wheel + trace assertions through real Pyodide`

---

### Task 12: Playwright drive (pre-ship verification)

**Files:** scratchpad only (per the v1 lesson — drive the real page).

- [ ] **Step 1: Script** — headless chromium against `npm run preview` (or the deployed site after push): load `/play/`, wait ready, run qinq preset; assert debugger strip visible IN viewport (boundingBox vs viewport, the v1 lesson), step forward 5× and assert the asm exec-highlight line number changes and regs card updates; drag slider to end == final step; badge is green; switch to `map_l2fwd`, run known-MAC packet, cross the phase boundary; deep-link `/play/?program=nanukproto&preset=nk_tunnel` still compiles+runs; **zero console errors** throughout.
- [ ] **Step 2: Fix what it finds; re-run until clean.** Then run it once against the deployed GitHub Pages URL after Task 13's push.

---

### Task 13: Docs + notes + memory

- [ ] **Step 1:** README + CONTRIBUTING: nanuk-isa package appears in the layout/test-matrix tables; playground feature list gains the debugger. `web/site/index.html` landing: one line about stepping through execution at two levels.
- [ ] **Step 2:** Lab notes `guide/notes/2026-07-11-playground-v2-lab-notes.md`: what was built, divergences the parity tests caught en route, UI gotchas.
- [ ] **Step 3:** Update auto-memory (nanuk-project.md): v2 shipped, nanuk-isa extraction, ISS = fourth implementation doctrine note.
- [ ] **Step 4: Commit/push; final CI + pages green check.**
