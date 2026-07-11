# IR Interpreter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An IR-level interpreter (`compiler/nanuk_ir/interp.py`) executing `nanuk.ir.v0` Programs directly, plus differential tests proving `interp(IR)` ≡ `emulate(lower(IR))` field-for-field on the golden model — the lightweight translation-validation rig from the [IR interpreter + playground design](../specs/2026-07-11-ir-interpreter-playground-design.md), Decision 1.

**Architecture:** A small state machine over `ir.Program` (five ops, three terminators) whose semantics mirror ISA totality exactly (same buffer clamp, header-violation rule, step budget, output surface, per `spec/model/exec.sail` + the frozen stage-1 semantics). Step accounting follows the v0 lowering's cost model instruction-for-instruction, so **every** result field — including `steps` and budget exhaustion — is comparable with the emulator's `ParseResult`. Differential tests run at two altitudes: synthetic IR programs in `compiler/tests`, real eDSL programs (l2l3l4, nanukproto) over the demo corpus in `lang/tests`.

**Tech Stack:** Python 3.12, protobuf (existing gencode — the `.proto` is NOT modified), pytest, uv workspaces per package, nanuk-emu golden model via `nanuk_spec.harness` (already a dev dep of both `compiler/` and `lang/`).

## Global Constraints

- ISA v0 frozen parameters (mirror, do not reinterpret): `BUF_BYTES=256`, `NHDR=16`, `SMD_SLOTS=8`, `STEP_BUDGET=256`; verdicts `0=accept, 1=drop, 2=error`; error codes `0=none, 1=header violation, 2=step budget` (codes 3/4 structurally impossible at IR level; 5 statically excluded by `validate()`).
- Step accounting ground truth (`spec/model/exec.sail`): budget checked **before** an instruction runs (error 2 fires on the 257th attempt with `steps` saturated at 256); the counter increments once fetched, so an instruction that error-halts mid-execute **has been counted**.
- Lowering cost model (from `compiler/nanuk_ir/lower.py`, v0): extract/shift/advance/emit_smd = 1 instruction each; mark = 1 if `emit_sethdr` else 0; halt = 1; goto = 1; dispatch = 2 per case tried (MOVI+BEQ), then the default terminator's own cost if no case matched.
- `payload_offset` = cursor at halt (normal or error); partial hdr/SMD state is delivered on error halts.
- No new dependencies anywhere. No changes to `nanuk_ir.proto` or the gencode.
- All emulator-dependent tests gated behind `NANUK_COSIM=1` (existing convention; must import cleanly without the emulator).
- Commands below run inside the devcontainer: prefix with `./dev.sh bash -lc '...'` from the repo root (or run bare inside `./dev.sh bash`).
- Commit messages follow repo style (imperative sentence, no `feat:` prefixes) and end with the trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Interpreter skeleton — result type, run loop, halts, step budget

**Files:**
- Create: `compiler/nanuk_ir/interp.py`
- Test: `compiler/tests/test_interp.py`

**Interfaces:**
- Consumes: `nanuk_ir.nanuk_ir_pb2` messages, `nanuk_ir.validate.validate`.
- Produces (used by Tasks 2–5): `interp(program: ir.Program, packet: bytes, *, check: bool = True) -> InterpResult`; `InterpResult` frozen dataclass with fields `verdict: int, error: int, payload_offset: int, steps: int, hdr_present: list[int], hdr_offset: list[int], smd: list[int]`, property `accepted`, method `hdr(hdr_id) -> int | None`; module constants `BUF_BYTES, NHDR, SMD_SLOTS, STEP_BUDGET, VERDICT_ACCEPT, VERDICT_DROP, VERDICT_ERROR, ERR_NONE, ERR_HDR_VIOLATION, ERR_STEP_BUDGET`; internals `_Machine` (attrs `packet, hdr_limit, cursor, steps, hdr_present, hdr_offset, smd, values`, methods `tick()`, `halt_err(code)`), `_Halted`, `_exec_op(m, op)`, `_exec_terminator(m, term) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `compiler/tests/test_interp.py`:

```python
"""IR interpreter semantics, mirrored from the frozen ISA v0 semantics
(stage-1 plan / spec/model). No emulator needed: these are pure-Python
unit tests; the emulator comparison lives in test_differential.py."""

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.interp import (
    STEP_BUDGET,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
    ERR_NONE,
    ERR_STEP_BUDGET,
    interp,
)


# -- tiny builders -----------------------------------------------------------

def prog(*states: ir.State) -> ir.Program:
    return ir.Program(ir_version=1, states=list(states))


def halt(drop: bool = False) -> ir.Terminator:
    return ir.Terminator(halt=ir.Halt(drop=drop))


