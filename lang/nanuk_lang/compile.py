"""State-level compilation: value handles, cursor tracking, register allocation.

The compiler is deliberately monolithic (no IR): each parse-state function
runs once against a StateCompiler, which emits assembly lines directly.

Register discipline
    r0..r2 hold extracted / shifted values, allocated linearly per state;
    values stay live from creation to the end of the state (no freeing).
    r3 is RESERVED as the scratch register for dispatch/compare constants
    (MOVI + BEQ pairs). Needing a fourth concurrent value is a compile error.

Cursor discipline
    Within a state the compiler tracks ``delta`` — bytes advanced since state
    entry — plus an ``epoch`` that increments on every dynamic (register)
    advance. ``mark`` anchors a header at the current (epoch, delta);
    ``extract`` requires a same-epoch anchor and a non-negative effective bit
    offset ``field.bit_offset - (delta - anchor_delta) * 8`` (EXT can only
    read forward from the cursor). After a dynamic advance the old anchors
    are unreachable; re-``mark`` against the new cursor to extract again.
"""

from .header import CompileError, Field, Header

_VALUE_REGS = ("r0", "r1", "r2")
_SCRATCH_REG = "r3"  # reserved for dispatch/compare constants

_MAX_EXT_BOFF = (1 << 11) - 1  # EXT bit-offset field is 11 bits
_MAX_IMM16 = (1 << 16) - 1
_MAX_SHAMT = 63
_SMD_SLOTS = 8
_MAX_HDR_ID = 15


def _shift_of(value, shamt: int):
    """Shared <<-builder for Value and Shifted handles."""
    if not isinstance(shamt, int) or isinstance(shamt, bool) or shamt < 0:
        raise CompileError(f"shift amount must be a non-negative integer, got {shamt!r}")
    base, total = (
        (value.base, value.shamt + shamt) if isinstance(value, Shifted) else (value, shamt)
    )
    if total > _MAX_SHAMT:
        raise CompileError(f"shift amount {total} out of range 0..{_MAX_SHAMT} (SHL)")
    if total == 0:
        return base
    return Shifted(base, total)


def _mul_of(value, factor: int):
    """``value * 2**k`` sugar; powers of two only (v0 has SHL, no MUL)."""
    if not isinstance(factor, int) or isinstance(factor, bool) or factor < 1:
        raise CompileError(f"can only multiply a value by a positive integer, got {factor!r}")
    if factor & (factor - 1):
        raise CompileError(
            f"cannot multiply {value.name} by {factor}: only powers of two "
            "(the v0 ISA has SHL but no multiply)"
        )
    return _shift_of(value, factor.bit_length() - 1)


class Value:
    """Opaque handle for an extracted value living in a register."""

    def __init__(self, reg: str, width: int, name: str):
        self.reg = reg
        self.width = width
        self.name = name

    def __lshift__(self, shamt):
        return _shift_of(self, shamt)

    def __mul__(self, factor):
        return _mul_of(self, factor)

    __rmul__ = __mul__

    def __repr__(self) -> str:
        return f"<value {self.name} in {self.reg}>"


class Shifted:
    """A derived handle ``base << shamt``; materialized with SHL at use time."""

    def __init__(self, base: Value, shamt: int):
        self.base = base
        self.shamt = shamt
        self.width = min(64, base.width + shamt)
        self.name = f"{base.name} << {shamt}"

    def __lshift__(self, shamt):
        return _shift_of(self, shamt)

    def __mul__(self, factor):
        return _mul_of(self, factor)

    __rmul__ = __mul__

    def __repr__(self) -> str:
        return f"<value {self.name}>"


class Terminator:
    """``s.accept`` / ``s.drop``: callable as a statement, passable to dispatch."""

    def __init__(self, sc: "StateCompiler", kind: str):
        self._sc = sc
        self.kind = kind  # "accept" | "drop"

    def __call__(self) -> None:
        self._sc._check_open()
        self._sc._emit(f"halt    {self.kind}")
        self._sc._terminated = True

    def __repr__(self) -> str:
        return f"<{self.kind}>"


