# Stage 1: Sail Spec + Golden Model + Assembler + pcap Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parser ISA v0 encoded in Sail with a generated C emulator (golden model), a Python assembler, and a scapy/pcap harness, proving the demo program parses Ethernet/VLAN(QinQ)/IPv4/UDP — all runnable in a devcontainer and CI.

**Architecture:** Sail model (`spec/model/`) is the single source of truth for semantics and encodings; a thin C-facing API of machine-word-sized functions makes the generated code drivable from a small C `main` without touching Sail runtime internals. Python (`spec/python/`) mirrors encodings for the assembler (drift guarded by an assemble→emulate differential test) and wraps the emulator binary for pcap-driven tests.

**Tech Stack:** Sail (opam, C backend) · CMake + ctest · C (thin CLI shim) · Python via uv: pytest, scapy · devcontainer (Ubuntu 24.04) · GitHub Actions (devcontainers/ci).

**Specs:** [Project design](../specs/2026-07-11-nanuk-project-design.md) · [Parser ISA v0 design](../specs/2026-07-11-parser-isa-v0-design.md)

## Global Constraints

- Total semantics: every abnormal path → defined error halt (verdict=2 + error code); no undefined behavior.
- All-zeros instruction word is **illegal**, not NOP; unlisted encodings and reserved-nonzero bits are illegal.
- Parameters (defaults): BUF_BYTES=256, NREGS=4 (+rz), IMEM_WORDS=1024, NHDR=16, SMD_SLOTS=8 (×16 bits), STEP_BUDGET=256.
- Licenses: Apache-2.0. Python ≥3.12. Commit per task, imperative subject lines.
- Sail owns encodings; Python `encoding.py` is a mirror, cross-checked by differential test (Task 8) — never let them drift silently.

## Frozen interface decisions (finalizing the ISA doc's "open for stage 1" items)

### Instruction encodings — 32-bit words, big-endian in files, opcode at [31:26]

Register field (3 bits): `r0=000, r1=001, r2=010, r3=011, rz=100`; `101`–`111` illegal.

| Opcode | Instr | Field layout (bits below [25:0]) |
|---|---|---|
| 0x01 | `EXT rd, boff, bsize` | [25:23] rd · [22:12] boff (11b, bits) · [11:6] bsize−1 (6b → 1..64) · [5:0]=0 |
| 0x02 | `ADVI imm` | [25:16]=0 · [15:0] imm16 (bytes) |
| 0x03 | `ADVR rs` | [25:23] rs · [22:0]=0 |
| 0x04 | `MOVI rd, imm` | [25:23] rd · [22:16]=0 · [15:0] imm16 (zero-extended) |
| 0x05 | `SHL rd, rs, sh` | [25:23] rd · [22:20] rs · [19:14] shamt (6b) · [13:0]=0 |
| 0x06 | `BEQ rs, rt, tgt` | [25:23] rs · [22:20] rt · [19:16]=0 · [15:0] absolute word target |
| 0x07 | `BNE rs, rt, tgt` | same as BEQ |
| 0x08 | `JMP tgt` | [25:16]=0 · [15:0] absolute word target |
| 0x09 | `SETHDR h` | [25:4]=0 · [3:0] hdr id |
| 0x0A | `STMD slot, rs, n` | [25:23] rs · [22:21] n−1 (2b → 1..4 slots) · [20:17] slot (4b) · [16:0]=0 |
| 0x0B | `HALT accept\|drop` | [25:1]=0 · [0] drop flag |

Everything else — opcode 0x00 (incl. the all-zeros word), opcodes >0x0B, reserved-nonzero bits, register codes 5–7 — decodes as **illegal**.

### Semantics details

