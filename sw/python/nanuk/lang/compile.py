"""State-level IR building: value handles and cursor tracking.

Each parse-state function runs once against a StateCompiler, which builds
nanuk IR ops (protos from nanuk-ir). Instruction selection, register
allocation, and label emission live downstream in nanuk.ir.pp_lower — this
module owns only the *frontend* concerns:

Value handles
    s.extract returns an opaque handle; the only operators are ``<<`` and
    ``* 2**k`` (v0 has SHL only). Derived handles stay symbolic until a use
    site (advance/smd/dispatch) materializes them as an IR Shift op.

Cursor discipline
    Within a state the compiler tracks ``delta`` — bytes advanced since state
    entry — plus an ``epoch`` that increments on every dynamic (register)
    advance. ``mark`` anchors a header at the current (epoch, delta);
    ``extract`` requires a same-epoch anchor and a non-negative effective bit
    offset ``field.bit_offset - (delta - anchor_delta) * 8`` (EXT can only
    read forward from the cursor). After a dynamic advance the old anchors
    are unreachable; re-``mark`` against the new cursor to extract again.
    The emitted IR carries cursor-relative offsets only — anchor/epoch
    bookkeeping never leaves the frontend.
"""

from nanuk.ir import nanuk_ir_pb2 as ir

from .header import CompileError, Field, Header

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
    """Opaque handle for an extracted value (an SSA-ish IR value id)."""

    def __init__(self, value_id: int, width: int, name: str):
        self.value_id = value_id
        self.width = width
        self.name = name

    def __lshift__(self, shamt):
        return _shift_of(self, shamt)

    def __mul__(self, factor):
        return _mul_of(self, factor)

    __rmul__ = __mul__

    def __repr__(self) -> str:
        return f"<value {self.name} (v{self.value_id})>"


class Shifted:
    """A derived handle ``base << shamt``; becomes an IR Shift op at use time."""

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

    def _ir(self) -> ir.Terminator:
        return ir.Terminator(halt=ir.Halt(drop=self.kind == "drop"))

    def __call__(self) -> None:
        self._sc._check_open()
        self._sc._terminator = self._ir()

    def __repr__(self) -> str:
        return f"<{self.kind}>"


