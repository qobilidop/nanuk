"""MAP IR validation: totality checks on nanuk.ir.v0 MapPrograms.

Sibling of pp_validate.py, same doctrine: value ids are SSA-ish (unique per
program, never 0, never crossing states), targets must exist, and every
range the *IR* defines is enforced here. Encoding ranges that the MAP ISA
fixes (10-bit signed offsets/deltas, 16-bit immediates, 1..8 byte
accesses) are IR-level ranges too — the MatchActionProgram IR is deliberately
byte-granular and offset-bounded like its engine.

Lookup is the one op with control flow: its miss_state must exist, and the
no-cross-state-values rule already guarantees the miss state sees none of
the values defined before the lookup.
"""

from . import nanuk_ir_pb2 as ir
from .pp_validate import IR_VERSION, ValidationError

_N_TABLES = 4
_MAX_HDR_ID = 15
_MAX_MD_FIELD = 15
_MIN_OFF, _MAX_OFF = -512, 511
_MIN_DELTA, _MAX_DELTA = -512, 511
_MAX_IMM16 = (1 << 16) - 1
_MIN_SIMM16, _MAX_SIMM16 = -(1 << 15), (1 << 15) - 1


def map_validate(program: ir.MatchActionProgram) -> None:
    """Raise ValidationError if `program` is not a well-formed MAP program."""
    if program.ir_version != IR_VERSION:
        raise ValidationError(
            f"unsupported ir_version {program.ir_version}; expected {IR_VERSION}"
        )
    if not program.states:
        raise ValidationError("MAP program has no states")

    table_ids: set[int] = set()
    for t in program.tables:
        if t.table_id >= _N_TABLES:
            raise ValidationError(
                f"table {t.debug_name or t.table_id}: id {t.table_id} out of "
                f"range 0..{_N_TABLES - 1}"
            )
        if t.table_id in table_ids:
            raise ValidationError(f"duplicate table id {t.table_id}")
        table_ids.add(t.table_id)
        if not 1 <= t.key_width <= 64:
            raise ValidationError(
                f"table {t.debug_name or t.table_id}: key_width {t.key_width} "
                "out of range 1..64"
            )
        if not 1 <= t.action_width <= 64:
            raise ValidationError(
                f"table {t.debug_name or t.table_id}: action_width "
                f"{t.action_width} out of range 1..64"
            )

    state_names: set[str] = set()
    for state in program.states:
        if not state.name:
            raise ValidationError("state with empty name")
        if state.name in state_names:
            raise ValidationError(f"duplicate state name {state.name!r}")
        state_names.add(state.name)

    seen_ids: set[int] = set()
    for state in program.states:
        _validate_map_state(state, state_names, table_ids, seen_ids)


def _check_access(where: str, what: str, hdr_id: int, off: int, nbytes: int) -> None:
    if hdr_id > _MAX_HDR_ID:
        raise ValidationError(f"{where}: {what} hdr_id {hdr_id} out of range")
    if not _MIN_OFF <= off <= _MAX_OFF:
        raise ValidationError(
            f"{where}: {what} byte offset {off} out of range "
            f"{_MIN_OFF}..{_MAX_OFF}"
        )
    if not 1 <= nbytes <= 8:
        raise ValidationError(f"{where}: {what} nbytes {nbytes} out of range 1..8")


def _validate_map_state(
    state: ir.MatchActionState, state_names: set[str], table_ids: set[int], seen_ids: set[int]
) -> None:
    where = f"state {state.name!r}"
    defined: dict[int, int] = {}  # value_id -> width (bits)

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
            case "load":
                ld = op.load
                _check_access(where, "load", ld.hdr_id, ld.byte_offset, ld.nbytes)
                define(ld.value_id, ld.nbytes * 8, "load")
            case "load_md":
                md = op.load_md
                if md.field > _MAX_MD_FIELD:
                    raise ValidationError(
                        f"{where}: load_md field {md.field} out of range "
                        f"0..{_MAX_MD_FIELD}"
                    )
                define(md.value_id, 64, "load_md")
            case "const":
                c = op.const
                if c.imm > _MAX_IMM16:
                    raise ValidationError(
                        f"{where}: const {c.imm:#x} does not fit in 16 bits"
                    )
                define(c.value_id, 16, "const")
            case "add":
                a = op.add
                if not _MIN_SIMM16 <= a.imm <= _MAX_SIMM16:
                    raise ValidationError(
                        f"{where}: add immediate {a.imm} out of signed 16-bit range"
                    )
                use(a.src_value_id, "add")
                define(a.value_id, 64, "add")
            case "store":
                st = op.store
                _check_access(where, "store", st.hdr_id, st.byte_offset, st.nbytes)
                use(st.value_id, "store")
            case "csum":
                cs = op.csum
                if cs.hdr_id > _MAX_HDR_ID:
                    raise ValidationError(
                        f"{where}: csum hdr_id {cs.hdr_id} out of range"
                    )
                if not _MIN_OFF <= cs.byte_offset <= _MAX_OFF:
                    raise ValidationError(
                        f"{where}: csum byte offset {cs.byte_offset} out of range"
                    )
            case "lookup":
                lk = op.lookup
                if lk.table_id not in table_ids:
                    raise ValidationError(
                        f"{where}: lookup references undeclared table "
                        f"{lk.table_id}"
                    )
                use(lk.key_value_id, "lookup key")
                if lk.miss_state not in state_names:
                    raise ValidationError(
                        f"{where}: lookup miss target {lk.miss_state!r} is not "
                        "a state"
                    )
                define(lk.value_id, 64, "lookup")
            case None:
                raise ValidationError(f"{where}: empty MatchActionOp (no oneof member set)")

    _validate_map_terminator(state.terminator, where, state_names, use, top_level=True)


def _validate_map_terminator(
    term: ir.Terminator, where: str, state_names: set[str], use, *, top_level: bool
) -> None:
    match term.WhichOneof("kind"):
        case "send":
            s = term.send
            use(s.bitmap_value_id, "send")
            if not _MIN_DELTA <= s.delta <= _MAX_DELTA:
                raise ValidationError(
                    f"{where}: send delta {s.delta} out of range "
                    f"{_MIN_DELTA}..{_MAX_DELTA}"
                )
        case "drop":
            pass
        case "goto":
            if term.goto.target_state not in state_names:
                raise ValidationError(
                    f"{where}: goto target {term.goto.target_state!r} is not a state"
                )
        case "dispatch":
            if not top_level:
                raise ValidationError(
                    f"{where}: dispatch default must not be a nested Dispatch"
                )
            d = term.dispatch
            use(d.value_id, "dispatch")
            for case_ in d.cases:
                if case_.target_state not in state_names:
                    raise ValidationError(
                        f"{where}: dispatch case {case_.match:#x} targets unknown "
                        f"state {case_.target_state!r}"
                    )
            _validate_map_terminator(
                d.default, where, state_names, use, top_level=False
            )
        case None:
            raise ValidationError(f"{where}: missing terminator")
        case other:
            raise ValidationError(
                f"{where}: terminator kind {other!r} is not allowed in "
                "MAP programs"
            )
