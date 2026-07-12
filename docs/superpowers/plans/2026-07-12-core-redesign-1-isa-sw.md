# Core Redesign Plan 1/3: ISA + Spec + SW Vertical

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the ISA v0 revision from `docs/superpowers/specs/2026-07-12-core-interface-design.md` through the software vertical: Sail models, emulators, IR schema, Python ISS/IR/eDSL, examples, and playground.

**Architecture:** Metadata becomes one shared `md[8×16b]` window read/written by both processors (PP gains LDMD; both share STMD semantics); MAP loses all system semantics (SEND egress bitmap, LDMD ingress/flood/hdr_present, CSUMUPD's IPv4 knowledge) and gains generic CSUM/ANDI/SHLI. Authority chain: Sail first, then mirrors.

**Tech Stack:** Sail (spec), C emulator shims, Python 3.12 (uv), protobuf (buf), pytest.

## Global Constraints

- Sail owns encoding truth; Python mirrors pin golden words in tests (`test_map_encoding.py` ↔ `spec/sail/test/map/test_map_decode.sail`).
- All builds/tests run in the dev container: `./dev.sh bash -lc '<cmd>'`. Sail: `cmake -S spec/sail -B spec/sail/build && cmake --build spec/sail/build && ctest --test-dir spec/sail/build --output-on-failure`. SW: `cd sw/python && uv run pytest tests`.
- Commits: imperative sentence with type prefixes per recent `git log`; the IR schema commit message must contain `[ir-breaking]`.
- Per-layer suites must be green at every commit. Cross-layer conformance (ISS-vs-emulator, `NANUK_COSIM=1` golden rig) is red between Task 1 and the end of Task 4 by necessity; it gates Tasks 4, 8, and 9.
- Prose says Nanuk; tokens say nanuk (naming doctrine).
- md slot conventions (system level, used by tests/examples): slot 0 = ingress in / egress bitmap out; slots 4–7 program-pair private; MD_TUN moves to slot 4. Flood table = table 3, kw=16, aw=16, entries `{i → (0xF & ~(1<<i))}` for i in 0..3.

## Locked encodings (Sail + Python must match bit-for-bit)

```
MAP (opcode at [31:26], reserved bits zero, ILLEGAL if nonzero):
  LDMD  0x03  unchanged encoding; NEW semantics: field ≥ 8 -> err_illegal at execute
  CSUM  0x0A  op @ rd(3) @ hdr(4) @ off(10 two's-c) @ rl(3) @ 0(6)     (replaces CSUMUPD)
  SEND  0x0B  op @ 0(3) @ delta(10 two's-c) @ 0(13)                    (register field now must-be-zero)
  STMD  0x0D  op @ rs(3) @ nm1(2) @ slot(4) @ 0(17)                    (same layout as PP STMD)
  ANDI  0x0E  op @ rd(3) @ rs(3) @ 0(4) @ imm16                        (ADDI shape; imm zero-extended)
  SHLI  0x0F  op @ rd(3) @ rs(3) @ sh(6) @ 0(14)                       (same layout as PP SHL)
PP:
  LDMD  0x0C  op @ rd(3) @ field(4) @ 0(19)                            (same layout as MAP LDMD; field ≥ 8 -> err_illegal)
  STMD  0x0A  unchanged encoding; writes the shared md window (state rename only)
```

Semantics notes fixed by the spec:
- `md : vector(8, bits16)` per model, harness-initialized (pass-through default), STMD-written, delivered as output. Replaces PP's `smd` register and MAP's `smd_in`/`ingress`/`egress` registers.
- New MAP CSUM: ones-complement checksum (RFC 1071 fold + complement) of window `[base+off, base+off+len)` where `len = unsigned(r[rl])` clamped semantics: `len = 0` → result `0xFFFF`; range escaping `[0, win_limit)` → `err_window_violation`; absent hdr → `err_hdr_absent`. Result to `rd`; **no window write**.
- ANDI: `rd = rs & zero_extend(imm16)`. SHLI: `rd = rs << sh` (64-bit truncate), matching PP SHL semantics.
- Old `send r0, delta` words (reg bits 0b000) decode as the new SEND — accepted, harmless (delta field position unchanged; egress now comes from md).

---

### Task 1: Sail MAP model — metadata window + instruction changes

**Files:**
- Modify: `spec/sail/model/map/params.sail` (smd_in_slots → md_slots; drop n_ports)
- Modify: `spec/sail/model/map/state.sail` (md register; delete smd_in/ingress/egress)
- Modify: `spec/sail/model/map/types.sail` (instr union)
- Modify: `spec/sail/model/map/decode.sail` (encodings above)
- Modify: `spec/sail/model/map/insts.sail` (execute clauses; delete flood_mask/port_mask_all)
- Modify: `spec/sail/model/map/api.sail` (emu_map_set_md/emu_map_get_md replace set_smd/set_ingress/get_egress)
- Modify: `spec/sail/emulator/map_main.c` (input keyword `md <slot> <value>`; JSON output `"md": [..]` replaces `"egress"`)
- Test: `spec/sail/test/map/test_map_decode.sail`, `test_map_exec_csum.sail`, `test_map_exec_mem.sail`, `test_map_exec_control.sail`, `spec/sail/emulator/map_smoke_test.sh`

**Interfaces:**
- Consumes: locked encodings table above.
- Produces: emulator CLI contract used by Task 3/4: input lines `md <slot> <hex16>`, result JSON `{"verdict": V, "error": E, "md": [m0..m7], "delta": D, "steps": S}`; Sail functions `emu_map_set_md(bits8, bits16)`, `emu_map_get_md(bits8) -> bits16`.

- [ ] **Step 1: Write failing decode tests.** In `spec/sail/test/map/test_map_decode.sail`, following the file's existing golden-word assertion pattern, replace CSUMUPD/SEND assertions and add STMD/ANDI/SHLI/LDMD-high-field cases:

```sail
// CSUM r1, hdr=1, off=0, rl=r2:
// 0b001010 @ 001 @ 0001 @ 0000000000 @ 010 @ 000000 = 0x2888_0080
assert(encode(CSUM(R1, 0x1, 0b0000000000, R2)) == 0x2888_0080);
assert(eq_instr(decode(0x2888_0080), CSUM(R1, 0x1, 0b0000000000, R2)));
// SEND delta=4: 0b001011 @ 000 @ 0000000100 @ 0(13) = 0x2C00_8000
assert(encode(SEND(0b0000000100)) == 0x2C00_8000);
// old register-coded SEND (rs=r1, delta=4 was 0x2C80_8000) no longer matches -> ILLEGAL
assert(eq_instr(decode(0x2C80_8000), ILLEGAL()));
// STMD r1, n=1, slot=4: 0b001101 @ 001 @ 00 @ 0100 @ 0(17) = 0x3488_0000
assert(encode(STMD(R1, 0b00, 0x4)) == 0x3488_0000);
// ANDI r1, r2, 0x00FF: 0b001110 @ 001 @ 010 @ 0000 @ imm = 0x38A0_00FF
assert(encode(ANDI(R1, R2, 0x00FF)) == 0x38A0_00FF);
// SHLI r1, r2, 2: 0b001111 @ 001 @ 010 @ 000010 @ 0(14) = 0x3CA0_8000
assert(encode(SHLI(R1, R2, 0b000010)) == 0x3CA0_8000);
```

(These words were hand-derived from the layouts; the golden words are the tripwire the Python tests pin to. If an existing `eq_instr` helper lacks arms for new constructors, extend it in the same file.)

- [ ] **Step 2: Run Sail tests, verify they fail to build** (unknown constructors): `./dev.sh bash -lc 'cmake --build spec/sail/build && ctest --test-dir spec/sail/build -R map --output-on-failure'` — expected: compile error naming `CSUM`.

- [ ] **Step 3: Update types.sail.** Replace the union entries:

```sail
    CSUM    : (regidx, bits4, bits10, regidx),   // rd, hdr, off, len reg
    SEND    : bits10,                             // head delta only
    STMD    : (regidx, bits2, bits4),             // rs, nunits-1, slot
    ANDI    : (regidx, regidx, bits16),
    SHLI    : (regidx, regidx, bits6),
```

(delete `CSUMUPD`; SEND's old `(regidx, bits10)` shape goes; keep everything else. Update the header comment's field-convention list to match.)

- [ ] **Step 4: Update decode.sail** with the locked encodings:

```sail
// Opcode 0x0A: CSUM rd, hdr(4), off(10), rl
mapping clause encdec = CSUM(rd, h, off, rl)
    <-> 0b001010 @ encdec_reg(rd) @ h @ off @ encdec_reg(rl) @ 0b000000 : bits(6)

// Opcode 0x0B: SEND delta(10)
mapping clause encdec = SEND(delta)
    <-> 0b001011 @ 0b000 : bits(3) @ delta @ 0b0000000000000 : bits(13)

// Opcode 0x0D: STMD rs, nunits-1(2), slot(4)
mapping clause encdec = STMD(rs, nm1, slot)
    <-> 0b001101 @ encdec_reg(rs) @ nm1 @ slot @ 0b00000000000000000 : bits(17)

// Opcode 0x0E: ANDI rd, rs, imm16 (imm zero-extended at execute)
mapping clause encdec = ANDI(rd, rs, imm)
    <-> 0b001110 @ encdec_reg(rd) @ encdec_reg(rs) @ 0b0000 : bits(4) @ imm

// Opcode 0x0F: SHLI rd, rs, shamt(6)
mapping clause encdec = SHLI(rd, rs, sh)
    <-> 0b001111 @ encdec_reg(rd) @ encdec_reg(rs) @ sh @ 0b00000000000000 : bits(14)
```

- [ ] **Step 5: Update params.sail/state.sail.** In params: delete `n_ports`, rename `smd_in_slots` to `md_slots : int = 8`. In state: delete registers `smd_in`, `ingress`, `egress`; add `register md : vector(8, bits16)`; in `reset_map_state()` replace `smd_in = init_vec_bits16_8(); ingress = 0x00;` with `md = init_vec_bits16_8();` and delete `egress = 0x00;`.

- [ ] **Step 6: Update insts.sail execute clauses.** Delete `port_mask_all`, `flood_mask`, and `ld_field`. New/changed clauses:

```sail
// LDMD rd, f: rd = md slot f (zero-extended). Fields >= md_slots are ILLEGAL.
function clause execute(LDMD(rd, f)) = {
    let fi = unsigned(f);
    if fi >= 8 then raise_err(err_illegal)
    else {
        assert(0 <= fi & fi < 8, "md slot index out of bounds");
        write_reg(rd, sail_zero_extend(md[fi], 64))
    }
}

// STMD rs, nm1, slot: write (nm1+1) 16-bit units of rs into md, MSB-first.
// slot + n > md_slots is ILLEGAL (mirrors the PP's SMD-range error shape:
// the PP uses err_smd_range; the MAP has no such code, so illegal).
function clause execute(STMD(rs, nm1, slot)) = {
    let n = unsigned(nm1) + 1;
    let s = unsigned(slot);
    if s + n > 8 then raise_err(err_illegal)
    else {
        let v = read_reg(rs);
        var i : int = 0;
        while i < n do {
            let sh = (n - 1 - i) * 16;
            let chunk = sail_shiftright(v, sh);
            let si = s + i;
            assert(0 <= si & si < 8, "md slot index out of bounds");
            md[si] = chunk[15 .. 0];
            i = i + 1
        }
    }
}

// ANDI rd, rs, imm16: rd = rs & zero-extended immediate.
function clause execute(ANDI(rd, rs, imm)) = {
    write_reg(rd, read_reg(rs) & sail_zero_extend(imm, 64))
}

// SHLI rd, rs, sh: rd = rs << sh (64-bit truncating).
function clause execute(SHLI(rd, rs, sh)) = {
    write_reg(rd, sail_shiftleft(read_reg(rs), unsigned(sh)))
}

// CSUM rd, hdr, off, rl: RFC 1071 ones-complement checksum of
// window[base+off .. base+off+len) into rd. len = low 16 bits of r[rl].
// No protocol knowledge: no IHL, no skipped bytes, no write-back.
function clause execute(CSUM(rd, h, off, rl)) = {
    let base = eff_addr(h, off);
    let len = unsigned(read_reg(rl)[15 .. 0]);
    if hdr_base(unsigned(h)) < 0 then {
        raise_err(err_hdr_absent)
    } else if base < 0 | base + len > win_limit() then {
        raise_err(err_window_violation)
    } else {
        var sum : int = 0;
        var i : int = 0;
        while i < len do {
            let b0 = unsigned(read_win_byte(base + i));
            let b1 = if i + 1 < len then unsigned(read_win_byte(base + i + 1)) else 0;
            sum = sum + b0 * 256 + b1;
            i = i + 2
        };
        var s : bits32 = get_slice_int(32, sum, 0);
        while unsigned(sail_shiftright(s, 16)) != 0 do {
            s = (s & 0x0000FFFF) + sail_shiftright(s, 16)
        };
        write_reg(rd, sail_zero_extend(xor_vec(s[15 .. 0], 0xFFFF), 64))
    }
}

// SEND delta: terminate; the transmitted frame starts delta bytes before
// the original frame start. Egress meaning lives in md, outside the ISA.
function clause execute(SEND(delta)) = {
    let d = signed(delta);
    let pl = if unsigned(plen) < buf_bytes then unsigned(plen) else buf_bytes;
    if d > headroom_bytes | d <= negate(pl) then {
        raise_err(err_send_range)
    } else {
        send_delta = d;
        verdict = verdict_sent;
        halted = true
    }
}
```

Also delete the old `CSUMUPD` clause and fix the `LD`/`ST` clauses' neighbors only if they referenced deleted helpers (they don't). Odd-length CSUM note: the last byte of an odd `len` is high-weighted (`b0 * 256`), the RFC 1071 convention — the loop above already does this since `b1 = 0` past the end.

