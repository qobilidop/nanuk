"""MAP-IR interpreter: executes nanuk.ir.v0 MapPrograms directly.

Sibling of interp.py, same doctrine: semantics mirror the Sail MAP model's
totality (window clamp, headroom, error codes, step budget), and step
accounting follows lower_map's cost model instruction-for-instruction —
every MapInterpResult field, including `steps` and budget exhaustion,
matches the golden model's MapResult exactly. If lower_map's cost model
changes, this file changes with it.

Error codes 3 (illegal) and 4 (pc range) are structurally impossible at IR
level. 1/2/5/6 (window violation, budget, absent header, send range) can
all occur — the IR deliberately carries the ISA's byte-granular window
semantics.
"""

from dataclasses import dataclass

from . import nanuk_ir_pb2 as ir
from .validate_map import validate_map

# Mirror of spec/map-model/params.sail (see also nanuk_spec.map_harness).
HEADROOM_BYTES = 32
BUF_BYTES = 256
WIN_BYTES = 288
N_PORTS = 4
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
class MapInterpResult:
    """Field-for-field compatible with nanuk_spec.map_harness.MapResult."""

    verdict: int
    error: int
    egress: int
    delta: int
    steps: int
    frame: bytes | None

    @property
    def sent(self) -> bool:
        return self.verdict == VERDICT_SENT


class _Halted(Exception):
    def __init__(self, verdict: int, error: int, egress: int = 0, delta: int = 0):
        self.verdict = verdict
        self.error = error
        self.egress = egress
        self.delta = delta


class _Machine:
    def __init__(self, packet: bytes, pp, tables, ingress: int):
        self.window = bytearray(WIN_BYTES)
        self.window[HEADROOM_BYTES : HEADROOM_BYTES + min(len(packet), BUF_BYTES)] = (
            packet[:BUF_BYTES]
        )
        self.plen_min = min(len(packet), BUF_BYTES)
        self.win_limit = HEADROOM_BYTES + self.plen_min
        self.pp = pp
        self.tables = list(tables)
        self.ingress = ingress
        self.steps = 0
        self.values: dict[int, int] = {}

    def tick(self) -> None:
        # Budget checked before the instruction runs, counted once fetched
        # (mirrors spec/map-model/exec.sail step()).
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


def interp_map(
    program: ir.MapProgram,
    packet: bytes,
    pp,
    tables,
    ingress: int,
    *,
    check: bool = True,
) -> MapInterpResult:
    """Execute a MAP IR program. Total, like the ISA.

    pp: ParseResult-shaped (hdr_present/hdr_offset/smd). tables: list of
    nanuk_spec.map_harness.Table, index = table id (entries masked to the
    declared widths, as every other implementation does).
    """
    if check:
        validate_map(program)
    m = _Machine(bytes(packet), pp, tables, ingress)
    states = {state.name: state for state in program.states}
    state = program.states[0]
    try:
        while True:
            m.values.clear()
            i = 0
            while i < len(state.ops):
                jump = _exec_op(m, state.ops[i])
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
        return MapInterpResult(
            verdict=halted.verdict,
            error=halted.error,
            egress=halted.egress,
            delta=halted.delta,
            steps=m.steps,
            frame=frame,
        )


def _mask(value: int, width: int) -> int:
    if width <= 0:
        return 0
    return value & ((1 << min(width, 64)) - 1)


def _exec_op(m: _Machine, op: ir.MapOp) -> str | None:
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
        case "load_md":  # LDMD
            md = op.load_md
            m.tick()
            if md.field < 8:
                v = m.pp.smd[md.field]
            elif md.field == 8:
                v = m.ingress
            elif md.field == 9:
                all_ports = (1 << N_PORTS) - 1
                v = all_ports & ~(1 << m.ingress) & all_ports
            elif md.field == 10:
                v = sum(
                    (1 << i) for i in range(16) if m.pp.hdr_present[i]
                )
            else:
                v = 0
            m.values[md.value_id] = v & _MASK64
        case "const":  # MOVI
            m.tick()
            m.values[op.const.value_id] = op.const.imm
        case "add":  # ADDI
            a = op.add
            m.tick()
            m.values[a.value_id] = (m.values[a.src_value_id] + a.imm) & _MASK64
        case "store":  # ST
            st = op.store
            m.tick()
            addr = m.eff_addr(st.hdr_id, st.byte_offset)
            m.check_window(addr, st.nbytes)
            m.window[addr : addr + st.nbytes] = (
                m.values[st.value_id] & ((1 << (8 * st.nbytes)) - 1)
            ).to_bytes(st.nbytes, "big")
        case "csum":  # CSUMUPD
            cs = op.csum
            m.tick()
            base = m.eff_addr(cs.hdr_id, cs.byte_offset)
            m.check_window(base, 20)
            ihl = m.window[base] & 0xF
            if ihl < 5:
                m.halt_err(ERR_WINDOW_VIOLATION)
            hlen = ihl * 4
            m.check_window(base, hlen)
            total = 0
            for i in range(0, hlen, 2):
                b0 = 0 if i == 10 else m.window[base + i]
                b1 = 0 if i == 10 else m.window[base + i + 1]
                total += (b0 << 8) | b1
            while total > 0xFFFF:
                total = (total & 0xFFFF) + (total >> 16)
            ck = total ^ 0xFFFF
            m.window[base + 10] = ck >> 8
            m.window[base + 11] = ck & 0xFF
        case "lookup":  # LOOKUP (fused branch-on-miss)
            lk = op.lookup
            m.tick()
            table = m.tables[lk.table_id] if lk.table_id < len(m.tables) else None
            if table is not None and table.key_width > 0:
                key = _mask(m.values[lk.key_value_id], table.key_width)
                for k, action in table.entries.items():
                    if _mask(k, table.key_width) == key:
                        m.values[lk.value_id] = _mask(action, table.action_width)
                        return None
            m.values[lk.value_id] = 0
            return lk.miss_state
    return None


def _exec_terminator(m: _Machine, term: ir.Terminator) -> str:
    match term.WhichOneof("kind"):
        case "send":  # SEND
            s = term.send
            m.tick()
            if s.delta > HEADROOM_BYTES or s.delta <= -m.plen_min:
                m.halt_err(ERR_SEND_RANGE)
            egress = m.values[s.bitmap_value_id] & ((1 << N_PORTS) - 1)
            raise _Halted(VERDICT_SENT, ERR_NONE, egress=egress, delta=s.delta)
        case "drop":  # DROP
            m.tick()
            raise _Halted(VERDICT_DROP, ERR_NONE)
        case "goto":  # JMP
            m.tick()
            return term.goto.target_state
        case "dispatch":  # MOVI+BEQ per case, then the default inline
            d = term.dispatch
            value = m.values[d.value_id]
            for case_ in d.cases:
                m.tick()  # MOVI rscratch, match
                m.tick()  # BEQ value, rscratch, target
                if case_.match == value:
                    return case_.target_state
            return _exec_terminator(m, d.default)