- `hdr_limit = min(plen, BUF_BYTES)`; `plen` = packet length in bytes, set by the harness.
- `EXT`: bit position `p = cursor*8 + boff` (bit 0 = MSB of the byte at cursor; network order). Requires `p + bsize ≤ hdr_limit*8` else error 1. Value accumulated MSB-first, zero-extended into 64-bit rd (`rz` discards).
- `ADVI/ADVR`: `cursor' = cursor + amount` (ADVR uses rs[15:0], defined); requires `cursor' ≤ hdr_limit` else error 1.
- `SHL`: 64-bit shift, truncating.
- `BEQ/BNE/JMP`: absolute 16-bit word targets (assembler resolves labels); all other instructions `pc' = pc+1`.
- `SETHDR h`: `hdr_present[h] = true; hdr_offset[h] = cursor`.
- `STMD slot, rs, n`: writes low `n×16` bits of rs into SMD slots `slot..slot+n−1`, MSB-first (e.g. 48-bit DMAC in r0 → `STMD 0, r0, 3` puts bits[47:32]→slot0, [31:16]→slot1, [15:0]→slot2). Requires `slot+n ≤ 8` else error 5.
- `HALT`: verdict 0 (accept) or 1 (drop), `payload_offset = cursor`, machine halts.
- Step budget: counted per executed instruction; exhausted → error 2. Fetch with `pc ≥ IMEM_WORDS` → error 4. Illegal decode → error 3.
- Error halt: `verdict=2`, `error=code`, halt; outputs still delivered (partial hdr/SMD state).
- **Error codes:** 0 none · 1 header violation · 2 step budget · 3 illegal instruction · 4 pc out of range · 5 SMD range.

### Output contract (emulator JSON, one line on stdout)

```json
{"verdict": 0, "error": 0, "payload_offset": 42, "steps": 25,
 "hdr_present": [1,0,...16 ints...], "hdr_offset": [0,0,...16...],
 "smd": [43690,...8 ints...]}
```

### C-facing Sail API (`spec/model/api.sail`) — all machine-word types (map to `uint64_t` in C)

```sail
val emu_reset : unit -> unit                       // init all state
val emu_poke_imem : (bits(16), bits(32)) -> unit   // load program word
val emu_poke_pkt : (bits(16), bits(8)) -> unit     // load packet byte
val emu_set_plen : bits(16) -> unit
val emu_run : unit -> unit                          // run until halt
val emu_get_verdict : unit -> bits(8)
val emu_get_error : unit -> bits(8)
val emu_get_cursor : unit -> bits(16)               // = payload_offset
val emu_get_steps : unit -> bits(32)
val emu_get_hdr_present : bits(8) -> bits(8)
val emu_get_hdr_offset : bits(8) -> bits(16)
val emu_get_smd : bits(8) -> bits(16)
```

### Assembly syntax

`;` comments · `label:` · `.equ NAME VALUE` · registers `r0–r3, rz` · immediates decimal or `0x…` · `.equ` names usable as any immediate · mnemonics case-insensitive · `halt accept` / `halt drop` · branch/jump targets are labels. Assembler output: raw binary, big-endian 32-bit words, loaded at word 0, entry `pc=0`.

### File map

```
.devcontainer/{Dockerfile,devcontainer.json}    dev.sh    CMakeLists.txt
spec/model/{main,prelude,params,types,decode,state,insts,exec,api}.sail
spec/emulator/main.c
spec/test/CMakeLists.txt + {test_state,test_decode,test_exec_linear,test_exec_control}.sail
spec/python/{pyproject.toml, nanuk_spec/{__init__,encoding,asm,harness}.py,
             tests/{test_encoding,test_asm,test_harness,test_demo}.py}
examples/l2l3l4/parse.asm
.github/workflows/{devcontainer.yml,ci.yml}
```

---

### Task 1: Devcontainer + Sail→C→exe walking skeleton

**Files:** Create `.devcontainer/Dockerfile`, `.devcontainer/devcontainer.json`, `dev.sh`, `CMakeLists.txt`, `spec/test/CMakeLists.txt`, `spec/model/main.sail` (+ minimal `prelude.sail`), `spec/test/test_smoke.sail`.
**Produces:** `./dev.sh <cmd>` runs in container; `add_sail_test(NAME SAIL_FILE)` CMake helper (copied from sail-xisa pattern: sail -c → C → link `$SAIL_DIR/lib/{sail,rts,elf,sail_failure}.c`, gmp+z); `cmake -B build && cmake --build build && ctest --test-dir build` green with a trivial assert test. **Also pins:** the Sail version and the exact flag for building without Sail's generated main (candidates: `--c-no-main` / `-c_no_main`) — record both in this task's commit message.

