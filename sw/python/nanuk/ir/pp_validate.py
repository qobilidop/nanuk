"""IR validation: totality checks on nanuk.ir.v0 Programs.

Rejects malformed programs before lowering (and before any satellite —
MLIR, translation validation — consumes them): unknown target states,
out-of-range widths/slots/header ids/shift amounts, value-id reuse
(ids are SSA-ish: unique per program, never 0), and use-before-def
(values are used only after their defining op, within the same state —
registers do not survive state entry in the v0 lowering).

ISA *encoding* limits (16-bit immediates, 11-bit EXT offsets, 4 GPRs)
are deliberately not checked here — the IR sits above the ISA; those
surface in lower.py.
"""

from . import nanuk_ir_pb2 as ir

_MAX_HDR_ID = 15
_SMD_SLOTS = 8
_MAX_SHAMT = 63

IR_VERSION = 1


class ValidationError(Exception):
    """Raised when a Program violates the Nanuk IR invariants."""


def pp_validate(program: ir.ParserProgram) -> None:
    """Raise ValidationError if `program` is not a well-formed IR program."""
    if program.ir_version != IR_VERSION:
        raise ValidationError(
            f"unsupported ir_version {program.ir_version}; expected {IR_VERSION}"
        )
    if not program.states:
        raise ValidationError("program has no states")

    state_names: set[str] = set()
    for state in program.states:
        if not state.name:
            raise ValidationError("state with empty name")
        if state.name in state_names:
            raise ValidationError(f"duplicate state name {state.name!r}")
        state_names.add(state.name)

    seen_ids: set[int] = set()
    for state in program.states:
        _validate_state(state, state_names, seen_ids)


def _validate_state(state: ir.ParserState, state_names: set[str], seen_ids: set[int]) -> None:
    where = f"state {state.name!r}"
    defined: dict[int, int] = {}  # value_id -> width, defs in this state

    def define(value_id: int, width: int, what: str) -> None:
        if value_id == 0:
            raise ValidationError(f"{where}: {what} has value_id 0 (ids start at 1)")
        if value_id in seen_ids:
            raise ValidationError(
                f"{where}: value id {value_id} reused by {what} "
                "(value ids are unique per program)"
            )
        seen_ids.add(value_id)
        defined[value_id] = width

    def use(value_id: int, what: str) -> int:
        if value_id not in defined:
            raise ValidationError(
                f"{where}: {what} uses value id {value_id} before it is defined "
                "in this state (values do not cross states)"
            )
        return defined[value_id]

    for op in state.ops:
        match op.WhichOneof("op"):
            case "extract":
                e = op.extract
                if not 1 <= e.width <= 64:
                    raise ValidationError(
                        f"{where}: extract width {e.width} out of range 1..64"
                    )
                define(e.value_id, e.width, "extract")
            case "shift":
                sh = op.shift
                if sh.amount > _MAX_SHAMT:
                    raise ValidationError(
                        f"{where}: shift amount {sh.amount} out of range 0..{_MAX_SHAMT}"
                    )
                src_width = use(sh.src_value_id, "shift")
                define(sh.value_id, min(64, src_width + sh.amount), "shift")
            case "advance":
                adv = op.advance
                match adv.WhichOneof("amount"):
                    case "value_id":
                        use(adv.value_id, "advance")
                    case "const_bytes":
                        pass
                    case None:
                        raise ValidationError(f"{where}: advance with no amount set")
            case "mark":
                if op.mark.emit_sethdr and op.mark.hdr_id > _MAX_HDR_ID:
                    raise ValidationError(
                        f"{where}: hdr_id {op.mark.hdr_id} out of range 0..{_MAX_HDR_ID}"
                    )
            case "emit_md":
                emd = op.emit_md
                width = use(emd.value_id, "emit_md")
                if not 1 <= emd.nunits <= 4:
                    raise ValidationError(
                        f"{where}: emit_md nunits {emd.nunits} out of range 1..4"
                    )
                if emd.nunits < (width + 15) // 16:
                    raise ValidationError(
                        f"{where}: emit_md of a {width}-bit value needs at least "
                        f"{(width + 15) // 16} units, got {emd.nunits}"
                    )
                if emd.slot + emd.nunits > _SMD_SLOTS:
                    raise ValidationError(
                        f"{where}: emit_md needs slots "
                        f"{emd.slot}..{emd.slot + emd.nunits - 1}, but only slots "
                        f"0..{_SMD_SLOTS - 1} exist"
                    )
            case "load_md":
                lmd = op.load_md
                if lmd.slot >= _SMD_SLOTS:
                    raise ValidationError(
                        f"{where}: load_md slot {lmd.slot} out of range "
                        f"0..{_SMD_SLOTS - 1}"
                    )
                define(lmd.value_id, 16, "load_md")
            case "movi":
                # imm width is an ISA-encoding limit (16-bit MOVI), checked in
                # pp_lower like the dispatch/ADVI immediates — not here.
                define(op.movi.value_id, 16, "movi")
            case None:
                raise ValidationError(f"{where}: empty Op (no oneof member set)")

    _validate_terminator(state.terminator, where, state_names, use, top_level=True)


def _validate_terminator(
    term: ir.Terminator, where: str, state_names: set[str], use, *, top_level: bool
) -> None:
    match term.WhichOneof("kind"):
        case "halt":
            pass
        case "goto":
            if term.goto.target_state not in state_names:
                raise ValidationError(
                    f"{where}: goto target {term.goto.target_state!r} is not a state"
                )
        case "dispatch":
            if not top_level:
                raise ValidationError(
                    f"{where}: dispatch default must be Halt or Goto, not a "
                    "nested Dispatch"
                )
            d = term.dispatch
            use(d.value_id, "dispatch")
            for case_ in d.cases:
                if case_.target_state not in state_names:
                    raise ValidationError(
                        f"{where}: dispatch case {case_.match:#x} targets unknown "
                        f"state {case_.target_state!r}"
                    )
            _validate_terminator(d.default, where, state_names, use, top_level=False)
        case None:
            raise ValidationError(f"{where}: missing terminator")
        case other:
            # send/drop belong to MapPrograms (map_validate), not parsers.
            raise ValidationError(
                f"{where}: terminator kind {other!r} is not allowed in "
                "parser programs"
            )
