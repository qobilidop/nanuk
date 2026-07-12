"""Symbolic executor over parser IR Programs (the formal satellite's first
piece; p4v / p4-symbolic / p4pktgen precedent).

Enumerates feasible execution paths with Z3: the packet is a symbolic byte
array + symbolic length; every EXT/advance forks an in-bounds path and a
header-violation path when both are satisfiable; dispatch forks per case.
Each path yields a WITNESS packet (path-coverage corpus generation — the
satellite's headline payoff) and an exact predicted (verdict, error,
steps): step accounting mirrors pp_interp.py tick-for-tick, so witnesses are
differentially validated against pp_interp AND the golden emulator on all
three fields.

v1 pragmatics, documented rather than hidden:
- loops are bounded by an `unroll` visit limit per state (the step budget
  alone admits 256-deep paths); paths pruned by the limit are simply not
  emitted — symex() is an under-approximation of the path set, never a
  wrong one (every emitted path is feasible and exact);
- enumeration stops at `max_paths` (again: under-approximation);
- hdr/SMD outputs are not predicted (verdict/error/steps are).

Requires z3-solver (compiler dev group; never shipped in wheels).
"""

from dataclasses import dataclass

import z3

from . import nanuk_ir_pb2 as ir
from .pp_interp import (
    BUF_BYTES,
    ERR_HDR_VIOLATION,
    ERR_STEP_BUDGET,
    STEP_BUDGET,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
)
from .pp_validate import pp_validate

_MASK64 = (1 << 64) - 1


@dataclass(frozen=True)
class SymPath:
    trace: tuple[str, ...]
    verdict: int
    error: int
    steps: int
    witness: bytes | None
    witness_plen: int


class _Budget(Exception):
    pass


class _Sym:
    """One in-flight path: constraints + concrete step count + machine."""

    def __init__(self, pkt, plen, hdr_limit_bits):
        self.pkt = pkt
        self.plen = plen
        self.hdr_limit_bits = hdr_limit_bits  # BitVec(32), = min(plen,256)*8
        self.constraints: list = []
        self.cursor = z3.BitVecVal(0, 32)     # bytes; symbolic after ADVR
        self.steps = 0
        self.values: dict[int, tuple] = {}    # value_id -> (term64, width)
        self.trace: list[str] = []
        self.visits: dict[str, int] = {}

    def fork(self) -> "_Sym":
        c = _Sym(self.pkt, self.plen, self.hdr_limit_bits)
        c.constraints = list(self.constraints)
        c.cursor = self.cursor
        c.steps = self.steps
        c.values = dict(self.values)
        c.trace = list(self.trace)
        c.visits = dict(self.visits)
        return c

    def tick(self) -> None:
        # Mirrors pp_interp.tick(): budget checked before the instruction runs.
        if self.steps >= STEP_BUDGET:
            raise _Budget()
        self.steps += 1

    def sat(self, extra=()) -> z3.ModelRef | None:
        s = z3.Solver()
        s.add(*self.constraints, *extra)
        if s.check() != z3.sat:
            return None
        return s.model()


def _read_bits(m: _Sym, pos, width: int):
    """Concat 9 packet bytes from the byte at pos>>3, align, mask — the
    symbolic mirror of pp_interp's extract (and Sail's read_pkt_bits)."""
    first = z3.Extract(15, 0, z3.LShR(pos, 3))
    bib = z3.ZeroExt(64, z3.Extract(7, 0, pos & 7))  # bit-in-byte, 72-bit
    chunk = z3.Concat(*[z3.Select(m.pkt, first + i) for i in range(9)])
    aligned = z3.LShR(chunk, z3.BitVecVal(72 - width, 72) - bib)
    return z3.Extract(63, 0, aligned) & z3.BitVecVal((1 << width) - 1, 64)