- [ ] Dockerfile per sail-xisa pattern (Ubuntu 24.04, opam→OCaml 5.1.0→sail, uv, clang; SAIL_DIR env) — already drafted; build image
- [ ] `dev.sh` (devcontainer exec wrapper, verbatim sail-xisa pattern)
- [ ] Top CMakeLists: find sail, SAIL_DIR check, `check` target (`sail --just-check spec/model/main.sail`), enable_testing, add_subdirectory(spec/test)
- [ ] `test_smoke.sail` with a `main` asserting trivial truth; register via `add_sail_test`
- [ ] Run: `./dev.sh cmake -B build && ./dev.sh cmake --build build && ./dev.sh ctest --test-dir build` → PASS
- [ ] Commit

### Task 2: CI workflows

**Files:** Create `.github/workflows/devcontainer.yml` (build+push image to GHCR on .devcontainer changes), `.github/workflows/ci.yml` (devcontainers/ci: cmake configure/check/build + ctest + `uv run pytest` once spec/python exists — pytest step added in Task 8).

- [ ] Port sail-xisa's two workflows, adjusting repo paths; `push: never`, `cacheFrom: ghcr.io/<repo>/dev`
- [ ] Validate YAML (`python3 -c "import yaml,sys; yaml.safe_load(open(...))"`)
- [ ] Commit (cloud verification deferred until repo has a GitHub remote)

### Task 3: Machine state + C-facing API

**Files:** Create `spec/model/{params,state,api}.sail`; test `spec/test/test_state.sail`.
**Produces:** all registers/vectors from *Semantics details* above; `emu_reset` zeroing everything; all `emu_*` pokes/getters; internal helpers `read_pkt_bits(pos:int, n:int) -> bits(64)` (MSB-first accumulate) and `hdr_limit() -> int`.

- [ ] Write params.sail (constants) + state.sail (registers + reset + helpers) + api.sail
- [ ] test_state.sail: reset zeroes state; poke_pkt/poke_imem round-trip via getters; read_pkt_bits crosses byte boundaries correctly (e.g. bits 4..12 of 0xAB,0xCD = 0xBC); hdr_limit clamps at BUF_BYTES
- [ ] `./dev.sh cmake --build build && ./dev.sh ctest` → PASS; commit

### Task 4: Instruction types + decode

**Files:** Create `spec/model/{types,decode}.sail`; test `spec/test/test_decode.sail`.
**Produces:** union `instr` with 11 constructors (`EXT(regidx, bits(11), bits(6))`, …) + `ILLEGAL()`; `encdec : instr <-> bits(32)` scattered mapping per the encoding table; decode fallthrough → ILLEGAL.

- [ ] types.sail (regidx enum incl. RZ, instr union) + decode.sail (encdec mappings, sail-xisa `@`-concatenation idiom)
- [ ] test_decode.sail: encode→decode round-trip for every instruction; `0x00000000` → ILLEGAL; reserved-nonzero-bits word → ILLEGAL; register code 0b101 → ILLEGAL
- [ ] Build + ctest → PASS; commit

### Task 5: Execute — linear instructions

**Files:** Create `spec/model/insts.sail` (execute scattered function: EXT, ADVI, ADVR, MOVI, SHL + error helper `raise_err(code)`); test `spec/test/test_exec_linear.sail`.

- [ ] Execute clauses per *Semantics details*; rz reads 0/discards writes
- [ ] Tests: EXT within/crossing bytes, sub-byte field (4-bit IHL from 0x45 → 5), EXT past hdr_limit → error 1 + verdict 2; ADVI ok/past-limit; ADVR uses rs[15:0]; MOVI zero-extends; SHL truncates at 64
- [ ] Build + ctest → PASS; commit

### Task 6: Execute — control flow, outputs, run loop

**Files:** Extend `spec/model/insts.sail` (BEQ/BNE/JMP/SETHDR/STMD/HALT); create `spec/model/exec.sail` (`step()`: budget check → fetch (pc range) → decode (illegal) → execute; `emu_run` loop); test `spec/test/test_exec_control.sail`.

- [ ] Implement per *Semantics details* (branches absolute; STMD MSB-first multi-slot + range error 5; HALT verdicts)
- [ ] Tests: taken/untaken branches; backward-branch loop terminated by step budget → error 2; SETHDR records cursor; STMD 3-unit DMAC placement; STMD slot 7 n=2 → error 5; HALT accept/drop; full tiny program (MOVI+BEQ+HALT) via emu_poke_imem + emu_run
- [ ] Build + ctest → PASS; commit

### Task 7: Emulator CLI