class StateCompiler:
    """The ``s`` object handed to each @p.state function."""

    def __init__(self, state_name: str, states: set):
        self._state_name = state_name
        self._states = states  # registered State objects; dispatch/goto targets
        self._lines: list[str] = []
        self._live: list[Value] = []
        self._anchors: dict[Header, tuple[int, int]] = {}  # header -> (epoch, delta)
        self._epoch = 0
        self._delta = 0
        self._terminated = False
        self.accept = Terminator(self, "accept")
        self.drop = Terminator(self, "drop")

    # -- statements ---------------------------------------------------------

    def mark(self, header: Header, hdr_id: int | None = None) -> None:
        """Anchor `header` at the current cursor; with hdr_id, also SETHDR."""
        self._check_open()
        if not isinstance(header, Header):
            raise CompileError(f"mark expects a Header, got {header!r}")
        self._anchors[header] = (self._epoch, self._delta)
        if hdr_id is not None:
            if not isinstance(hdr_id, int) or not 0 <= hdr_id <= _MAX_HDR_ID:
                raise CompileError(
                    f"hdr_id {hdr_id!r} out of range 0..{_MAX_HDR_ID} (SETHDR)"
                )
            self._emit(f"sethdr  {hdr_id}", comment=header.name)

    def extract(self, field: Field) -> Value:
        """EXT a header field into a fresh register; returns a value handle."""
        self._check_open()
        if not isinstance(field, Field):
            raise CompileError(f"extract expects a header field, got {field!r}")
        header = field.header
        anchor = self._anchors.get(header)
        if anchor is None:
            raise CompileError(
                f"state {self._state_name!r}: header {header.name!r} is not marked "
                f"in this state; call s.mark({header.name}) before extracting "
                f"{field.qualname}"
            )
        anchor_epoch, anchor_delta = anchor
        if anchor_epoch != self._epoch:
            raise CompileError(
                f"state {self._state_name!r}: cannot extract {field.qualname} — the "
                f"cursor moved by a register amount since {header.name!r} was marked, "
                "so its offset is no longer statically known; re-mark a header at the "
                "new cursor and extract from that"
            )
        boff = field.bit_offset - (self._delta - anchor_delta) * 8
        if boff < 0:
            raise CompileError(
                f"state {self._state_name!r}: field {field.qualname} is behind the "
                "cursor (EXT only reads forward); extract it before advancing"
            )
        if boff > _MAX_EXT_BOFF:
            raise CompileError(
                f"state {self._state_name!r}: field {field.qualname} sits {boff} bits "
                f"ahead of the cursor; EXT offsets max out at {_MAX_EXT_BOFF}"
            )
        reg = self._alloc(field.qualname)
        value = Value(reg, field.width, field.qualname)
        self._live.append(value)
        self._emit(f"ext     {reg}, {boff}, {field.width}", comment=field.qualname)
        return value

    def smd(self, value, *, slot: int) -> None:
        """STMD a value into SMD slots starting at `slot` (ceil(width/16) units)."""
        self._check_open()
        reg, width, name = self._materialize(value)
        nunits = (width + 15) // 16
        if not isinstance(slot, int) or slot < 0:
            raise CompileError(f"SMD slot must be a non-negative integer, got {slot!r}")
        if slot + nunits > _SMD_SLOTS:
            raise CompileError(
                f"{name}: {width} bits need SMD slots {slot}..{slot + nunits - 1}, "
                f"but only slots 0..{_SMD_SLOTS - 1} exist"
            )
        self._emit(f"stmd    {slot}, {reg}, {nunits}", comment=name)

    def advance(self, amount) -> None:
        """Advance the cursor: int -> ADVI (offset tracking follows);
        value handle -> (SHL +) ADVR, after which extract offsets are unknown."""
        self._check_open()
        if isinstance(amount, int) and not isinstance(amount, bool):
            if not 0 <= amount <= _MAX_IMM16:
                raise CompileError(
                    f"advance amount {amount} out of range 0..{_MAX_IMM16} (ADVI)"
                )
            self._emit(f"advi    {amount}")
            self._delta += amount
        elif isinstance(amount, (Value, Shifted)):
            reg, _, name = self._materialize(amount)
            self._emit(f"advr    {reg}", comment=name)
            self._epoch += 1
            self._delta = 0
        else:
            raise CompileError(
                f"advance expects an int or an extracted value, got {amount!r}"
            )

    def dispatch(self, value, arms: dict, *, default) -> None:
        """Compare-and-branch chain: MOVI+BEQ per arm, then the default.

        Arms map 16-bit constants to states; default is s.accept, s.drop,
        or another state. Terminates the state.
        """
        self._check_open()
        reg, _, name = self._materialize(value)
        for const, target in arms.items():
            if not isinstance(const, int) or isinstance(const, bool) or const < 0:
                raise CompileError(f"dispatch constant {const!r} must be a non-negative int")
            if const > _MAX_IMM16:
                raise CompileError(
                    f"dispatch constant {const:#x} does not fit in 16 bits "
                    "(MOVI immediates are 16 bits; wide compares are a v0.x feature)"
                )
            label = self._target_label(target, f"dispatch arm {const:#x}")
            self._emit(f"movi    {_SCRATCH_REG}, {const:#06x}")
            self._emit(f"beq     {reg}, {_SCRATCH_REG}, {label}", comment=name)
        if isinstance(default, Terminator):
            self._emit(f"halt    {default.kind}")
        else:
            label = self._target_label(default, "dispatch default")
            self._emit(f"jmp     {label}")
        self._terminated = True

    def goto(self, target) -> None:
        """Unconditional JMP to another state. Terminates the state."""
        self._check_open()
        label = self._target_label(target, "goto")
        self._emit(f"jmp     {label}")
        self._terminated = True

    # -- internals ----------------------------------------------------------

    def _check_open(self) -> None:
        if self._terminated:
            raise CompileError(
                f"state {self._state_name!r}: statement after the state was "
                "terminated by accept/drop/goto/dispatch"
            )

    def _emit(self, instr: str, comment: str | None = None) -> None:
        if comment:
            instr = f"{instr:<26} ; {comment}"
        self._lines.append(instr)

    def _alloc(self, name: str) -> str:
        used = {v.reg for v in self._live}
        for reg in _VALUE_REGS:
            if reg not in used:
                return reg
        live = ", ".join(v.name for v in self._live)
        raise CompileError(
            f"state {self._state_name!r}: out of registers allocating {name!r}; "
            f"live values: {live} ({_SCRATCH_REG} is reserved for compare constants)"
        )

    def _materialize(self, value) -> tuple[str, int, str]:
        """Return (reg, width, name); emits SHL for derived handles."""
        if isinstance(value, Value):
            return value.reg, value.width, value.name
        if isinstance(value, Shifted):
            reg = self._alloc(value.name)
            materialized = Value(reg, value.width, value.name)
            self._live.append(materialized)
            self._emit(f"shl     {reg}, {value.base.reg}, {value.shamt}", comment=value.name)
            return reg, value.width, value.name
        raise CompileError(f"expected an extracted value, got {value!r}")

    def _target_label(self, target, what: str) -> str:
        if target not in self._states:
            raise CompileError(
                f"state {self._state_name!r}: {what} target {target!r} is not a "
                "state of this parser"
            )
        return target.name
