"""IR -> nanuk assembly lowering (moved here from lang/nanuk_lang/compile.py).

This is where parser-level IR meets the ISA: register allocation, MOVI
materialization of compare constants, and label emission.

Register discipline
    r0..r2 hold IR values, allocated linearly per state in op order;
    a value stays live from its defining op to the end of its state
    (no freeing — registers do not survive into the next state).
    r3 is RESERVED as the scratch register for dispatch/compare constants
    (MOVI + BEQ pairs). Needing a fourth concurrent value is a LowerError.

ISA encoding limits enforced here (the IR itself is above the ISA):
    dispatch case constants and ADVI amounts must fit 16 bits (MOVI/ADVI),
    EXT bit offsets must fit 11 bits.
"""

from . import nanuk_ir_pb2 as ir
from .validate import validate

_VALUE_REGS = ("r0", "r1", "r2")
_SCRATCH_REG = "r3"  # reserved for dispatch/compare constants

_MAX_EXT_BOFF = (1 << 11) - 1
_MAX_IMM16 = (1 << 16) - 1


class LowerError(Exception):
    """Raised when a (valid) IR program cannot be lowered to ISA v0."""


def to_asm(program: ir.Program, *, check: bool = True) -> str:
    """Lower an IR program to assembly text for nanuk_spec's assembler.

    With check=True (default) the program is validated first; lowering
    itself only raises LowerError for ISA-encoding limits (registers,
    immediate widths).
    """
    if check:
        validate(program)
    lines: list[str] = []
    for state in program.states:
        lines.append(f"{state.name}:")
        lines.extend(f"    {line}" for line in _lower_state(state))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class _StateLowering:
    def __init__(self, state: ir.State):
        self.state = state
        self.lines: list[str] = []
        self.regs: dict[int, str] = {}    # value_id -> register
        self.widths: dict[int, int] = {}  # value_id -> bit width
        self.names: dict[int, str] = {}   # value_id -> human-readable name
        self.order: list[int] = []        # value ids in allocation order

    def emit(self, instr: str, comment: str | None = None) -> None:
        if comment:
            instr = f"{instr:<26} ; {comment}"
        self.lines.append(instr)

    def alloc(self, value_id: int, width: int, name: str) -> str:
        used = set(self.regs.values())
        for reg in _VALUE_REGS:
            if reg not in used:
                self.regs[value_id] = reg
                self.widths[value_id] = width
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


def _lower_state(state: ir.State) -> list[str]:
    lo = _StateLowering(state)
    for op in state.ops:
        match op.WhichOneof("op"):
            case "extract":
                e = op.extract
                if e.bit_offset > _MAX_EXT_BOFF:
                    raise LowerError(
                        f"state {state.name!r}: extract bit offset {e.bit_offset} "
                        f"exceeds the EXT limit of {_MAX_EXT_BOFF}"
                    )
                name = e.debug_name or f"v{e.value_id}"
                reg = lo.alloc(e.value_id, e.width, name)
                lo.emit(f"ext     {reg}, {e.bit_offset}, {e.width}", comment=name)
            case "shift":
                sh = op.shift
                src_reg = lo.reg_of(sh.src_value_id, "shift")
                name = f"{lo.names[sh.src_value_id]} << {sh.amount}"
                width = min(64, lo.widths[sh.src_value_id] + sh.amount)
                reg = lo.alloc(sh.value_id, width, name)
                lo.emit(f"shl     {reg}, {src_reg}, {sh.amount}", comment=name)
            case "advance":
                adv = op.advance
                if adv.WhichOneof("amount") == "const_bytes":
                    if adv.const_bytes > _MAX_IMM16:
                        raise LowerError(
                            f"state {state.name!r}: advance amount {adv.const_bytes} "
                            f"exceeds the ADVI limit of {_MAX_IMM16}"
                        )
                    lo.emit(f"advi    {adv.const_bytes}")
                else:
                    reg = lo.reg_of(adv.value_id, "advance")
                    lo.emit(f"advr    {reg}", comment=lo.names[adv.value_id])
            case "mark":
                if op.mark.emit_sethdr:
                    lo.emit(
                        f"sethdr  {op.mark.hdr_id}",
                        comment=op.mark.debug_name or None,
                    )
                # emit_sethdr=False is a frontend re-anchor: lowers to nothing.
            case "emit_smd":
                smd = op.emit_smd
                reg = lo.reg_of(smd.value_id, "emit_smd")
                nunits = (lo.widths[smd.value_id] + 15) // 16
                lo.emit(
                    f"stmd    {smd.slot}, {reg}, {nunits}",
                    comment=lo.names[smd.value_id],
                )
    _lower_terminator(lo, state.terminator)
    return lo.lines


def _lower_terminator(lo: _StateLowering, term: ir.Terminator) -> None:
    match term.WhichOneof("kind"):
        case "halt":
            lo.emit(f"halt    {'drop' if term.halt.drop else 'accept'}")
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
