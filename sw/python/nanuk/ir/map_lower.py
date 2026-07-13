"""MatchActionProgram IR -> Nanuk MAP assembly lowering. Sibling of lower.py.

Register discipline
    r0..r2 hold IR values, allocated per state in op order; unlike the
    parser lowering, a register is FREED after its value's last use in the
    state (straight-line MAP programs materialize many short-lived
    constants — tunnel push writes an 11-constant header — so no-free
    allocation would starve). r3 is RESERVED as the scratch register for
    dispatch/compare constants.

The cost model this lowering implies (instructions per op/terminator) is
mirrored instruction-for-instruction by map_interp.py — change one, change
both, and the differential tests will catch you if you don't.
"""

from . import nanuk_ir_pb2 as ir
from .pp_lower import LowerError
from .map_validate import map_validate

_VALUE_REGS = ("r0", "r1", "r2")
_SCRATCH_REG = "r3"

_MAX_IMM16 = (1 << 16) - 1



# MapBinOp.Kind -> mnemonic. One IR op, five opcodes: the ISA keeps them flat
# (opcodes 0x10-0x14) so decode stays uniform, while the IR keeps them one
# message so the frontend has one shape to build.
_BIN_MNEMONIC = {1: "add", 2: "sub", 3: "and", 4: "or", 5: "xor"}



def to_map_asm(program: ir.MatchActionProgram, *, check: bool = True) -> str:
    """Lower a MatchActionProgram to assembly text for nanuk.isa's map assembler."""
    return to_map_asm_annotated(program, check=check)[0]


def to_map_asm_annotated(
    program: ir.MatchActionProgram, *, check: bool = True
) -> tuple[str, list[dict[str, str]]]:
    """to_map_asm, plus one {register: value name} binding snapshot per
    emitted instruction (emission order). With last-use liveness, a
    register visibly rebinds to the newest value after the old one dies."""
    if check:
        map_validate(program)
    lines: list[str] = []
    bindings: list[dict[str, str]] = []
    for state in program.states:
        lo = _lower_state(state)
        lines.append(f"{state.name}:")
        lines.extend(f"    {line}" for line in lo.lines)
        lines.append("")
        bindings.extend(lo.bindings)
    return "\n".join(lines).rstrip() + "\n", bindings


def _last_uses(state: ir.MatchActionState) -> dict[int, int]:
    """value_id -> index of its last consuming op (len(ops) = terminator)."""
    last: dict[int, int] = {}

    def note(vid: int, idx: int) -> None:
        if vid:
            last[vid] = idx

    for idx, op in enumerate(state.ops):
        match op.WhichOneof("op"):
            case "add":
                note(op.add.src_value_id, idx)
            case "and_imm":
                note(op.and_imm.src_value_id, idx)
            case "bin_op":
                note(op.bin_op.lhs_value_id, idx)
                note(op.bin_op.rhs_value_id, idx)
            case "shift":
                note(op.shift.src_value_id, idx)
            case "store":
                note(op.store.value_id, idx)
            case "store_md":
                note(op.store_md.value_id, idx)
            case "csum":
                note(op.csum.len_value_id, idx)
            case "lookup":
                note(op.lookup.key_value_id, idx)
    term = state.terminator
    t_idx = len(state.ops)
    match term.WhichOneof("kind"):
        case "dispatch":
            note(term.dispatch.value_id, t_idx)
    return last


class _StateLowering:
    def __init__(self, state: ir.MatchActionState):
        self.state = state
        self.lines: list[str] = []
        self.regs: dict[int, str] = {}
        self.names: dict[int, str] = {}
        self.order: list[int] = []
        self.last_uses = _last_uses(state)
        self.bindings: list[dict[str, str]] = []  # per emitted instruction

    def emit(self, instr: str, comment: str | None = None) -> None:
        if comment:
            instr = f"{instr:<30} ; {comment}"
        self.lines.append(instr)
        self.bindings.append({reg: self.names[v] for v, reg in self.regs.items()})

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

    def free_dead(self, op_idx: int) -> None:
        """Release registers whose values have no use after op_idx (called
        after source registers are captured, before the result allocates —
        so a dying source's register is immediately reusable)."""
        for vid in list(self.regs):
            if self.last_uses.get(vid, -1) <= op_idx:
                del self.regs[vid]