def goto(target: str) -> ir.Terminator:
    return ir.Terminator(goto=ir.Goto(target_state=target))


# -- halts, run loop, budget -------------------------------------------------

def test_halt_accept():
    r = interp(prog(ir.State(name="s", terminator=halt(drop=False))), b"\x00")
    assert r.verdict == VERDICT_ACCEPT
    assert r.error == ERR_NONE
    assert r.accepted
    assert r.payload_offset == 0
    assert r.steps == 1  # the HALT itself


def test_halt_drop():
    r = interp(prog(ir.State(name="s", terminator=halt(drop=True))), b"\x00")
    assert r.verdict == VERDICT_DROP
    assert not r.accepted


def test_goto_chains_states_and_costs_one_step_each():
    p = prog(
        ir.State(name="a", terminator=goto("b")),
        ir.State(name="b", terminator=goto("c")),
        ir.State(name="c", terminator=halt()),
    )
    r = interp(p, b"")
    assert r.accepted
    assert r.steps == 3  # jmp, jmp, halt


def test_goto_loop_exhausts_step_budget():
    p = prog(
        ir.State(name="a", terminator=goto("b")),
        ir.State(name="b", terminator=goto("a")),
    )
    r = interp(p, b"\x00" * 8)
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_STEP_BUDGET
    assert r.steps == STEP_BUDGET  # saturated: error on the 257th attempt


def test_empty_packet_is_fine():
    r = interp(prog(ir.State(name="s", terminator=halt())), b"")
    assert r.accepted


def test_start_state_is_states_zero_not_name():
    p = prog(
        ir.State(name="not_start", terminator=halt(drop=True)),
        ir.State(name="start", terminator=halt(drop=False)),
    )
    assert interp(p, b"").verdict == VERDICT_DROP


def test_outputs_are_fresh_per_run():
    p = prog(ir.State(name="s", terminator=halt()))
    a, b = interp(p, b""), interp(p, b"")
    assert a.hdr_present == [0] * 16 and a.smd == [0] * 8
    assert a.hdr_present is not b.hdr_present  # no shared mutable state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./dev.sh bash -lc 'cd compiler && uv run --group dev pytest tests/test_interp.py -q'`
Expected: FAIL — `ModuleNotFoundError: No module named 'nanuk_ir.interp'`

- [ ] **Step 3: Write the implementation**

Create `compiler/nanuk_ir/interp.py`:

```python
"""IR-level interpreter: executes nanuk.ir.v0 Programs directly.

Fills the IR-level execution gap (assembly-level execution already has two
implementations: the Sail golden model and the RTL core; IR-level had
zero). Because these semantics are defined here, independent of lower.py,
running interp(program, packet) against emulate(lower(program), packet)
differentially tests the compiler — a lightweight translation-validation
rig (compiler/tests/test_differential.py, lang/tests/test_interp_parity.py).

Semantics mirror ISA totality (spec/model, frozen in the stage-1 plan):
same buffer clamp, same header-violation rule, same step budget, same
output surface. Step accounting follows the v0 lowering's cost model
instruction-for-instruction, so every InterpResult field — including
`steps` and budget exhaustion — matches the golden model's ParseResult
exactly. If the lowering's cost model ever changes (e.g. a dispatch
accelerator in v0.x), this file must change with it.

Error codes 3 (illegal decode) and 4 (pc range) are structurally
impossible at IR level; 5 (SMD range) is rejected statically by
validate(). Only 0/1/2 can appear in an InterpResult.
"""

from dataclasses import dataclass

from . import nanuk_ir_pb2 as ir
from .validate import validate

# Mirror of spec/model/params.sail (see also nanuk_spec.harness).
BUF_BYTES = 256
NHDR = 16
SMD_SLOTS = 8
STEP_BUDGET = 256

VERDICT_ACCEPT = 0
VERDICT_DROP = 1
VERDICT_ERROR = 2

ERR_NONE = 0
ERR_HDR_VIOLATION = 1
ERR_STEP_BUDGET = 2

_MASK64 = (1 << 64) - 1
_MASK16 = (1 << 16) - 1


@dataclass(frozen=True)
class InterpResult:
    """Field-for-field compatible with nanuk_spec.harness.ParseResult."""

    verdict: int
    error: int
    payload_offset: int
    steps: int
    hdr_present: list[int]
    hdr_offset: list[int]
    smd: list[int]

    @property
    def accepted(self) -> bool:
        return self.verdict == VERDICT_ACCEPT

    def hdr(self, hdr_id: int) -> int | None:
        """Offset of a recorded header, or None if not present."""
        return self.hdr_offset[hdr_id] if self.hdr_present[hdr_id] else None


class _Halted(Exception):
    """Internal control flow: any halt, normal or error."""

    def __init__(self, verdict: int, error: int):
        self.verdict = verdict
        self.error = error


class _Machine:
    def __init__(self, packet: bytes):
        self.packet = packet
        self.hdr_limit = min(len(packet), BUF_BYTES)
        self.cursor = 0
        self.steps = 0
        self.hdr_present = [0] * NHDR
        self.hdr_offset = [0] * NHDR
        self.smd = [0] * SMD_SLOTS
        self.values: dict[int, tuple[int, int]] = {}  # value_id -> (value, width)

    def tick(self) -> None:
        # Mirrors step() in spec/model/exec.sail: budget checked before the
        # instruction runs (error 2 fires on the 257th attempt, steps
        # saturated at 256); counted once fetched, so an instruction that
        # error-halts mid-execute has already been counted.
        if self.steps >= STEP_BUDGET:
            raise _Halted(VERDICT_ERROR, ERR_STEP_BUDGET)
        self.steps += 1

    def halt_err(self, code: int) -> None:
        raise _Halted(VERDICT_ERROR, code)


def interp(program: ir.Program, packet: bytes, *, check: bool = True) -> InterpResult:
    """Execute an IR program over a packet. Total, like the ISA.

    With check=True (default) the program is validated first, like
    lower.to_asm; interpretation itself cannot fail on a valid program.
    """
    if check:
        validate(program)
    machine = _Machine(packet)
    states = {state.name: state for state in program.states}
    state = program.states[0]
    try:
        while True:
            machine.values.clear()  # values do not cross states (validated)
            for op in state.ops:
                _exec_op(machine, op)
            state = states[_exec_terminator(machine, state.terminator)]
    except _Halted as halted:
        return InterpResult(
            verdict=halted.verdict,
            error=halted.error,
            payload_offset=machine.cursor,
            steps=machine.steps,
            hdr_present=machine.hdr_present,
            hdr_offset=machine.hdr_offset,
            smd=machine.smd,
        )


def _exec_op(m: _Machine, op: ir.Op) -> None:
    match op.WhichOneof("op"):
        case _:
            raise NotImplementedError  # Task 2


def _exec_terminator(m: _Machine, term: ir.Terminator) -> str:
    match term.WhichOneof("kind"):
        case "halt":  # HALT
            m.tick()
            raise _Halted(
                VERDICT_DROP if term.halt.drop else VERDICT_ACCEPT, ERR_NONE
            )
        case "goto":  # JMP
            m.tick()
            return term.goto.target_state
        case _:
            raise NotImplementedError  # dispatch: Task 3
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./dev.sh bash -lc 'cd compiler && uv run --group dev pytest tests/test_interp.py -q'`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add compiler/nanuk_ir/interp.py compiler/tests/test_interp.py
git commit -m "Add the IR interpreter skeleton: run loop, halts, step budget

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Linear ops — extract, shift, advance, mark, emit_smd

**Files:**
- Modify: `compiler/nanuk_ir/interp.py` (replace the `_exec_op` stub)
- Test: `compiler/tests/test_interp.py` (append)

**Interfaces:**
- Consumes: Task 1's `_Machine`, `tick()`, `halt_err()`, `ERR_HDR_VIOLATION`.
- Produces: complete `_exec_op(m: _Machine, op: ir.Op) -> None` handling all five op kinds; `m.values[value_id] = (value, width)` bookkeeping that Task 3's dispatch reads.

- [ ] **Step 1: Write the failing tests**

Append to `compiler/tests/test_interp.py` (also extend the import from `nanuk_ir.interp` with `ERR_HDR_VIOLATION`):