def symex(
    program: ir.ParserProgram,
    *,
    max_packet: int = 128,
    unroll: int = 3,
    max_paths: int = 512,
    check: bool = True,
) -> list[SymPath]:
    """Enumerate feasible paths; every emitted path carries a witness."""
    if check:
        pp_validate(program)
    pkt = z3.Array("pkt", z3.BitVecSort(16), z3.BitVecSort(8))
    plen = z3.BitVec("plen", 16)
    hdr_limit = z3.If(
        z3.ULT(plen, z3.BitVecVal(BUF_BYTES, 16)), plen, z3.BitVecVal(BUF_BYTES, 16)
    )
    hdr_limit_bits = z3.ZeroExt(16, hdr_limit) * 8

    states = {st.name: st for st in program.states}
    paths: list[SymPath] = []

    root = _Sym(pkt, plen, hdr_limit_bits)
    root.constraints.append(z3.ULE(plen, max_packet))

    def emit(m: _Sym, verdict: int, error: int) -> None:
        if len(paths) >= max_paths:
            return
        model = m.sat()
        if model is None:
            return
        plen_val = model.eval(plen, model_completion=True).as_long()
        witness = bytes(
            model.eval(z3.Select(pkt, i), model_completion=True).as_long()
            for i in range(plen_val)
        )
        paths.append(
            SymPath(
                trace=tuple(m.trace),
                verdict=verdict,
                error=error,
                steps=m.steps,
                witness=witness,
                witness_plen=plen_val,
            )
        )

    def run_state(m: _Sym, name: str) -> None:
        if len(paths) >= max_paths:
            return
        m.visits[name] = m.visits.get(name, 0) + 1
        if m.visits[name] > unroll:
            return  # pruned: under-approximation, documented
        m.trace.append(name)
        st = states[name]
        try:
            for op in st.ops:
                if not _exec_op(m, op):
                    return  # this path continued in forks / ended
            _exec_terminator(m, st.terminator)
        except _Budget:
            emit(m, VERDICT_ERROR, ERR_STEP_BUDGET)

    def _exec_op(m: _Sym, op: ir.ParserOp) -> bool:
        """Returns False when the path was fully handled via forks."""
        match op.WhichOneof("op"):
            case "extract":
                e = op.extract
                m.tick()
                pos = m.cursor * 8 + e.bit_offset
                viol = z3.UGT(pos + e.width, m.hdr_limit_bits)
                bad = m.fork()
                bad.constraints.append(viol)
                if bad.sat() is not None:
                    emit(bad, VERDICT_ERROR, ERR_HDR_VIOLATION)
                m.constraints.append(z3.Not(viol))
                if m.sat() is None:
                    return False
                m.values[e.value_id] = (_read_bits(m, pos, e.width), e.width)
            case "shift":
                sh = op.shift
                m.tick()
                src, src_width = m.values[sh.src_value_id]
                m.values[sh.value_id] = (
                    (src << sh.amount) & z3.BitVecVal(_MASK64, 64),
                    min(64, src_width + sh.amount),
                )
            case "advance":
                adv = op.advance
                m.tick()
                if adv.WhichOneof("amount") == "const_bytes":
                    amount = z3.BitVecVal(adv.const_bytes, 32)
                else:
                    amount = z3.ZeroExt(
                        16, z3.Extract(15, 0, m.values[adv.value_id][0])
                    )
                ncur = m.cursor + amount
                viol = z3.UGT(ncur * 8, m.hdr_limit_bits)
                bad = m.fork()
                bad.constraints.append(viol)
                if bad.sat() is not None:
                    emit(bad, VERDICT_ERROR, ERR_HDR_VIOLATION)
                m.constraints.append(z3.Not(viol))
                if m.sat() is None:
                    return False
                m.cursor = ncur
            case "mark":
                if op.mark.emit_sethdr:
                    m.tick()
            case "emit_md":
                m.tick()
            case "load_md":
                md = op.load_md
                m.tick()
                sym = z3.BitVec(f"md_{md.slot}_{md.value_id}", 64)
                m.constraints.append(z3.ULE(sym, 0xFFFF))
                m.values[md.value_id] = (sym, 16)
        return True

    def _exec_terminator(m: _Sym, term: ir.Terminator) -> None:
        match term.WhichOneof("kind"):
            case "halt":
                m.tick()
                emit(
                    m,
                    VERDICT_DROP if term.halt.drop else VERDICT_ACCEPT,
                    0,
                )
            case "goto":
                m.tick()
                run_state(m, term.goto.target_state)
            case "dispatch":
                d = term.dispatch
                value = m.values[d.value_id][0]
                excluded: list = []
                for case_ in d.cases:
                    m.tick()  # MOVI
                    m.tick()  # BEQ
                    taken = m.fork()
                    taken.constraints.extend(excluded)
                    taken.constraints.append(
                        value == z3.BitVecVal(case_.match, 64)
                    )
                    if taken.sat() is not None:
                        run_state(taken, case_.target_state)
                    excluded.append(value != z3.BitVecVal(case_.match, 64))
                m.constraints.extend(excluded)
                if m.sat() is None:
                    return
                _exec_terminator(m, d.default)

    try:
        run_state(root, program.states[0].name)
    except _Budget:
        emit(root, VERDICT_ERROR, ERR_STEP_BUDGET)
    return paths


def gen_corpus(program: ir.ParserProgram, **kw) -> list[bytes]:
    """Deduped witness packets — one per feasible path (the pcap-corpus
    generator the design doc promised)."""
    seen: set[bytes] = set()
    out: list[bytes] = []
    for p in symex(program, **kw):
        if p.witness is not None and p.witness not in seen:
            seen.add(p.witness)
            out.append(p.witness)
    return out


def reachable_states(program: ir.ParserProgram, **kw) -> set[str]:
    """States on at least one feasible path (unreachable = defined dead code)."""
    reached: set[str] = set()
    for p in symex(program, **kw):
        reached.update(p.trace)
    return reached