- [ ] **Step 7: Update api.sail + map_main.c.** Replace `emu_map_set_ingress`/`emu_map_set_smd`/`emu_map_get_egress` with:

```sail
val emu_map_set_md : (bits8, bits16) -> unit
function emu_map_set_md(s, v) = {
    let i = unsigned(s);
    assert(0 <= i & i < 8, "md slot index out of bounds");
    md[i] = v
}

val emu_map_get_md : bits8 -> bits16
function emu_map_get_md(s) = {
    let i = unsigned(s);
    assert(0 <= i & i < 8, "md slot index out of bounds");
    md[i]
}
```

In `map_main.c`: extern decls `zemu_map_set_md(uint64_t, uint64_t)` / `uint64_t zemu_map_get_md(uint64_t)`; input keyword `md <slot> <value>` replaces `ingress`/`smd`; the result JSON replaces `"egress": N` with `"md": [m0, m1, ..., m7]` (loop over 8 `zemu_map_get_md` calls). Update `map_smoke_test.sh` expectations accordingly.

- [ ] **Step 8: Extend exec tests.** In `test_map_exec_mem.sail` (or the file's closest fit): STMD writes md; LDMD reads back what the harness set; LDMD field 9 raises err_illegal; STMD slot+n overflow raises err_illegal; ANDI/SHLI arithmetic. In `test_map_exec_csum.sail`: rewrite for CSUM — program the classic IPv4 recompute (`ld` byte 0 → `andi 0x0F` → `shli 2` → `csum` → `st` at +10) over a known 20-byte header and assert the stored bytes equal the scapy-verified checksum already present in the file's fixtures; add `len = 0 → 0xFFFF` and odd-length cases. In `test_map_exec_control.sail`: SEND records delta and verdict without touching md.

- [ ] **Step 9: Build + run.** `./dev.sh bash -lc 'cmake --build spec/sail/build && ctest --test-dir spec/sail/build -R map --output-on-failure'` — expected: all map tests + map smoke PASS.

- [ ] **Step 10: Commit.** `git commit -m "feat(spec): MAP metadata window + generic CSUM/ANDI/SHLI/STMD — system semantics evicted"`

---

### Task 2: Sail PP model — LDMD + shared md window

**Files:**
- Modify: `spec/sail/model/pp/{params,state,types,decode,insts,api}.sail`
- Modify: `spec/sail/emulator/pp_main.c`, `spec/sail/emulator/pp_smoke_test.sh`
- Test: `spec/sail/test/pp/test_decode.sail`, `test_exec_linear.sail`, `test_state.sail`

**Interfaces:**
- Consumes: locked PP LDMD encoding (0x0C).
- Produces: emulator CLI: input `md <slot> <hex16>` (new), result JSON `"md": [m0..m7]` replacing `"smd": [..]`; `emu_set_md(bits8, bits16)` / `emu_get_md(bits8)`.

- [ ] **Step 1: Failing decode test** in `test_decode.sail`:

```sail
// LDMD r1, field 3: 0b001100 @ 001 @ 0011 @ 0(19)
assert(encode(LDMD(R1, 0x3)) == 0x3098_0000);
assert(eq_instr(decode(0x3098_0000), LDMD(R1, 0x3)));
```

- [ ] **Step 2: Run to verify build failure** (`ctest -R pp`): unknown constructor LDMD.

- [ ] **Step 3: Implement.** types.sail: add `LDMD : (regidx, bits4)` to the union. decode.sail: `mapping clause encdec = LDMD(rd, f) <-> 0b001100 @ encdec_reg(rd) @ f @ 0b0000000000000000000 : bits(19)`. state.sail: rename register `smd` → `md` (all uses; reset unchanged semantics — zeros, then harness fills). insts.sail: STMD clause writes `md` (rename only); add:

```sail
// LDMD rd, f: rd = md slot f. Fields >= md_slots are ILLEGAL.
function clause execute(LDMD(rd, f)) = {
    let fi = unsigned(f);
    if fi >= 8 then raise_err(err_illegal)
    else {
        assert(0 <= fi & fi < 8, "md slot index out of bounds");
        write_reg(rd, sail_zero_extend(md[fi], 64))
    }
}
```

params.sail: rename `smd_slots` (if named so) → `md_slots`. api.sail: rename `emu_get_smd` → `emu_get_md` and add `emu_set_md` (same body shape as MAP's Task 1 Step 7). pp_main.c: add `md` input keyword; rename JSON key `"smd"` → `"md"`; update `pp_smoke_test.sh`.

- [ ] **Step 4: Extend exec tests.** `test_exec_linear.sail`: program does `stmd` then `ldmd` of the same slot and branches on equality (PP round-trip); harness-set md slot readable via `ldmd`; field 8 → err_illegal (PP error code table: reuse existing `err_illegal`).

- [ ] **Step 5: Run** `ctest -R pp` + pp smoke — PASS. Full Sail suite: `ctest --test-dir spec/sail/build --output-on-failure` — PASS.

- [ ] **Step 6: Commit.** `git commit -m "feat(spec): PP reads and writes the shared metadata window (LDMD; smd -> md)"`

---

### Task 3: Python ISA tier — encodings, assemblers, ISS, testkit

**Files:**
- Modify: `sw/python/nanuk/isa/map_encoding.py`, `map_asm.py`, `map_iss.py`
- Modify: `sw/python/nanuk/isa/pp_encoding.py`, `pp_asm.py`, `pp_iss.py`
- Modify: `sw/python/nanuk/testkit/map_harness.py`, `pp_harness.py` (emulator CLI drivers + result types)
- Test: `sw/python/tests/isa/` (existing test modules for encoding/asm/iss, extended in place)

**Interfaces:**
- Consumes: locked encodings; emulator CLI contracts from Tasks 1–2.
- Produces (used by Tasks 5–8):
  - `map_encoding`: `encode_csum(rd: str, hdr: int, off: int, rl: str) -> int`, `encode_send(delta: int) -> int`, `encode_stmd(rs: str, nunits: int, slot: int) -> int`, `encode_andi(rd: str, rs: str, imm: int) -> int`, `encode_shli(rd: str, rs: str, sh: int) -> int`; `encode_csumupd` deleted; opcode constants `OP_CSUM = 0x0A`, `OP_STMD = 0x0D`, `OP_ANDI = 0x0E`, `OP_SHLI = 0x0F`.
  - `pp_encoding`: `encode_ldmd(rd: str, field: int) -> int` with `OP_LDMD = 0x0C`.
  - `map_iss.run_map_iss(prog, packet, pp, tables, md_in, *, line_map=None) -> MatchActionIssResult` — `ingress: int` parameter replaced by `md_in: Sequence[int]` (8 slots); result field `egress: int` replaced by `md: tuple[int, ...]` (8 slots, post-run).
  - `pp_iss`: result gains `md: tuple[int, ...]` (renamed from `smd`), run function gains `md_in` parameter (default all-zero).
  - testkit `MatchActionResult`: field `egress` → `md: tuple[int, ...]`; harness runners take `md_in` instead of `ingress`/`smd`.

- [ ] **Step 1: Failing encoding tests.** Extend the isa encoding test module with golden words identical to Task 1 Step 1's Sail constants:

```python
def test_map_new_encodings_golden():
    # Same golden words as spec/sail/test/map/test_map_decode.sail (Task 1).
    assert encode_csum("r1", 1, 0, "r2") == 0x28880080
    assert encode_send(4) == 0x2C008000
    assert encode_stmd("r1", 1, 4) == 0x34880000
    assert encode_andi("r1", "r2", 0x00FF) == 0x38A000FF
    assert encode_shli("r1", "r2", 2) == 0x3CA08000

def test_pp_ldmd_encoding_golden():
    assert encode_ldmd("r1", 3) == 0x30980000
```

- [ ] **Step 2: Run to fail:** `uv run pytest tests/isa -k golden -x` — AttributeError (functions missing).

- [ ] **Step 3: Implement encodings** (map_encoding.py — delete `encode_csumupd`, reshape `encode_send`):

```python
OP_CSUM = 0x0A
OP_STMD = 0x0D
OP_ANDI = 0x0E
OP_SHLI = 0x0F

def encode_csum(rd: str, hdr: int, off: int, rl: str) -> int:
    return (
        (OP_CSUM << 26)
        | (_reg(rd) << 23)
        | (_check(hdr, 4, "header id") << 19)
        | (_signed(off, 10, "byte offset") << 9)
        | (_reg(rl) << 6)
    )

def encode_send(delta: int) -> int:
    return (OP_SEND << 26) | (_signed(delta, 10, "send delta") << 13)

def encode_stmd(rs: str, nunits: int, slot: int) -> int:
    if not 1 <= nunits <= 4:
        raise ValueError(f"unit count {nunits} out of range 1..4")
    return (
        (OP_STMD << 26)
        | (_reg(rs) << 23)
        | ((nunits - 1) << 21)
        | (_check(slot, 4, "md slot") << 17)
    )

def encode_andi(rd: str, rs: str, imm: int) -> int:
    return (
        (OP_ANDI << 26) | (_reg(rd) << 23) | (_reg(rs) << 20)
        | _check(imm, 16, "ANDI immediate")
    )

def encode_shli(rd: str, rs: str, sh: int) -> int:
    return (
        (OP_SHLI << 26) | (_reg(rd) << 23) | (_reg(rs) << 20)
        | (_check(sh, 6, "shift amount") << 14)
    )
```

pp_encoding.py: `OP_LDMD = 0x0C`; `def encode_ldmd(rd, field): return (OP_LDMD << 26) | (_reg(rd) << 23) | (_check(field, 4, "md slot") << 19)`.

- [ ] **Step 4: Assemblers.** map_asm.py mnemonic table: delete `csumupd`; add `csum rd, hdr, off, rl`, `stmd rs, nunits, slot`, `andi rd, rs, imm`, `shli rd, rs, sh`; `send` becomes single-operand `send delta`. pp_asm.py: add `ldmd rd, field`. Follow each file's existing operand-parsing helpers exactly (they mirror `_encode_*` signatures). Extend asm round-trip tests with one line per new mnemonic.

- [ ] **Step 5: ISS.** map_iss.py `_decode`: replace the 0x0A/0x0B arms and add 0x0D/0x0E/0x0F:

```python
        case 0x0A:  # CSUM rd, hdr(4), off(10 signed), rl
            if w & 0x3F or r1 > 4 or ((w >> 6) & 7) > 4:
                return None
            return ("csum", r1, (w >> 19) & 0xF, _sext10((w >> 9) & 0x3FF), (w >> 6) & 7)
        case 0x0B:  # SEND delta(10 signed)
            if w & 0x1FFF or (w >> 23) & 7:
                return None
            return ("send", _sext10((w >> 13) & 0x3FF))
        case 0x0D:  # STMD rs, nm1(2), slot(4)
            if w & 0x1FFFF or r1 > 4:
                return None
            return ("stmd", r1, ((w >> 21) & 3) + 1, (w >> 17) & 0xF)
        case 0x0E:  # ANDI rd, rs, imm16 (zero-extended)
            if w & 0x000F0000 or r1 > 4 or r2 > 4:
                return None
            return ("andi", r1, r2, w & _MASK16)
        case 0x0F:  # SHLI rd, rs, sh(6)
            if w & 0x3FFF or r1 > 4 or r2 > 4:
                return None
            return ("shli", r1, r2, (w >> 14) & 0x3F)
```

`_Machine.__init__` signature: `(self, words, packet, pp, tables, md_in, line_map=None)`; replace `self.ingress = ingress` with `self.md = [v & _MASK16 for v in md_in] + [0] * (8 - len(md_in))`; delete `self.egress`. Execute arms: `_ld_field` shrinks to `return self.md[f] if f < 8 else None` — inline it:

```python
            case ("ldmd", rd, f):
                if f >= 8:
                    self.raise_err(ERR_ILLEGAL)
                else:
                    self.write_reg(rd, self.md[f])
            case ("stmd", rs, n, slot):
                if slot + n > 8:
                    self.raise_err(ERR_ILLEGAL)
                else:
                    v = self.read_reg(rs)
                    for i in range(n):
                        self.md[slot + i] = (v >> (16 * (n - 1 - i))) & _MASK16
            case ("andi", rd, rs, imm):
                self.write_reg(rd, self.read_reg(rs) & imm)
            case ("shli", rd, rs, sh):
                self.write_reg(rd, self.read_reg(rs) << sh)
            case ("csum", rd, h, off, rl):
                self._csum(rd, h, off, rl)
            case ("send", d):
                if d > HEADROOM_BYTES or d <= -self.plen_min:
                    self.raise_err(ERR_SEND_RANGE)
                    return
                self.delta = d
                self.verdict = VERDICT_SENT
                self.halted = True
```

```python
    def _csum(self, rd: int, h: int, off: int, rl: int) -> None:
        base_hdr = self.hdr_base(h)
        if base_hdr < 0:
            self.raise_err(ERR_HDR_ABSENT)
            return
        base = HEADROOM_BYTES + base_hdr + off
        length = self.read_reg(rl) & _MASK16
        if base < 0 or base + length > self.win_limit:
            self.raise_err(ERR_WINDOW_VIOLATION)
            return
        total = 0
        for i in range(0, length, 2):
            b0 = self.window[base + i]
            b1 = self.window[base + i + 1] if i + 1 < length else 0
            total += (b0 << 8) | b1
        while total > 0xFFFF:
            total = (total & 0xFFFF) + (total >> 16)
        self.write_reg(rd, total ^ 0xFFFF)
```

Delete `_csumupd`, `N_PORTS`, and the old `_ld_field`. `run_map_iss(prog, packet, pp, tables, md_in, *, line_map=None)`; result: `md=tuple(m.md)` replaces `egress=m.egress`. `MatchActionIssResult`: field `egress: int` → `md: tuple[int, ...]` (keep field order verdict/error/md/delta/steps/frame/trace). pp_iss.py: rename `smd` state/result field to `md`, seed from new `md_in` run parameter (default `()` → zeros), add `ldmd` decode arm (op 0x0C, same reserved-bit mask shape as `stmd`'s neighbor) and execute arm mirroring MAP's including the `f >= 8` illegal.

- [ ] **Step 6: Testkit harnesses.** `map_harness.py`: the emulator driver replaces `ingress N` / `smd i v` input lines with `md i v` (8 lines), parses `"md"` array from result JSON; `MatchActionResult.egress: int` → `md: tuple[int, ...]`. `pp_harness.py`: `ParserResult.smd` → `md`; driver adds `md i v` inputs and reads `"md"` key. Grep-check all testkit/test consumers of `.egress` / `.smd` and mechanically rename.

- [ ] **Step 7: ISS-vs-emulator conformance.** Extend the existing conformance test in `tests/isa/` with programs covering each new instruction (stmd/ldmd round-trip, andi+shli+csum IPv4 recompute against a scapy-checksummed header, send delta, ldmd field 9 illegal, stmd overflow illegal) — the same program run through `run_map_iss` and the `nanuk-map-emu` harness must produce identical (verdict, error, md, delta, steps).

- [ ] **Step 8: Run.** `./dev.sh bash -lc 'cd sw/python && uv run pytest tests/isa -q'` and with `NANUK_COSIM=1` — expected: PASS (this is the moment cross-layer conformance goes green again for the ISA tier).

- [ ] **Step 9: Commit.** `git commit -m "feat(isa): Python mirrors of the metadata-window ISA — encodings, assemblers, ISS, testkit"`

---

### Task 4: IR schema revision `[ir-breaking]` + gencode

**Files:**
- Modify: `spec/proto/nanuk/ir/v0/nanuk_ir.proto`
- Regenerate: `sw/python/nanuk/ir/nanuk_ir_pb2.py` (via `uv run python scripts/gen.py` from sw/python)

**Interfaces:**
- Produces (message shapes used by Tasks 5–7):

```proto
message MdLoad {              // renames MapLoadMd; both program kinds
  uint32 value_id = 1;
  uint32 slot = 2;            // 0..7 — md window slot, no special fields
  string debug_name = 3;
}

message MdStore {             // renames EmitSmd; both program kinds
  uint32 value_id = 1;
  uint32 slot = 2;            // occupies ceil(width/16) slots from here
}

message AndImm {
  uint32 value_id = 1;
  uint32 src_value_id = 2;
  uint32 imm = 3;             // 16-bit, zero-extended
}

message Csum {                // replaces CsumUpdate
  uint32 value_id = 1;        // checksum result value
  uint32 hdr_id = 2;
  sint32 byte_offset = 3;
  uint32 len_value_id = 4;    // runtime length value
}

message MapSend {
  reserved 1;                 // was bitmap_value_id
  sint32 delta = 2;
}
```

  - `ParserOp.op` gains `MdLoad load_md = 6;` and `emit_smd` field becomes `MdStore emit_md = 4;` (same tag, renamed field+type).
  - `MatchActionOp.op`: `load_md` retyped `MdLoad`; `csum` retyped `Csum`; gains `MdStore store_md = 8; AndImm and_imm = 9; Shift shift = 10;`.

- [ ] **Step 1: Edit the proto** per the shapes above (delete `MapLoadMd`, `EmitSmd`, `CsumUpdate`; update the MapLoadMd field comment lines; keep `Shift` where it is — it is now shared by both op sets, move its comment accordingly).

- [ ] **Step 2: Verify buf breaking fires** (sanity that the tripwire sees it): `./dev.sh bash -lc 'buf lint spec/proto && buf breaking spec/proto --against ".git#branch=main,subdir=spec/proto" || true'` — expected: lint PASS, breaking reports the removals/renames.

- [ ] **Step 3: Regenerate gencode:** `./dev.sh bash -lc 'cd sw/python && uv run python scripts/gen.py'` — `nanuk_ir_pb2.py` updates.

- [ ] **Step 4: Commit** (message must carry the hatch token):

```bash
git commit -m "feat(ir)!: metadata window ops — MdLoad/MdStore/AndImm/Csum; MapSend drops egress [ir-breaking]"
```

---

### Task 5: IR interpreters, lowerings, validators

**Files:**
- Modify: `sw/python/nanuk/ir/map_interp.py`, `map_lower.py`, `map_validate.py`
- Modify: `sw/python/nanuk/ir/pp_interp.py`, `pp_lower.py`, `pp_validate.py`, `pp_symex.py`
- Test: `sw/python/tests/ir/` (existing modules, extended)

**Interfaces:**
- Consumes: Task 4 message shapes; Task 3 asm mnemonics.
- Produces: `interp_map(program, packet, pp, tables, md_in, *, check=True, trace=None) -> MatchActionInterpResult` with `md: tuple[int, ...]` replacing `egress`; `interp(...)` (parser) gains `md_in` parameter and `md` result field; lowerings emit the new mnemonics.

- [ ] **Step 1: Failing IR tests.** Add to the map IR test module: a program using `load_md(slot=0)` → `lookup` (flood table) → `store_md(slot=0)` → `send(delta=0)`; assert `interp_map(...).md[0] == expected_bitmap` and that `to_map_asm` output contains `ldmd`, `stmd`, and bare `send 0`. Add a csum program: `load` IHL byte → `and_imm 0x0F` → `shift 2` → `csum(len_value)` → `store` at +10; assert interp result frame bytes 10–11 equal the scapy checksum fixture already used by the existing csum tests. Parser side: `load_md` + `emit_md` round-trip through `interp`.

- [ ] **Step 2: Run to fail:** `uv run pytest tests/ir -x` — AttributeError on new proto fields / functions.

- [ ] **Step 3: Implement interpreters.** map_interp.py: state gains `self.md` (from `md_in`, 8 slots, pass-through); `load_md` op reads `md[slot]` (slot > 7 → validation error, not runtime); new ops `store_md` (writes slots MSB-first across `ceil(width/16)` slots — mirror the existing EmitSmd width logic from pp side), `and_imm`, `shift` (64-bit truncating), `csum` (same arithmetic as ISS `_csum`, reading `len` from the value environment, writing the result value — no window write); `send` terminator: delta only, result `md=tuple(self.md)`. pp_interp.py: gains `md_in` param + `md` in result; `load_md` op; `emit_md` writes md (rename of emit_smd handling). pp_symex.py: add `load_md` as a fresh symbolic 16-bit input (slot-indexed), `emit_md` as an output constraint — follow the file's existing EmitSmd handling, renamed.

- [ ] **Step 4: Implement lowerings.** map_lower.py: `load_md` → `ldmd {reg}, {slot}`; `store_md` → `stmd {reg}, {nunits}, {slot}` (nunits from value width, mirroring pp_lower's EmitSmd logic); `and_imm` → `andi`; `shift` → `shli`; `csum` → `csum {rd}, {hdr}, {off}, {rl}`; send terminator → `send {delta}`. pp_lower.py: `load_md` → `ldmd`; `emit_md` keeps the existing stmd emission (rename). map_validate.py/pp_validate.py: slot ranges (0–7; store crossing 8 rejected), `Csum.len_value_id` must be defined, `MapSend` no longer references a value (drop that check).

- [ ] **Step 5: Run:** `uv run pytest tests/ir -q` — PASS. Also `tests/isa` still green.

- [ ] **Step 6: Commit.** `git commit -m "feat(ir): interp/lower/validate for the metadata window, generic csum, and bare send"`

---

### Task 6: eDSL — metadata primitives and send sugar

**Files:**
- Modify: `sw/python/nanuk/lang/match_action.py`, `sw/python/nanuk/lang/parser.py`
- Test: `sw/python/tests/lang/` (existing modules, extended)

**Interfaces:**
- Consumes: Task 4 IR messages.
- Produces (used by examples/playground):
  - `MatchActionStateCompiler.load_md(slot: int) -> MatchActionValue` (0–7 only; `MD_INGRESS`/`MD_FLOOD`/`MD_HDRS` constants deleted)
  - `.store_md(value: MatchActionValue, slot: int) -> None`
  - `.and_imm(value, imm: int) -> MatchActionValue`, `.shift(value, amount: int) -> MatchActionValue`
  - `.csum(hdr: BoundHeader | None = None, *, byte_offset: int = 0, length: MatchActionValue) -> MatchActionValue` (replaces `csum_update`)
  - `.send(*, delta: int = 0, egress: MatchActionValue | None = None) -> None` — egress kwarg is sugar: emits `store_md(egress, 0)` then the bare send terminator.
  - Parser side: `ParserStateCompiler.load_md(slot) -> value` and `emit_md(value, slot)` (rename of the existing emit-SMD primitive, same signature shape).

- [ ] **Step 1: Failing lang tests.** l2fwd-shaped program through the eDSL: `dst = s.load(eth.dst)` → `hit = s.lookup(l2, dst, miss=flood)` → `s.send(egress=hit)`; assert compiled asm contains `stmd` before `send`, and `send 0` has no register operand. Flood state: `ing = s.load_md(0)` → `fl = s.lookup(flood_tbl, ing, miss=...)`. Csum: assert `.csum(...)` returns a value usable by `.store(...)` and the compiled asm sequence is `andi`/`shli`/`csum`/`st` for the TTL program shape.

- [ ] **Step 2: Run to fail**, then **Step 3: implement** the methods per the Produces block (each method follows the file's existing op-emission pattern: `_check_open()`, `_require_value(...)`, allocate `MatchActionValue`, append the `ir.MatchActionOp(...)`). `send`:

```python
    def send(self, *, delta: int = 0, egress: MatchActionValue | None = None) -> None:
        """Terminate with verdict sent. egress= is system-convention sugar:
        it stores the value to md slot 0 (the nanuk_switch egress slot)."""
        self._check_open()
        if egress is not None:
            self.store_md(egress, 0)
        # Then set the state terminator exactly as the current send() does,
        # with the reshaped message: ir.Terminator(send=ir.MapSend(delta=delta))
```

- [ ] **Step 4: Run:** `uv run pytest tests/lang -q` — PASS.

- [ ] **Step 5: Commit.** `git commit -m "feat(lang): md window primitives, generic csum, send egress sugar"`

---

### Task 7: Examples migration + golden rig

**Files:**
- Modify: `examples/map_l2fwd/fwd.asm` (+ its eDSL twin if present in the dir), `examples/map_ttl/fwd.asm`, `examples/nanukproto/tunnel_push.asm`, `tunnel_pop.asm`, `parse_tunnel.asm`, each example's README/tables fixtures
- Modify: `sw/python/tests/golden/` fixtures that install tables or assert on egress/smd
- Test: `sw/python/tests/golden/` (the pcap rig), `NANUK_COSIM=1`

**Interfaces:**
- Consumes: new asm mnemonics; md slot conventions from Global Constraints (slot 0 ingress/egress, slot 4 = MD_TUN, flood table = table 3).
- Produces: migrated example programs used verbatim by plan 2 cosim and plan 3 demo.

- [ ] **Step 1: Migrate map_l2fwd/fwd.asm.** The miss path becomes a flood-table lookup. Shape (adapt labels to the file's existing ones):

```asm
; L2 forward: lookup dst MAC; hit -> egress from table; miss -> flood table.
    ld      r0, 0, 0, 6            ; eth.dst (hdr 0, offset 0, 6 bytes)
    lookup  r1, 0, r0, miss        ; table 0: {mac -> port bitmap}
    stmd    r1, 1, 0               ; md[0] = egress bitmap
    send    0
miss:
    ldmd    r2, 0                  ; md[0] = ingress port id (system convention)
    lookup  r1, 3, r2, dark        ; table 3: {ingress -> flood bitmap}
    stmd    r1, 1, 0
    send    0
dark:
    drop                           ; unconfigured flood table: fail closed
```

- [ ] **Step 2: Migrate map_ttl/fwd.asm.** TTL decrement keeps its LD/ADDI/ST; the checksum block becomes:

```asm
    ld      r2, 1, 0, 1            ; ipv4 byte 0 (version|IHL)
    andi    r2, r2, 0x000F         ; IHL
    shli    r2, r2, 2              ; header length in bytes
    movi    r3, 0
    st      r3, 1, 10, 2           ; zero the checksum field first
    csum    r3, 1, 0, r2           ; ones-complement sum over the header
    st      r3, 1, 10, 2           ; store the new checksum
```

(then the l2fwd forwarding tail as in Step 1). Keep every existing comment convention of the file.

- [ ] **Step 3: Migrate the tunnel pair.** `parse_tunnel.asm`: `stmd` slot 0 → slot 4 (MD_TUN now program-pair private). `tunnel_pop.asm`: `ldmd r0, MD_TUN` → `ldmd r0, 4`; both programs' flood usage (`ldmd rX, MD_FLOOD`) becomes the flood-table lookup sequence from Step 1; `send rX, d` → `stmd rX, 1, 0` + `send d`. Update `examples/nanukproto/README.md` narrative (slot conventions paragraph).

- [ ] **Step 4: Golden rig + fixtures.** Wherever tests install tables for these examples, add the flood-table install: `table_config(3, 16, 16)` + adds `{0: 0xE, 1: 0xD, 2: 0xB, 3: 0x7}`; replace `ingress=N`/`smd=[...]` harness arguments with `md_in=[N, 0, 0, 0, tun, 0, 0, 0]` shapes; assertions on `result.egress` become `result.md[0]`.

- [ ] **Step 5: Run the full SW suite with cosim:** `./dev.sh bash -lc 'cd sw/python && uv sync && NANUK_COSIM=1 uv run pytest tests -q'` — expected: all green (golden rig now exercises the migrated programs against the regenerated emulators).

- [ ] **Step 6: Commit.** `git commit -m "feat(examples): programs speak the metadata window — flood table, generic csum, bare send"`

---

### Task 8: Playground bridge + presets

**Files:**
- Modify: `web/py/bridge.py` (and its copy mechanism target `web/public/bridge.py` via `web/scripts/build_wheels.sh` — edit `web/py/bridge.py`, then re-copy), `web/src/programs/` presets, `web/src/lib/` result types (TS: the field carrying `egress`)
- Test: `web/py/tests/`, `web/tests/` (`cd web && npm test`)

**Interfaces:**
- Consumes: Task 6 eDSL API; Task 5 interp signatures.
- Produces: `run_map_packet(..., md_in: list[int])` bridge signature; result JSON `md: list[int]` replacing `egress` (the UI labels md[0] "egress bitmap" — a switch-convention label, which is correct: the playground presents the nanuk_switch packaging).

- [ ] **Step 1:** Update `web/py/bridge.py`: `interp_map` call sites pass `md_in` (preset ingress becomes `md_in[0]`), result dict `"md": list(result.md)` (keep a derived `"egress": result.md[0]` only if the TS types demand a scalar — prefer updating TS). Update the l2fwd eDSL preset source to the new API (send sugar + flood-table state). Update `web/py/tests/test_presets.py` expectations.

- [ ] **Step 2:** Update TS result type + result panel binding (`egress` → `md[0]`, displayed label unchanged: "egress bitmap").

- [ ] **Step 3: Run:** `./dev.sh bash -lc 'cd sw/python && uv run pytest ../../web/py/tests -q'`; then `cd web && bash scripts/build_wheels.sh && npm test && npm run build` — PASS.

- [ ] **Step 4: Commit.** `git commit -m "feat(web): playground speaks md — bridge, presets, result panel"`

---

### Task 9: Full-vertical verification sweep

**Files:** none new — this is the gate.

- [ ] **Step 1:** `./dev.sh bash -lc 'cmake --build spec/sail/build && ctest --test-dir spec/sail/build --output-on-failure'` — all Sail + smoke PASS.
- [ ] **Step 2:** `./dev.sh bash -lc 'cd sw/python && uv sync && NANUK_COSIM=1 uv run pytest tests ../../web/py/tests -q'` — all PASS.
- [ ] **Step 3:** `./dev.sh bash -lc 'cd sw/python && uv run ruff check ../..'` — clean.
- [ ] **Step 4:** `./dev.sh bash -lc 'buf lint spec/proto'` — clean (breaking-vs-main already acknowledged in Task 4's commit).
- [ ] **Step 5:** Grep sweep for stragglers: `grep -rn 'MD_FLOOD\|MD_INGRESS\|MD_HDRS\|csumupd\|csum_update\|smd_in\|bitmap_value_id\|\.egress' --include='*.py' --include='*.sail' --include='*.proto' sw spec web examples` — expected: no hits outside docs/ and this plan.
- [ ] **Step 6:** Note for plan 2: `hw/amaranth` cosim is now red against the new emulators/ISS — that is plan 2's opening state, not a regression to fix here. Do not run the HW suite as part of this plan's gate.
- [ ] **Step 7: Commit** any straggler fixes: `git commit -m "chore: plan-1 verification sweep stragglers"`