```python
# -- linear ops (values mirror the stage-1 Sail test vectors) ----------------

def one_state(ops: list[ir.Op], term: ir.Terminator | None = None) -> ir.Program:
    return prog(ir.State(name="s", ops=ops, terminator=term or halt()))


def ext(vid: int, boff: int, width: int) -> ir.Op:
    return ir.Op(extract=ir.Extract(value_id=vid, bit_offset=boff, width=width))


def smd_op(vid: int, slot: int) -> ir.Op:
    return ir.Op(emit_smd=ir.EmitSmd(value_id=vid, slot=slot))


def test_extract_crossing_byte_boundary():
    # bits 4..11 of 0xAB,0xCD = 0xBC (network order, bit 0 = MSB)
    p = one_state([ext(1, 4, 8), smd_op(1, 0)])
    assert interp(p, b"\xab\xcd").smd[0] == 0xBC


def test_extract_sub_byte_ihl():
    # low nibble of 0x45 (IPv4 version/IHL byte) = 5
    p = one_state([ext(1, 4, 4), smd_op(1, 0)])
    assert interp(p, b"\x45").smd[0] == 5


def test_extract_past_hdr_limit_is_error_1_and_counted():
    p = one_state([ext(1, 0, 16)])
    r = interp(p, b"\xff")  # 16 bits wanted, 8 available
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_VIOLATION)
    assert r.steps == 1  # the failing EXT was fetched, hence counted


def test_advance_const_moves_cursor_into_payload_offset():
    p = one_state([ir.Op(advance=ir.Advance(const_bytes=3))])
    r = interp(p, b"\x00" * 8)
    assert r.accepted and r.payload_offset == 3


def test_advance_past_hdr_limit_is_error_1():
    p = one_state([ir.Op(advance=ir.Advance(const_bytes=9))])
    r = interp(p, b"\x00" * 8)
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_HDR_VIOLATION)
    assert r.payload_offset == 0  # cursor unchanged by the failing ADVI


def test_advance_by_value_uses_low_16_bits():
    # 24-bit value 0x010002: ADVR must advance by 0x0002, not 0x10002.
    p = one_state([ext(1, 0, 24), ir.Op(advance=ir.Advance(value_id=1))])
    r = interp(p, b"\x01\x00\x02" + b"\x00" * 5)
    assert r.accepted and r.payload_offset == 2


def test_shift_widens_and_truncates_at_64():
    # 60-bit extract shifted by 8: width saturates at 64, value masked.
    body = [
        ext(1, 0, 60),
        ir.Op(shift=ir.Shift(value_id=2, src_value_id=1, amount=8)),
        smd_op(2, 0),  # 64-bit value -> 4 slots
    ]
    r = interp(one_state(body), b"\xff" * 8)
    assert r.smd[:4] == [0xFFFF, 0xFFFF, 0xFFFF, 0xFF00]


def test_mark_records_cursor_and_reanchor_is_free():
    body = [
        ir.Op(advance=ir.Advance(const_bytes=2)),
        ir.Op(mark=ir.Mark(hdr_id=3, emit_sethdr=True)),
        ir.Op(mark=ir.Mark(emit_sethdr=False)),  # re-anchor: no step, no record
    ]
    r = interp(one_state(body), b"\x00" * 4)
    assert r.hdr(3) == 2
    assert r.hdr_present == [0, 0, 0, 1] + [0] * 12
    assert r.steps == 3  # advi + sethdr + halt; the re-anchor cost nothing


def test_emit_smd_multi_slot_msb_first():
    # 48-bit DMAC aa:bb:cc:dd:ee:01 -> slots 0..2 MSB-first (stage-1 vector)
    p = one_state([ext(1, 0, 48), smd_op(1, 0)])
    r = interp(p, bytes.fromhex("aabbccddee01") + b"\x00" * 8)
    assert r.smd[:3] == [0xAABB, 0xCCDD, 0xEE01]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./dev.sh bash -lc 'cd compiler && uv run --group dev pytest tests/test_interp.py -q'`
Expected: Task 1's 7 still pass; the 9 new ones FAIL with `NotImplementedError`

- [ ] **Step 3: Implement `_exec_op`**

In `compiler/nanuk_ir/interp.py`, replace the `_exec_op` stub with:

```python
def _exec_op(m: _Machine, op: ir.Op) -> None:
    match op.WhichOneof("op"):
        case "extract":  # EXT
            e = op.extract
            m.tick()
            p = m.cursor * 8 + e.bit_offset
            if p + e.width > m.hdr_limit * 8:
                m.halt_err(ERR_HDR_VIOLATION)
            first, last = p // 8, (p + e.width - 1) // 8
            chunk = int.from_bytes(m.packet[first : last + 1], "big")
            drop = (last - first + 1) * 8 - (p % 8) - e.width
            m.values[e.value_id] = ((chunk >> drop) & ((1 << e.width) - 1), e.width)
        case "shift":  # SHL
            sh = op.shift
            m.tick()
            src, src_width = m.values[sh.src_value_id]
            m.values[sh.value_id] = (
                (src << sh.amount) & _MASK64,
                min(64, src_width + sh.amount),
            )
        case "advance":  # ADVI / ADVR
            adv = op.advance
            m.tick()
            if adv.WhichOneof("amount") == "const_bytes":
                amount = adv.const_bytes
            else:
                amount = m.values[adv.value_id][0] & _MASK16  # ADVR uses rs[15:0]
            if m.cursor + amount > m.hdr_limit:
                m.halt_err(ERR_HDR_VIOLATION)
            m.cursor += amount
        case "mark":  # SETHDR — or, for a re-anchor, nothing at all
            if op.mark.emit_sethdr:
                m.tick()
                m.hdr_present[op.mark.hdr_id] = 1
                m.hdr_offset[op.mark.hdr_id] = m.cursor
        case "emit_smd":  # STMD
            e = op.emit_smd
            m.tick()
            value, width = m.values[e.value_id]
            nunits = (width + 15) // 16
            for i in range(nunits):  # MSB-first; in range per validate()
                m.smd[e.slot + i] = (value >> (16 * (nunits - 1 - i))) & _MASK16
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./dev.sh bash -lc 'cd compiler && uv run --group dev pytest tests/test_interp.py -q'`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add compiler/nanuk_ir/interp.py compiler/tests/test_interp.py
git commit -m "Interpret the linear IR ops: extract, shift, advance, mark, emit_smd

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Dispatch, validation-by-default, package export