class StateCompiler:
    """The ``s`` object handed to each @p.state function; builds one IR ParserState."""

    def __init__(self, state_name: str, states: set, value_ids):
        self._state_name = state_name
        self._states = states  # registered ParserState objects; dispatch/goto targets
        self._value_ids = value_ids  # program-wide id counter (ids unique per program)
        self._ops: list[ir.ParserOp] = []
        self._terminator: ir.Terminator | None = None
        self._anchors: dict[Header, tuple[int, int]] = {}  # header -> (epoch, delta)
        self._epoch = 0
        self._delta = 0
        self.accept = Terminator(self, "accept")
        self.drop = Terminator(self, "drop")

    # -- statements ---------------------------------------------------------

    def mark(self, header: Header, hdr_id: int | None = None) -> None:
        """Anchor `header` at the current cursor; with hdr_id, also SETHDR."""
        self._check_open()
        if not isinstance(header, Header):
            raise CompileError(f"mark expects a Header, got {header!r}")
        self._anchors[header] = (self._epoch, self._delta)
        if hdr_id is None:
            mark = ir.Mark(emit_sethdr=False, debug_name=header.name)
        else:
            if not isinstance(hdr_id, int) or not 0 <= hdr_id <= _MAX_HDR_ID:
                raise CompileError(
                    f"hdr_id {hdr_id!r} out of range 0..{_MAX_HDR_ID} (SETHDR)"
                )
            mark = ir.Mark(hdr_id=hdr_id, emit_sethdr=True, debug_name=header.name)
        self._ops.append(ir.ParserOp(mark=mark))

    def extract(self, field: Field) -> Value:
        """Extract a header field into a fresh IR value; returns a handle."""
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
        value = Value(next(self._value_ids), field.width, field.qualname)
        self._ops.append(
            ir.ParserOp(
                extract=ir.Extract(
                    value_id=value.value_id,
                    bit_offset=boff,
                    width=field.width,
                    debug_name=field.qualname,
                )
            )
        )
        return value

    def smd(self, value, *, slot: int) -> None:
        """Emit a value into SMD slots starting at `slot` (ceil(width/16) units)."""
        self._check_open()
        materialized = self._materialize(value)
        nunits = (materialized.width + 15) // 16
        if not isinstance(slot, int) or slot < 0:
            raise CompileError(f"SMD slot must be a non-negative integer, got {slot!r}")
        if slot + nunits > _SMD_SLOTS:
            raise CompileError(
                f"{materialized.name}: {materialized.width} bits need SMD slots "
                f"{slot}..{slot + nunits - 1}, but only slots 0..{_SMD_SLOTS - 1} exist"
            )
        self._ops.append(
            ir.ParserOp(emit_smd=ir.EmitSmd(value_id=materialized.value_id, slot=slot))
        )

    def advance(self, amount) -> None:
        """Advance the cursor: int -> constant advance (offset tracking follows);
        value handle -> register advance, after which extract offsets are unknown."""
        self._check_open()
        if isinstance(amount, int) and not isinstance(amount, bool):
            if not 0 <= amount <= _MAX_IMM16:
                raise CompileError(
                    f"advance amount {amount} out of range 0..{_MAX_IMM16} (ADVI)"
                )
            self._ops.append(ir.ParserOp(advance=ir.Advance(const_bytes=amount)))
            self._delta += amount
        elif isinstance(amount, (Value, Shifted)):
            materialized = self._materialize(amount)
            self._ops.append(ir.ParserOp(advance=ir.Advance(value_id=materialized.value_id)))
            self._epoch += 1
            self._delta = 0
        else:
            raise CompileError(
                f"advance expects an int or an extracted value, got {amount!r}"
            )

    def dispatch(self, value, arms: dict, *, default) -> None:
        """Compare-and-branch: ordered cases over 16-bit constants, then the
        default (s.accept, s.drop, or another state). Terminates the state."""
        self._check_open()
        materialized = self._materialize(value)
        cases = []
        for const, target in arms.items():
            if not isinstance(const, int) or isinstance(const, bool) or const < 0:
                raise CompileError(f"dispatch constant {const!r} must be a non-negative int")
            if const > _MAX_IMM16:
                raise CompileError(
                    f"dispatch constant {const:#x} does not fit in 16 bits "
                    "(MOVI immediates are 16 bits; wide compares are a v0.x feature)"
                )
            label = self._target_label(target, f"dispatch arm {const:#x}")
            cases.append(ir.Case(match=const, target_state=label))
        if isinstance(default, Terminator):
            default_ir = default._ir()
        else:
            label = self._target_label(default, "dispatch default")
            default_ir = ir.Terminator(goto=ir.Goto(target_state=label))
        self._terminator = ir.Terminator(
            dispatch=ir.Dispatch(
                value_id=materialized.value_id, cases=cases, default=default_ir
            )
        )

    def goto(self, target) -> None:
        """Unconditional transfer to another state. Terminates the state."""
        self._check_open()
        label = self._target_label(target, "goto")
        self._terminator = ir.Terminator(goto=ir.Goto(target_state=label))

    # -- internals ----------------------------------------------------------

    def _check_open(self) -> None:
        if self._terminator is not None:
            raise CompileError(
                f"state {self._state_name!r}: statement after the state was "
                "terminated by accept/drop/goto/dispatch"
            )

    def _materialize(self, value) -> Value:
        """Return a plain Value; emits an IR Shift op for derived handles."""
        if isinstance(value, Value):
            return value
        if isinstance(value, Shifted):
            materialized = Value(next(self._value_ids), value.width, value.name)
            self._ops.append(
                ir.ParserOp(
                    shift=ir.Shift(
                        value_id=materialized.value_id,
                        src_value_id=value.base.value_id,
                        amount=value.shamt,
                    )
                )
            )
            return materialized
        raise CompileError(f"expected an extracted value, got {value!r}")

    def _target_label(self, target, what: str) -> str:
        if target not in self._states:
            raise CompileError(
                f"state {self._state_name!r}: {what} target {target!r} is not a "
                "state of this parser"
            )
        return target.name
