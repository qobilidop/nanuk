"""MapProgram IR -> nanuk MAP assembly lowering. Sibling of lower.py.

Register discipline (same as the parser lowering)
    r0..r2 hold IR values, allocated linearly per state in op order; a
    value stays live from its defining op to the end of its state.
    r3 is RESERVED as the scratch register for dispatch/compare constants.

The cost model this lowering implies (instructions per op/terminator) is
mirrored instruction-for-instruction by interp_map.py — change one, change
both, and the differential tests will catch you if you don't.
"""

from . import nanuk_ir_pb2 as ir
from .lower import LowerError
from .validate_map import validate_map

_VALUE_REGS = ("r0", "r1", "r2")
_SCRATCH_REG = "r3"

_MAX_IMM16 = (1 << 16) - 1

_MD_NAMES = {8: "ingress", 9: "flood", 10: "hdr_present"}


def to_map_asm(program: ir.MapProgram, *, check: bool = True) -> str:
    """Lower a MapProgram to assembly text for nanuk_spec's map assembler."""
    if check:
        validate_map(program)
    lines: list[str] = []
    for state in program.states:
        lines.append(f"{state.name}:")
        lines.extend(f"    {line}" for line in _lower_state(state))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class _StateLowering:
    def __init__(self, state: ir.MapState):
        self.state = state
        self.lines: list[str] = []
        self.regs: dict[int, str] = {}
        self.names: dict[int, str] = {}
        self.order: list[int] = []

    def emit(self, instr: str, comment: str | None = None) -> None:
        if comment:
            instr = f"{instr:<30} ; {comment}"
        self.lines.append(instr)

    def alloc(self, value_id: int, name: str) -> str:
        used = set(self.regs.values())
        for reg in _VALUE_REGS:
            if reg not in used:
                self.regs[value_id] = reg
                self.names[value_id] = name
                self.order.append(value_id)
                return reg
        live = ", ".join(self.names[v] for v in self.order)
        raise LowerError(
            f"state {self.state.name!r}: out of registers allocating {name!r}; "
            f"live values: {live} ({_SCRATCH_REG} is reserved for compare constants)"
        )

    def reg_of(self, value_id: int, what: str) -> str:
        reg = self.regs.get(value_id)
        if reg is None:
            raise LowerError(
                f"state {self.state.name!r}: {what} uses value id {value_id} "
                "with no defining op in this state"
            )
        return reg


def _lower_state(state: ir.MapState) -> list[str]:
    lo = _StateLowering(state)
    for op in state.ops:
        match op.WhichOneof("op"):
            case "load":
                ld = op.load
                name = ld.debug_name or f"v{ld.value_id}"
                reg = lo.alloc(ld.value_id, name)
                lo.emit(
                    f"ld      {reg}, {ld.hdr_id}, {ld.byte_offset}, {ld.nbytes}",
                    comment=name,
                )
            case "load_md":
                md = op.load_md
                name = md.debug_name or _MD_NAMES.get(md.field, f"md{md.field}")
                reg = lo.alloc(md.value_id, name)
                lo.emit(f"ldmd    {reg}, {md.field}", comment=name)
            case "const":
                c = op.const
                name = c.debug_name or f"{c.imm:#x}"
                reg = lo.alloc(c.value_id, name)
                lo.emit(f"movi    {reg}, {c.imm:#06x}", comment=name)
            case "add":
                a = op.add
                src_reg = lo.reg_of(a.src_value_id, "add")
                name = f"{lo.names[a.src_value_id]} + {a.imm}"
                reg = lo.alloc(a.value_id, name)
                lo.emit(f"addi    {reg}, {src_reg}, {a.imm}", comment=name)
            case "store":
                st = op.store
                reg = lo.reg_of(st.value_id, "store")
                lo.emit(
                    f"st      {reg}, {st.hdr_id}, {st.byte_offset}, {st.nbytes}",
                    comment=st.debug_name or lo.names[st.value_id],
                )
            case "csum":
                cs = op.csum
                lo.emit(f"csumupd {cs.hdr_id}, {cs.byte_offset}")
            case "lookup":
                lk = op.lookup
                key_reg = lo.reg_of(lk.key_value_id, "lookup")
                name = f"lookup t{lk.table_id}[{lo.names[lk.key_value_id]}]"
                reg = lo.alloc(lk.value_id, name)
                lo.emit(
                    f"lookup  {reg}, {lk.table_id}, {key_reg}, {lk.miss_state}",
                    comment=name,
                )
    _lower_terminator(lo, state.terminator)
    return lo.lines


def _lower_terminator(lo: _StateLowering, term: ir.Terminator) -> None:
    match term.WhichOneof("kind"):
        case "send":
            s = term.send
            reg = lo.reg_of(s.bitmap_value_id, "send")
            lo.emit(f"send    {reg}, {s.delta}", comment=lo.names[s.bitmap_value_id])
        case "drop":
            lo.emit("drop")
        case "goto":
            lo.emit(f"jmp     {term.goto.target_state}")
        case "dispatch":
            d = term.dispatch
            reg = lo.reg_of(d.value_id, "dispatch")
            name = lo.names[d.value_id]
            for case_ in d.cases:
                if case_.match > _MAX_IMM16:
                    raise LowerError(
                        f"state {lo.state.name!r}: dispatch constant "
                        f"{case_.match:#x} does not fit in 16 bits (MOVI)"
                    )
                lo.emit(f"movi    {_SCRATCH_REG}, {case_.match:#06x}")
                lo.emit(
                    f"beq     {reg}, {_SCRATCH_REG}, {case_.target_state}",
                    comment=name,
                )
            _lower_terminator(lo, d.default)
        case None:
            raise LowerError(f"state {lo.state.name!r}: missing terminator")