def _lower_state(state: ir.MatchActionState) -> "_StateLowering":
    lo = _StateLowering(state)
    for idx, op in enumerate(state.ops):
        match op.WhichOneof("op"):
            case "load":
                ld = op.load
                name = ld.debug_name or f"v{ld.value_id}"
                lo.free_dead(idx)
                reg = lo.alloc(ld.value_id, name)
                lo.emit(
                    f"ld      {reg}, {ld.hdr_id}, {ld.byte_offset}, {ld.nbytes}",
                    comment=name,
                )
            case "load_md":
                md = op.load_md
                name = md.debug_name or f"md{md.slot}"
                lo.free_dead(idx)
                reg = lo.alloc(md.value_id, name)
                lo.emit(f"ldmd    {reg}, {md.slot}", comment=name)
            case "const":
                c = op.const
                name = c.debug_name or f"{c.imm:#x}"
                lo.free_dead(idx)
                reg = lo.alloc(c.value_id, name)
                lo.emit(f"movi    {reg}, {c.imm:#06x}", comment=name)
            case "add":
                a = op.add
                src_reg = lo.reg_of(a.src_value_id, "add")
                name = f"{lo.names[a.src_value_id]} + {a.imm}"
                lo.free_dead(idx)
                reg = lo.alloc(a.value_id, name)
                lo.emit(f"addi    {reg}, {src_reg}, {a.imm}", comment=name)
            case "store":
                st = op.store
                reg = lo.reg_of(st.value_id, "store")
                lo.emit(
                    f"st      {reg}, {st.hdr_id}, {st.byte_offset}, {st.nbytes}",
                    comment=st.debug_name or lo.names[st.value_id],
                )
                lo.free_dead(idx)
            case "csum":
                cs = op.csum
                len_reg = lo.reg_of(cs.len_value_id, "csum")
                name = f"csum over {lo.names[cs.len_value_id]}"
                lo.free_dead(idx)
                reg = lo.alloc(cs.value_id, name)
                lo.emit(
                    f"csum    {reg}, {cs.hdr_id}, {cs.byte_offset}, {len_reg}",
                    comment=name,
                )
            case "store_md":
                sm = op.store_md
                reg = lo.reg_of(sm.value_id, "store_md")
                lo.emit(
                    f"stmd    {reg}, {sm.nunits}, {sm.slot}",
                    comment=lo.names[sm.value_id],
                )
                lo.free_dead(idx)
            case "and_imm":
                ai = op.and_imm
                src_reg = lo.reg_of(ai.src_value_id, "and_imm")
                name = f"{lo.names[ai.src_value_id]} & {ai.imm:#x}"
                lo.free_dead(idx)
                reg = lo.alloc(ai.value_id, name)
                lo.emit(f"andi    {reg}, {src_reg}, {ai.imm:#06x}", comment=name)
            case "bin_op":
                b = op.bin_op
                mn = _BIN_MNEMONIC[b.kind]
                lhs_reg = lo.reg_of(b.lhs_value_id, "bin_op")
                rhs_reg = lo.reg_of(b.rhs_value_id, "bin_op")
                name = f"{lo.names[b.lhs_value_id]} {mn} {lo.names[b.rhs_value_id]}"
                lo.free_dead(idx)
                reg = lo.alloc(b.value_id, name)
                lo.emit(f"{mn:<7} {reg}, {lhs_reg}, {rhs_reg}", comment=name)
            case "shift":
                sh = op.shift
                src_reg = lo.reg_of(sh.src_value_id, "shift")
                name = f"{lo.names[sh.src_value_id]} << {sh.amount}"
                lo.free_dead(idx)
                reg = lo.alloc(sh.value_id, name)
                lo.emit(f"shli    {reg}, {src_reg}, {sh.amount}", comment=name)
            case "lookup":
                lk = op.lookup
                key_reg = lo.reg_of(lk.key_value_id, "lookup")
                name = f"lookup t{lk.table_id}[{lo.names[lk.key_value_id]}]"
                lo.free_dead(idx)
                reg = lo.alloc(lk.value_id, name)
                lo.emit(
                    f"lookup  {reg}, {lk.table_id}, {key_reg}, {lk.miss_state}",
                    comment=name,
                )
    _lower_terminator(lo, state.terminator)
    return lo


def _lower_terminator(lo: _StateLowering, term: ir.Terminator) -> None:
    match term.WhichOneof("kind"):
        case "send":
            lo.emit(f"send    {term.send.delta}")
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