**Files:**
- Modify: `compiler/nanuk_ir/interp.py` (complete `_exec_terminator`)
- Modify: `compiler/nanuk_ir/__init__.py`
- Test: `compiler/tests/test_interp.py` (append)

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: complete `_exec_terminator` (dispatch: first-match-wins, full-value equality, cost 2 per case tried + default's own cost); `nanuk_ir` package exports `interp` and `InterpResult`.

- [ ] **Step 1: Write the failing tests**

Append to `compiler/tests/test_interp.py`:

```python
# -- dispatch and the cost model ---------------------------------------------

def dispatch(vid: int, cases: list[tuple[int, str]], default: ir.Terminator) -> ir.Terminator:
    return ir.Terminator(dispatch=ir.Dispatch(
        value_id=vid,
        cases=[ir.Case(match=m, target_state=t) for m, t in cases],
        default=default,
    ))


def two_way(cases, default=None) -> ir.Program:
    """start extracts byte 0 and dispatches; 'acc' accepts, 'drp' drops."""
    return prog(
        ir.State(name="start", ops=[ext(1, 0, 8)],
                 terminator=dispatch(1, cases, default or halt(drop=True))),
        ir.State(name="acc", terminator=halt(drop=False)),
        ir.State(name="drp", terminator=halt(drop=True)),
    )


def test_dispatch_first_match_wins():
    p = two_way([(0x42, "drp"), (0x42, "acc")])
    assert interp(p, b"\x42").verdict == VERDICT_DROP


def test_dispatch_falls_through_to_default():
    p = two_way([(0x01, "acc")])
    assert interp(p, b"\x42").verdict == VERDICT_DROP


def test_dispatch_compares_full_value_not_low_16_bits():
    # 24-bit value 0x01BEEF must NOT match case 0xBEEF.
    p = prog(
        ir.State(name="start", ops=[ext(1, 0, 24)],
                 terminator=dispatch(1, [(0xBEEF, "acc")], halt(drop=True))),
        ir.State(name="acc", terminator=halt(drop=False)),
    )
    assert interp(p, b"\x01\xbe\xef").verdict == VERDICT_DROP


def test_dispatch_cost_is_two_per_case_tried():
    # match on 2nd case: ext(1) + [movi+beq](2) + [movi+beq](2) + halt(1) = 6
    p = two_way([(0x01, "drp"), (0x42, "acc")])
    r = interp(p, b"\x42")
    assert r.accepted and r.steps == 6
    # no match: ext(1) + 2*2 tried + default halt(1) = 6
    r = interp(p, b"\x99")
    assert r.verdict == VERDICT_DROP and r.steps == 6


def test_dispatch_default_goto_costs_a_jmp():
    p = two_way([(0x01, "acc")], default=goto("drp"))
    # ext(1) + movi+beq(2) + jmp(1) + halt(1) = 5
    assert interp(p, b"\x42").steps == 5


def test_budget_can_exhaust_mid_dispatch():
    # 'a' goto 'b' goto 'a' ... consumes 254 steps; then in 'spin' below the
    # dispatch's movi lands on step 256 and the beq is the 257th attempt.
    # Simpler equivalent: a 1-state ext+self-goto loop; each lap is 2 steps
    # (ext, jmp), so the 128th lap's ext is step 255, jmp is 256, and the
    # next lap's ext attempt is #257 -> budget error, steps saturated.
    p = prog(ir.State(name="a", ops=[ext(1, 0, 8)], terminator=goto("a")))
    r = interp(p, b"\xff")
    assert (r.verdict, r.error) == (VERDICT_ERROR, ERR_STEP_BUDGET)
    assert r.steps == STEP_BUDGET


# -- validation and exports ---------------------------------------------------

def test_invalid_program_rejected_by_default():
    from nanuk_ir.validate import ValidationError
    bad = prog(ir.State(name="s", terminator=goto("nowhere")))
    with pytest.raises(ValidationError):
        interp(bad, b"")


def test_check_false_skips_validation():
    ok = prog(ir.State(name="s", terminator=halt()))
    assert interp(ok, b"", check=False).accepted


def test_package_exports():
    import nanuk_ir
    assert nanuk_ir.interp is interp
    from nanuk_ir import InterpResult  # noqa: F401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./dev.sh bash -lc 'cd compiler && uv run --group dev pytest tests/test_interp.py -q'`
Expected: dispatch tests FAIL with `NotImplementedError`; `test_invalid_program_rejected_by_default` PASSES already (validate is wired since Task 1); `test_package_exports` FAILS with `AttributeError`

- [ ] **Step 3: Implement dispatch and the export**

In `compiler/nanuk_ir/interp.py`, replace `_exec_terminator`'s fallback case with:

```python
        case "dispatch":  # MOVI+BEQ per case in order, then the default inline
            d = term.dispatch
            value = m.values[d.value_id][0]
            for case_ in d.cases:
                m.tick()  # MOVI rscratch, match
                m.tick()  # BEQ value, rscratch, target
                if case_.match == value:
                    return case_.target_state
            return _exec_terminator(m, d.default)
```

In `compiler/nanuk_ir/__init__.py`, add to the imports and `__all__`:

```python
from .interp import InterpResult, interp
```

(and `"InterpResult", "interp",` in `__all__`, keeping alphabetical order.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./dev.sh bash -lc 'cd compiler && uv run --group dev pytest tests -q'`
Expected: full compiler suite passes (existing 39 + 26 new)

- [ ] **Step 5: Commit**

```bash
git add compiler/nanuk_ir/interp.py compiler/nanuk_ir/__init__.py compiler/tests/test_interp.py
git commit -m "Interpret dispatch and export the interpreter from nanuk_ir

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Differential rig — interp(IR) vs emulate(lower(IR))

**Files:**
- Create: `compiler/tests/test_differential.py`

**Interfaces:**
- Consumes: `interp` (Tasks 1–3), `to_asm` from `nanuk_ir.lower`, `rich_program` from `tests/test_roundtrip.py`, `assemble` from `nanuk_spec.asm`, `run_program` from `nanuk_spec.harness` (nanuk-spec is already in compiler's dev group).
- Produces: nothing downstream; this is the rig itself.

- [ ] **Step 1: Write the test file**

Create `compiler/tests/test_differential.py`:

```python
"""Translation validation, the light way: interp(IR) and
emulate(lower(IR)) must agree on EVERY ParseResult field — including
`steps` and budget exhaustion, since the interpreter's cost accounting
mirrors the v0 lowering instruction-for-instruction.

Gated behind NANUK_COSIM=1 (needs the built nanuk-emu golden model)."""

import os
import random

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.interp import interp
from nanuk_ir.lower import to_asm
from nanuk_spec.asm import assemble
from nanuk_spec.harness import run_program

from test_roundtrip import rich_program

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="differential rig needs NANUK_COSIM=1 and a built nanuk-emu",
)

FIELDS = ("verdict", "error", "payload_offset", "steps",
          "hdr_present", "hdr_offset", "smd")


def assert_same(program: ir.Program, packet: bytes, label: str) -> None:
    ir_result = interp(program, packet)
    emu_result = run_program(assemble(to_asm(program)), packet)
    for field in FIELDS:
        assert getattr(ir_result, field) == getattr(emu_result, field), (
            f"{label}: field {field!r} diverges: "
            f"interp={getattr(ir_result, field)!r} "
            f"emu={getattr(emu_result, field)!r} packet={packet.hex()}"
        )


def budget_loop() -> ir.Program:
    """Extract + self-goto forever: exhausts the step budget on any packet
    long enough to extract from, exercising error-2 + steps parity."""
    return ir.Program(ir_version=1, states=[
        ir.State(
            name="spin",
            ops=[ir.Op(extract=ir.Extract(value_id=1, bit_offset=0, width=8))],
            terminator=ir.Terminator(goto=ir.Goto(target_state="spin")),
        ),
    ])


def edge_packets() -> list[bytes]:
    return [
        b"",                        # empty: extracts fail immediately
        b"\x00",                    # 1 byte
        b"\xbe\xef" + b"\x00" * 5,  # 7 bytes: rich_program's advi 7 lands exactly
        b"\xbe\xef" + b"\x00" * 6,  # 8 bytes: one to spare
        bytes(range(64)),           # plenty
    ]


@pytest.mark.parametrize("pkt", edge_packets(), ids=lambda p: f"len{len(p)}")
def test_rich_program_edges(pkt):
    assert_same(rich_program(), pkt, "rich/edge")


@pytest.mark.parametrize("pkt", edge_packets(), ids=lambda p: f"len{len(p)}")
def test_budget_loop_edges(pkt):
    assert_same(budget_loop(), pkt, "loop/edge")


@pytest.mark.parametrize("seed", range(10))
def test_rich_program_random_packets(seed):
    rng = random.Random(3000 + seed)
    for i in range(10):
        pkt = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 65)))
        assert_same(rich_program(), pkt, f"rich/seed={seed} pkt={i}")
```

- [ ] **Step 2: Run gated-off to prove clean import**

Run: `./dev.sh bash -lc 'cd compiler && uv run --group dev pytest tests/test_differential.py -q'`
Expected: all SKIPPED (no `NANUK_COSIM`), zero errors

- [ ] **Step 3: Run the rig for real**

Run: `./dev.sh bash -lc 'cd compiler && NANUK_COSIM=1 uv run --group dev pytest tests/test_differential.py -q'`
Expected: 20 passed. If a field diverges, the interpreter (or its cost model) is wrong — fix `interp.py`, not the assertion; the golden model is the spec.

- [ ] **Step 4: Commit**

```bash
git add compiler/tests/test_differential.py
git commit -m "Differentially test interp(IR) against the lowered golden model

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Real-program parity — l2l3l4 and nanukproto over the demo corpus

**Files:**
- Modify: `examples/nanukproto/parse.py` (expose `make_parser()` / `build_ir()`, mirroring `lang/nanuk_lang/programs/l2l3l4.py`)
- Create: `lang/tests/test_interp_parity.py`

**Interfaces:**
- Consumes: `interp` via `nanuk_ir` (main dep of nanuk-lang), `build_ir()` from `nanuk_lang.programs.l2l3l4`, `CORPUS` from `lang/tests/test_parity.py`, `assemble`/`run_program` from nanuk-spec (dev group).
- Produces: `examples/nanukproto/parse.py` gains `make_parser() -> Parser` and `build_ir()` (keeping `build() -> str` behavior identical).

- [ ] **Step 1: Refactor nanukproto's entry points**

In `examples/nanukproto/parse.py`: rename `def build() -> str:` to `def make_parser() -> Parser:`, change its last line `return p.compile()` to `return p`, and append after it:

```python
def build_ir():
    """The nanuk.ir.v0 Program (for satellites: interpreter, playground)."""
    return make_parser().build_ir()


def build() -> str:
    return make_parser().compile()
```

- [ ] **Step 2: Verify nothing broke**

Run: `./dev.sh bash -lc 'cd lang && NANUK_COSIM=1 uv run --group dev pytest tests/test_nanukproto.py -q'`
Expected: 7 passed (same as before the refactor)

- [ ] **Step 3: Write the parity test**

Create `lang/tests/test_interp_parity.py`:

```python
"""The IR interpreter agrees with the golden model on the REAL programs:
l2l3l4 and nanukproto build_ir() over the full demo corpus (and a couple
of tunnel packets nanukproto alone can reach). Together with test_parity
(eDSL == hand asm) this closes the triangle: interp == emu == hand.

Gated behind NANUK_COSIM=1 (needs the built nanuk-emu golden model)."""

import importlib.util
import os
import struct
from pathlib import Path

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

from nanuk_ir.interp import interp
from nanuk_ir.lower import to_asm
from nanuk_lang.programs.l2l3l4 import build_ir as l2l3l4_ir
from nanuk_spec.asm import assemble
from nanuk_spec.harness import run_program

from test_parity import CORPUS, DMAC

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="interp parity needs NANUK_COSIM=1 and a built nanuk-emu",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "nanukproto_parse", REPO_ROOT / "examples" / "nanukproto" / "parse.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
nanukproto_ir = _mod.build_ir

FIELDS = ("verdict", "error", "payload_offset", "steps",
          "hdr_present", "hdr_offset", "smd")


def nk_tunnel(magic=0x4E4B, version=1) -> bytes:
    """An Ethernet frame carrying the invented tunnel around IPv4/UDP."""
    nk_hdr = (struct.pack(">H", magic)
              + bytes([(version << 4)])
              + (0x0ABCDE).to_bytes(3, "big")
              + struct.pack(">H", 0x0800))
    inner = bytes(IP(dst="10.0.0.2") / UDP(dport=4242) / b"hi")
    eth = bytes.fromhex("aabbccddee01") + bytes(6) + struct.pack(">H", 0x88B5)
    return eth + nk_hdr + inner


EXTRA = [
    ("nk_tunnel_good", nk_tunnel()),
    ("nk_bad_magic", nk_tunnel(magic=0x1234)),
    ("nk_bad_version", nk_tunnel(version=7)),
]

PACKETS = [(name, bytes(pkt)) for name, pkt in CORPUS] + EXTRA


@pytest.fixture(scope="module", params=["l2l3l4", "nanukproto"])
def program(request):
    return (l2l3l4_ir if request.param == "l2l3l4" else nanukproto_ir)()


@pytest.mark.parametrize("pkt", [p for _, p in PACKETS], ids=[n for n, _ in PACKETS])
def test_interp_matches_golden_model(program, pkt):
    ir_result = interp(program, pkt)
    emu_result = run_program(assemble(to_asm(program)), pkt)
    for field in FIELDS:
        assert getattr(ir_result, field) == getattr(emu_result, field), (
            f"field {field!r}: interp={getattr(ir_result, field)!r} "
            f"emu={getattr(emu_result, field)!r}"
        )
```

- [ ] **Step 4: Run it**

Run: `./dev.sh bash -lc 'cd lang && NANUK_COSIM=1 uv run --group dev pytest tests/test_interp_parity.py -q'`
Expected: 28 passed (14 packets × 2 programs). A `steps` divergence here means the cost model missed a lowering detail — fix `interp.py`.

- [ ] **Step 5: Run the full lang suite, then commit**

Run: `./dev.sh bash -lc 'cd lang && NANUK_COSIM=1 uv run --group dev pytest tests -q'`
Expected: all pass (62 prior + 28 new)

```bash
git add examples/nanukproto/parse.py lang/tests/test_interp_parity.py
git commit -m "Close the parity triangle: interp == golden model on real programs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Docs touch-up and full verification

**Files:**
- Modify: `README.md` (compiler line in Layout)
- Modify: `docs/superpowers/specs/2026-07-11-ir-interpreter-playground-design.md` (status line)

**Interfaces:** none — bookkeeping.

- [ ] **Step 1: Update the README layout line**

Change `compiler/ protobuf nanuk IR: schema, validation, and IR -> assembly lowering` to:

```
compiler/ protobuf nanuk IR: schema, validation, IR -> assembly lowering, interpreter
```

- [ ] **Step 2: Update the design doc status**

In `docs/superpowers/specs/2026-07-11-ir-interpreter-playground-design.md`, change the `**Status:**` line to:

```
**Status:** IR interpreter implemented (see `compiler/nanuk_ir/interp.py` + differential rigs in `compiler/tests/test_differential.py`, `lang/tests/test_interp_parity.py`). Playground: approved, not started.
```

- [ ] **Step 3: Full CI-equivalent run**

Run (from repo root):

```bash
./dev.sh bash -lc '
set -e
cmake -B build && cmake --build build --target check && cmake --build build
ctest --test-dir build --output-on-failure
(cd spec/python && uv sync --quiet && uv run pytest -q)
(cd hw && uv sync --quiet && NANUK_COSIM=1 uv run pytest tests -q)
(cd lang && uv sync --quiet && NANUK_COSIM=1 uv run --group dev pytest tests -q)
(cd compiler && uv sync --quiet && NANUK_COSIM=1 uv run --group dev pytest tests -q)
echo ALL_CI_CHECKS_PASSED'
```

Expected: ends with `ALL_CI_CHECKS_PASSED`

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-07-11-ir-interpreter-playground-design.md
git commit -m "Note the IR interpreter in README and design-doc status

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** (design doc Decision 1): interpret the protobuf IR at `compiler/nanuk_ir/interp.py`, ~200 lines, five ops + three terminators ✓ (T1–3); semantics mirror ISA totality (bounds, budgets, all defined) ✓ (T1 budget, T2 bounds, error codes); `interp(IR)` vs `emulate(lower(IR))` differential ✓ (T4 synthetic + T5 real programs); "chassis for symbolic executor / playground" needs no code now — the public `interp()`/`InterpResult` surface is the chassis. Decision 2/3 items (source spans, Pyodide, ISS) are playground scope, deliberately out.

**Cost-model soundness:** every lowering emission is mapped: ext/shl/advi/advr/stmd/sethdr = 1 (T2), re-anchor mark = 0 (T2 test), halt/jmp = 1 (T1), dispatch = 2·(cases tried) + default cost (T3 tests); budget check-before-execute with counted failing instruction matches `exec.sail` (T1/T2 tests; T4/T5 verify against the real emulator on every field including `steps`).

**Type consistency:** `interp(program, packet, *, check=True)`, `InterpResult` field names/order match `ParseResult` exactly (`verdict, error, payload_offset, steps, hdr_present, hdr_offset, smd`); helper names (`prog/halt/goto/ext/smd_op/dispatch/two_way/one_state`) used consistently across T1–T3 test appends; T4/T5 import surfaces verified against actual files (`rich_program` in `compiler/tests/test_roundtrip.py`, `CORPUS`/`DMAC` in `lang/tests/test_parity.py`, `build_ir` in `lang/nanuk_lang/programs/l2l3l4.py`).