**Files:** Create `spec/emulator/main.c`; extend top `CMakeLists.txt` with `nanuk-emu` target (sail -c with no-main flag on `spec/model/main.sail` → link with main.c + runtime); smoke test registered in ctest (bash: assemble-by-hand two words via printf, run, grep JSON).

- [ ] main.c: read prog.bin (BE u32 words → `zemu_poke_imem`), pkt.bin (bytes → `zemu_poke_pkt`), `zemu_set_plen`, `zemu_run`, print JSON per output contract using `zemu_get_*`; exit 0
- [ ] ctest smoke: program = `{MOVI r0,7; HALT accept}` = words `0x10000007` (op 0x04<<26 | r0<<23 | 7), `0x2C000000` (op 0x0B<<26, drop=0), 0-byte packet; expect `"verdict": 0`
- [ ] Build + ctest → PASS; commit

### Task 8: Python project — encoding mirror + assembler

**Files:** Create `spec/python/pyproject.toml` (uv; deps pytest, scapy), `nanuk_spec/{__init__,encoding,asm}.py`, `tests/{test_encoding,test_asm}.py`. Extend `.github/workflows/ci.yml` runCmd with `cd spec/python && uv sync && uv run pytest`.
**Produces:** `encoding.py`: per-instr `encode_*` functions returning int + opcode/field constants (mirror of the table). `asm.py`: `assemble(text: str) -> bytes` (two-pass, labels, `.equ`, errors with line numbers) + `main()` CLI (`nanuk-asm in.asm -o out.bin`).

- [ ] TDD encoding.py: each instruction encodes to hand-computed golden words (incl. the Task 7 words); field-range validation raises
- [ ] TDD asm.py: label resolution (forward+backward), .equ, comments, case-insensitivity, error on unknown mnemonic/label/range; assembles the ISA-doc demo program without error
- [ ] `uv run pytest` in container → PASS; commit

### Task 9: pcap harness

**Files:** Create `spec/python/nanuk_spec/harness.py`, `tests/test_harness.py`.
**Produces:** `ParseResult` dataclass (verdict/error/payload_offset/steps/hdr_present/hdr_offset/smd + `.accepted` property); `run_program(prog: bytes, packet: bytes, emu: Path = <build/nanuk-emu>) -> ParseResult` (tempfiles + subprocess + JSON parse); `run_pcap(prog, pcap_path) -> list[ParseResult]` (scapy `rdpcap`, raw bytes per packet).

- [ ] TDD with the Task 7 tiny program: run_program returns verdict 0; a scapy-written 2-packet pcap round-trips through run_pcap
- [ ] Differential guard: assemble `{MOVI r0,7; HALT accept}` via asm.py and byte-compare to Task 7's hand-computed words — Python↔Sail encoding drift tripwire
- [ ] `uv run pytest` → PASS; commit

### Task 10: The demo — v0 program + pcap corpus (stage-1 done criterion)

**Files:** Create `examples/l2l3l4/parse.asm` (ISA-doc demo program in final syntax), `spec/python/tests/test_demo.py`. Update `README.md` (tagline + quickstart).

- [ ] Corpus via scapy fixtures: plain IPv4/UDP · single VLAN · QinQ · IPv4 with options (IHL>5) · IPv4/TCP (accept, no UDP hdr) · ARP (unknown EtherType → accept, only eth hdr) · runt frame (< 14 bytes → error 1, verdict 2) · non-v4 version field → drop
- [ ] Assertions per packet: verdict; hdr_present/hdr_offset for h_eth/h_vlan/h_ipv4/h_udp (exact offsets); SMD: DMAC slots 0–2, VLAN TCI slot 3, UDP dport slot 4; payload_offset
- [ ] `uv run pytest` all green + full `ctest` green — **stage 1 demo passes**
- [ ] README quickstart (devcontainer build, dev.sh, ctest, pytest); commit

## Self-review notes

Spec coverage: ISA doc's 11 instructions ✓ (T4–6), totality rules ✓ (T5/T6 error paths), output contract ✓ (T7 JSON = offsets+SMD+verdict), assembler ✓ (T8), pcap rig ✓ (T9–10), devcontainer+CI from day one ✓ (T1–2), parameters ✓ (params.sail). Type consistency: `emu_*` names used identically in T3 API, T7 C shim (z-prefixed), T9 subprocess contract. Encoding table is the single reference for T4 (Sail) and T8 (Python), cross-checked in T9.
