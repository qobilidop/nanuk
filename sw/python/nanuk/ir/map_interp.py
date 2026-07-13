"""MAP-IR interpreter: executes nanuk.ir.v0 MapPrograms directly.

Sibling of pp_interp.py, same doctrine: semantics mirror the Sail MAP model's
totality (window clamp, headroom, error codes, step budget), and step
accounting follows map_lower's cost model instruction-for-instruction —
every MatchActionInterpResult field, including `steps` and budget exhaustion,
matches the golden model's MatchActionResult exactly. If map_lower's cost model
changes, this file changes with it.

Error codes 3 (illegal) and 4 (pc range) are structurally impossible at IR
level (md slot bounds are validation-time). 1/2/5/6 (window violation,
budget, absent header, send range) can all occur — the IR deliberately
carries the ISA's byte-granular window semantics.
"""

from dataclasses import dataclass

from . import nanuk_ir_pb2 as ir
from .pp_interp import TraceEvent
from .map_validate import map_validate

# Mirror of spec/sail/model/map/params.sail (see also nanuk.testkit.map_harness).
HEADROOM_BYTES = 32
BUF_BYTES = 256
WIN_BYTES = 288
MD_SLOTS = 8
STEP_BUDGET = 256

VERDICT_SENT = 0
VERDICT_DROP = 1
VERDICT_ERROR = 2

ERR_NONE = 0
ERR_WINDOW_VIOLATION = 1
ERR_STEP_BUDGET = 2
ERR_HDR_ABSENT = 5
ERR_SEND_RANGE = 6

_MASK64 = (1 << 64) - 1


@dataclass(frozen=True)
class MatchActionInterpResult:
    """Field-for-field compatible with nanuk.testkit.map_harness.MatchActionResult."""

    verdict: int
    error: int
    md: tuple[int, ...]
    delta: int
    steps: int
    frame: bytes | None

    @property
    def sent(self) -> bool:
        return self.verdict == VERDICT_SENT


class _Halted(Exception):
    def __init__(self, verdict: int, error: int, delta: int = 0):
        self.verdict = verdict
        self.error = error
        self.delta = delta


class _Machine:
    def __init__(self, packet: bytes, pp, tables, md_in, trace=None):
        self.window = bytearray(WIN_BYTES)
        self.window[HEADROOM_BYTES : HEADROOM_BYTES + min(len(packet), BUF_BYTES)] = (
            packet[:BUF_BYTES]
        )
        self.plen_min = min(len(packet), BUF_BYTES)
        self.win_limit = HEADROOM_BYTES + self.plen_min
        self.pp = pp
        self.tables = list(tables)
        md = [v & 0xFFFF for v in md_in]
        self.md = md + [0] * (MD_SLOTS - len(md))
        self.steps = 0
        self.values: dict[int, int] = {}
        self.trace = trace
        self.state_name = ""

    def record(
        self,
        kind: str,
        index: int,
        values: dict[int, int] | None = None,
        writes: tuple = (),
        lookup: tuple | None = None,
    ) -> None:
        if self.trace is None:
            return
        self.trace.append(TraceEvent(
            state=self.state_name,
            kind=kind,
            index=index,
            steps_after=self.steps,
            values=dict(values or {}),
            writes=writes,
            lookup=lookup,
        ))

    def tick(self) -> None:
        # Budget checked before the instruction runs, counted once fetched
        # (mirrors spec/sail/model/map/exec.sail step()).
        if self.steps >= STEP_BUDGET:
            raise _Halted(VERDICT_ERROR, ERR_STEP_BUDGET)
        self.steps += 1

    def halt_err(self, code: int) -> None:
        raise _Halted(VERDICT_ERROR, code)

    def eff_addr(self, hdr_id: int, off: int) -> int:
        if hdr_id != 15:
            if not self.pp.hdr_present[hdr_id]:
                self.halt_err(ERR_HDR_ABSENT)
            base = self.pp.hdr_offset[hdr_id]
        else:
            base = 0
        return HEADROOM_BYTES + base + off

    def check_window(self, addr: int, nbytes: int) -> None:
        if addr < 0 or addr + nbytes > self.win_limit:
            self.halt_err(ERR_WINDOW_VIOLATION)


def map_interp(
    program: ir.MatchActionProgram,
    packet: bytes,
    pp,
    tables,
    md_in,
    *,
    check: bool = True,
    trace: list | None = None,
) -> MatchActionInterpResult:
    """Execute a MAP IR program. Total, like the ISA.

    pp: ParserResult-shaped (hdr_present/hdr_offset). md_in: up to 8 16-bit
    metadata slots (pass-through default). tables: list of
    nanuk.testkit.map_harness.Table, index = table id (entries masked to the
    declared widths, as every other implementation does). With a trace
    list, records one pp_interp.TraceEvent per executed IR event.
    """
    if check:
        map_validate(program)
    m = _Machine(bytes(packet), pp, tables, md_in, trace)
    states = {state.name: state for state in program.states}
    state = program.states[0]
    try:
        while True:
            m.values.clear()
            m.state_name = state.name
            i = 0
            while i < len(state.ops):
                try:
                    jump = _exec_op(m, state.ops[i], i)
                except _Halted as halted:
                    # Error-halting ops executed one instruction; budget
                    # halts executed nothing (mirrors pp_interp.py).
                    if halted.error != ERR_STEP_BUDGET:
                        m.record("op", i)
                    raise
                if jump is not None:  # lookup miss
                    state = states[jump]
                    break
                i += 1
            else:
                state = states[_exec_terminator(m, state.terminator)]
    except _Halted as halted:
        frame = None
        if halted.verdict == VERDICT_SENT:
            start = HEADROOM_BYTES - halted.delta
            end = HEADROOM_BYTES + m.plen_min
            frame = bytes(m.window[start:end]) + bytes(packet[BUF_BYTES:])
        return MatchActionInterpResult(
            verdict=halted.verdict,
            error=halted.error,
            md=tuple(m.md),
            delta=halted.delta,
            steps=m.steps,
            frame=frame,
        )


