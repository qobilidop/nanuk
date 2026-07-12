"""IR-level interpreter: executes nanuk.ir.v0 Programs directly.

Fills the IR-level execution gap (assembly-level execution already has two
implementations: the Sail golden model and the RTL core; IR-level had
zero). Because these semantics are defined here, independent of lower.py,
running interp(program, packet) against emulate(lower(program), packet)
differentially tests the compiler — a lightweight translation-validation
rig (compiler/tests/test_differential.py, lang/tests/test_interp_parity.py).

Semantics mirror ISA totality (spec/parser-model, frozen in the stage-1 plan):
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

# Mirror of spec/parser-model/params.sail (see also nanuk_spec.harness).
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
        # Mirrors step() in spec/parser-model/exec.sail: budget checked before the
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
        case "dispatch":  # MOVI+BEQ per case in order, then the default inline
            d = term.dispatch
            value = m.values[d.value_id][0]
            for case_ in d.cases:
                m.tick()  # MOVI rscratch, match
                m.tick()  # BEQ value, rscratch, target
                if case_.match == value:
                    return case_.target_state
            return _exec_terminator(m, d.default)