def _mask(value: int, width: int) -> int:
    if width <= 0:
        return 0
    return value & ((1 << min(width, 64)) - 1)


def _exec_op(m: _Machine, op: ir.MatchActionOp, index: int) -> str | None:
    """Execute one op; returns a state name on a lookup miss (control
    transfer), else None."""
    match op.WhichOneof("op"):
        case "load":  # LD
            ld = op.load
            m.tick()
            addr = m.eff_addr(ld.hdr_id, ld.byte_offset)
            m.check_window(addr, ld.nbytes)
            m.values[ld.value_id] = int.from_bytes(
                m.window[addr : addr + ld.nbytes], "big"
            )
            m.record("op", index, {ld.value_id: m.values[ld.value_id]})
        case "load_md":  # LDMD
            md = op.load_md
            m.tick()
            m.values[md.value_id] = m.md[md.slot]
            m.record("op", index, {md.value_id: m.values[md.value_id]})
        case "store_md":  # STMD
            sm = op.store_md
            m.tick()
            v = m.values[sm.value_id]
            n = sm.nunits
            for i in range(n):  # MSB-first; in range per map_validate()
                m.md[sm.slot + i] = (v >> (16 * (n - 1 - i))) & 0xFFFF
            m.record("op", index)
        case "and_imm":  # ANDI
            ai = op.and_imm
            m.tick()
            m.values[ai.value_id] = m.values[ai.src_value_id] & ai.imm
            m.record("op", index, {ai.value_id: m.values[ai.value_id]})
        case "shift":  # SHLI
            sh = op.shift
            m.tick()
            m.values[sh.value_id] = (m.values[sh.src_value_id] << sh.amount) & _MASK64
            m.record("op", index, {sh.value_id: m.values[sh.value_id]})
        case "const":  # MOVI
            m.tick()
            m.values[op.const.value_id] = op.const.imm
            m.record("op", index, {op.const.value_id: op.const.imm})
        case "add":  # ADDI
            a = op.add
            m.tick()
            m.values[a.value_id] = (m.values[a.src_value_id] + a.imm) & _MASK64
            m.record("op", index, {a.value_id: m.values[a.value_id]})
        case "store":  # ST
            st = op.store
            m.tick()
            addr = m.eff_addr(st.hdr_id, st.byte_offset)
            m.check_window(addr, st.nbytes)
            data = (
                m.values[st.value_id] & ((1 << (8 * st.nbytes)) - 1)
            ).to_bytes(st.nbytes, "big")
            m.window[addr : addr + st.nbytes] = data
            m.record("op", index, writes=((addr, data),))
        case "csum":  # CSUM — generic RFC 1071 range checksum into a value
            cs = op.csum
            m.tick()
            base = m.eff_addr(cs.hdr_id, cs.byte_offset)
            length = m.values[cs.len_value_id] & 0xFFFF
            m.check_window(base, length)
            total = 0
            for i in range(0, length, 2):
                b0 = m.window[base + i]
                b1 = m.window[base + i + 1] if i + 1 < length else 0
                total += (b0 << 8) | b1
            while total > 0xFFFF:
                total = (total & 0xFFFF) + (total >> 16)
            m.values[cs.value_id] = total ^ 0xFFFF
            m.record("op", index, {cs.value_id: m.values[cs.value_id]})
        case "lookup":  # LOOKUP (fused branch-on-miss)
            lk = op.lookup
            m.tick()
            table = m.tables[lk.table_id] if lk.table_id < len(m.tables) else None
            if table is not None and table.key_width > 0:
                key = _mask(m.values[lk.key_value_id], table.key_width)
                for k, action in table.entries.items():
                    if _mask(k, table.key_width) == key:
                        act = _mask(action, table.action_width)
                        m.values[lk.value_id] = act
                        m.record(
                            "op", index,
                            {lk.value_id: act},
                            lookup=(lk.table_id, key, True, act),
                        )
                        return None
            else:
                key = 0
            m.values[lk.value_id] = 0
            m.record(
                "op", index, {lk.value_id: 0}, lookup=(lk.table_id, key, False, 0)
            )
            return lk.miss_state
    return None


def _exec_terminator(m: _Machine, term: ir.Terminator, default: bool = False) -> str:
    kind = "term_default" if default else "term"
    match term.WhichOneof("kind"):
        case "send":  # SEND
            s = term.send
            m.tick()
            if s.delta > HEADROOM_BYTES or s.delta <= -m.plen_min:
                m.record(kind, 0)
                m.halt_err(ERR_SEND_RANGE)
            m.record(kind, 0)
            raise _Halted(VERDICT_SENT, ERR_NONE, delta=s.delta)
        case "drop":  # DROP
            m.tick()
            m.record(kind, 0)
            raise _Halted(VERDICT_DROP, ERR_NONE)
        case "goto":  # JMP
            m.tick()
            m.record(kind, 0)
            return term.goto.target_state
        case "dispatch":  # MOVI+BEQ per case, then the default inline
            d = term.dispatch
            value = m.values[d.value_id]
            for j, case_ in enumerate(d.cases):
                m.tick()  # MOVI rscratch, match
                m.tick()  # BEQ value, rscratch, target
                m.record("term_case", j)
                if case_.match == value:
                    return case_.target_state
            return _exec_terminator(m, d.default, default=True)
