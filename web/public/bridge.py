"""nanuk playground bridge: runs inside Pyodide (and under pytest).

Renders the IR pane text deterministically (so per-state line ranges are
known exactly), tracks per-op asm emission counts mirroring the v0
lowering, and exposes compile_source()/run_packet() as the JSON API
consumed by web/src/lib/py.ts. Line numbers are 1-based inclusive
everywhere."""

from dataclasses import dataclass, field

from nanuk_ir import nanuk_ir_pb2 as ir


@dataclass
class RenderedOp:
    label: str
    ir_line: int
    asm_count: int


@dataclass
class RenderedState:
    name: str
    ir_range: tuple[int, int]
    ops: list[RenderedOp] = field(default_factory=list)


@dataclass
class RenderedIr:
    text: str
    states: list[RenderedState]


def _value_name(names: dict[int, str], value_id: int) -> str:
    return names.get(value_id, f"v{value_id}")


def render_ir(program: ir.Program) -> RenderedIr:
    lines: list[str] = []
    states: list[RenderedState] = []
    for st in program.states:
        start_line = len(lines) + 1
        lines.append(f"{st.name}:")
        rstate = RenderedState(name=st.name, ir_range=(start_line, start_line))
        names: dict[int, str] = {}
        for op in st.ops:
            match op.WhichOneof("op"):
                case "extract":
                    e = op.extract
                    name = e.debug_name or f"v{e.value_id}"
                    names[e.value_id] = name
                    lines.append(
                        f"    v{e.value_id} = extract(boff={e.bit_offset}, "
                        f"w={e.width})  ; {name}"
                    )
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "shift":
                    sh = op.shift
                    name = f"{_value_name(names, sh.src_value_id)} << {sh.amount}"
                    names[sh.value_id] = name
                    lines.append(
                        f"    v{sh.value_id} = v{sh.src_value_id} << {sh.amount}"
                    )
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "advance":
                    adv = op.advance
                    if adv.WhichOneof("amount") == "const_bytes":
                        lines.append(f"    advance {adv.const_bytes}")
                        label = f"advance {adv.const_bytes}"
                    else:
                        label = f"advance {_value_name(names, adv.value_id)}"
                        lines.append(f"    advance v{adv.value_id}")
                    rstate.ops.append(RenderedOp(label, len(lines), 1))
                case "mark":
                    m = op.mark
                    disp = m.debug_name or f"hdr{m.hdr_id}"
                    if m.emit_sethdr:
                        lines.append(f"    mark hdr[{m.hdr_id}]  ; {disp}")
                        rstate.ops.append(RenderedOp(f"mark {disp}", len(lines), 1))
                    else:
                        lines.append(f"    mark (re-anchor)  ; {disp}")
                        rstate.ops.append(
                            RenderedOp(f"mark {disp} (re-anchor)", len(lines), 0)
                        )
                case "emit_smd":
                    s = op.emit_smd
                    name = _value_name(names, s.value_id)
                    lines.append(f"    smd[{s.slot}] = v{s.value_id}  ; {name}")
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
        _render_terminator(st.terminator, lines, rstate, names)
        rstate.ir_range = (start_line, len(lines))
        states.append(rstate)
        lines.append("")
    return RenderedIr(text="\n".join(lines).rstrip() + "\n", states=states)


def _render_terminator(
    term: ir.Terminator,
    lines: list[str],
    rstate: RenderedState,
    names: dict[int, str],
) -> None:
    match term.WhichOneof("kind"):
        case "halt":
            verdict = "drop" if term.halt.drop else "accept"
            lines.append(f"    halt {verdict}")
            rstate.ops.append(RenderedOp(f"halt {verdict}", len(lines), 1))
        case "goto":
            lines.append(f"    goto {term.goto.target_state}")
            rstate.ops.append(
                RenderedOp(f"goto {term.goto.target_state}", len(lines), 1)
            )
        case "dispatch":
            d = term.dispatch
            name = _value_name(names, d.value_id)
            lines.append(f"    dispatch v{d.value_id}  ; {name}")
            rstate.ops.append(RenderedOp(f"dispatch {name}", len(lines), 0))
            for case_ in d.cases:
                lines.append(f"        {case_.match:#06x} -> {case_.target_state}")
                rstate.ops.append(
                    RenderedOp(
                        f"{name} == {case_.match:#x} -> {case_.target_state}",
                        len(lines),
                        2,  # MOVI + BEQ
                    )
                )
            # default: one more line, then the default terminator's own cost
            match d.default.WhichOneof("kind"):
                case "halt":
                    verdict = "drop" if d.default.halt.drop else "accept"
                    lines.append(f"        default -> halt {verdict}")
                    rstate.ops.append(
                        RenderedOp(f"default -> halt {verdict}", len(lines), 1)
                    )
                case "goto":
                    target = d.default.goto.target_state
                    lines.append(f"        default -> goto {target}")
                    rstate.ops.append(
                        RenderedOp(f"default -> goto {target}", len(lines), 1)
                    )


# --- JSON API (called from web/src/lib/py.ts via pyodide.globals) -----------

import ast
import json
import traceback

from nanuk_ir.interp import interp
from nanuk_ir.lower import LowerError, to_asm
from nanuk_ir.validate import ValidationError, validate

_EDSL_FILENAME = "<edsl>"
_LAST_PROGRAM = None


def _err(kind: str, message: str, line: int | None = None) -> str:
    return json.dumps(
        {"ok": False, "error": {"kind": kind, "message": message, "line": line}}
    )


def _edsl_line(exc: BaseException) -> int | None:
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == _EDSL_FILENAME:
            return frame.lineno
    return None


def _edsl_ranges(source: str, state_names: set[str]) -> dict[str, tuple[int, int]]:
    """Line ranges of @p.state-decorated functions whose names are states."""
    ranges: dict[str, tuple[int, int]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name in state_names:
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            else:
                start = node.lineno
            ranges[node.name] = (start, node.end_lineno or node.lineno)
    return ranges


def _asm_ranges(asm_text: str, state_names: list[str]) -> dict[str, tuple[int, int]]:
    lines = asm_text.splitlines()
    starts = {
        line.rstrip(":"): i + 1 for i, line in enumerate(lines) if line.endswith(":")
    }
    ranges: dict[str, tuple[int, int]] = {}
    for name in state_names:
        start = starts[name]
        end = start
        for i in range(start, len(lines)):
            if lines[i].startswith("    "):
                end = i + 1
            else:
                break
        ranges[name] = (start, end)
    return ranges


def compile_source(source: str) -> str:
    global _LAST_PROGRAM
    try:
        code = compile(source, _EDSL_FILENAME, "exec")
    except SyntaxError as e:
        return _err("syntax", str(e.msg), e.lineno)
    namespace: dict = {}
    try:
        exec(code, namespace)
    except (ValidationError, LowerError) as e:
        return _err("compile", str(e), _edsl_line(e))
    except Exception as e:  # CompileError included: nanuk_lang may not be imported yet
        kind = "compile" if type(e).__name__ == "CompileError" else "runtime"
        return _err(kind, f"{type(e).__name__}: {e}", _edsl_line(e))
    build_map_ir = namespace.get("build_map_ir")
    if callable(build_map_ir):
        return _compile_map(source, build_map_ir)
    build_ir = namespace.get("build_ir")
    if not callable(build_ir):
        return _err(
            "no_build_ir",
            "the program must define a build_ir() (parser) or "
            "build_map_ir() (MAP) function",
        )
    try:
        program = build_ir()
        validate(program)
        asm_text = to_asm(program, check=False)
    except (ValidationError, LowerError) as e:
        return _err("compile", str(e), _edsl_line(e))
    except Exception as e:
        kind = "compile" if type(e).__name__ == "CompileError" else "runtime"
        return _err(kind, f"{type(e).__name__}: {e}", _edsl_line(e))

    rendered = render_ir(program)
    names = [st.name for st in program.states]
    edsl = _edsl_ranges(source, set(names))
    asm = _asm_ranges(asm_text, names)
    states = []
    for rstate in rendered.states:
        a_lo, _ = asm[rstate.name]
        cursor = a_lo + 1  # first instruction line after the label
        ops = []
        for op in rstate.ops:
            asm_lines = list(range(cursor, cursor + op.asm_count))
            cursor += op.asm_count
            ops.append(
                {"label": op.label, "ir_line": op.ir_line, "asm_lines": asm_lines}
            )
        states.append({
            "name": rstate.name,
            "edsl": list(edsl[rstate.name]) if rstate.name in edsl else None,
            "ir": list(rstate.ir_range),
            "asm": list(asm[rstate.name]),
            "ops": ops,
        })
    _LAST_PROGRAM = program
    globals()["_LAST_MAP_PROGRAM"] = None
    return json.dumps({
        "ok": True,
        "kind": "parser",
        "ir_text": rendered.text,
        "asm_text": asm_text,
        "states": states,
    })


def run_packet(packet_hex: str) -> str:
    cleaned = "".join(packet_hex.split())
    try:
        packet = bytes.fromhex(cleaned)
    except ValueError:
        return _err("bad_hex", "packet must be hex bytes (whitespace allowed)")

    if globals().get("_LAST_MAP_PROGRAM") is not None:
        return _run_map_packet(packet)

    if _LAST_PROGRAM is None:
        return _err("no_program", "compile a program first")
    result = interp(_LAST_PROGRAM, packet, check=False)
    return json.dumps({
        "ok": True,
        "kind": "parser",
        "result": {
            "verdict": result.verdict,
            "error": result.error,
            "payload_offset": result.payload_offset,
            "steps": result.steps,
            "hdr_present": result.hdr_present,
            "hdr_offset": result.hdr_offset,
            "smd": result.smd,
        },
    })


# --- MAP programs (M3): render, compile, composed run ------------------------

from nanuk_ir.interp_map import interp_map
from nanuk_ir.lower_map import to_map_asm
from nanuk_ir.validate_map import validate_map


def render_map_ir(program: ir.MapProgram) -> RenderedIr:
    """MAP sibling of render_ir; asm emission counts mirror lower_map."""
    lines: list[str] = []
    states: list[RenderedState] = []
    if program.tables:
        for t in program.tables:
            lines.append(
                f"table t{t.table_id} \"{t.debug_name}\" "
                f"key={t.key_width}b action={t.action_width}b"
            )
        lines.append("")
    for st in program.states:
        start_line = len(lines) + 1
        lines.append(f"{st.name}:")
        rstate = RenderedState(name=st.name, ir_range=(start_line, start_line))
        names: dict[int, str] = {}
        for op in st.ops:
            match op.WhichOneof("op"):
                case "load":
                    ld = op.load
                    name = ld.debug_name or f"v{ld.value_id}"
                    names[ld.value_id] = name
                    lines.append(
                        f"    v{ld.value_id} = load(hdr={ld.hdr_id}, "
                        f"off={ld.byte_offset:+d}, n={ld.nbytes})  ; {name}"
                    )
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "load_md":
                    md = op.load_md
                    name = md.debug_name or f"md{md.field}"
                    names[md.value_id] = name
                    lines.append(f"    v{md.value_id} = load_md({md.field})  ; {name}")
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "const":
                    c = op.const
                    name = c.debug_name or f"{c.imm:#x}"
                    names[c.value_id] = name
                    lines.append(f"    v{c.value_id} = {c.imm:#06x}")
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "add":
                    a = op.add
                    name = f"{_value_name(names, a.src_value_id)} + {a.imm}"
                    names[a.value_id] = name
                    lines.append(f"    v{a.value_id} = v{a.src_value_id} + {a.imm}")
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "store":
                    stq = op.store
                    name = stq.debug_name or _value_name(names, stq.value_id)
                    lines.append(
                        f"    store(hdr={stq.hdr_id}, off={stq.byte_offset:+d}, "
                        f"n={stq.nbytes}) = v{stq.value_id}  ; {name}"
                    )
                    rstate.ops.append(RenderedOp(f"store {name}", len(lines), 1))
                case "csum":
                    cs = op.csum
                    lines.append(
                        f"    csum_update(hdr={cs.hdr_id}, off={cs.byte_offset:+d})"
                    )
                    rstate.ops.append(RenderedOp("csum_update", len(lines), 1))
                case "lookup":
                    lk = op.lookup
                    key = _value_name(names, lk.key_value_id)
                    name = f"t{lk.table_id}[{key}]"
                    names[lk.value_id] = name
                    lines.append(
                        f"    v{lk.value_id} = lookup(t{lk.table_id}, v{lk.key_value_id}) "
                        f"miss -> {lk.miss_state}  ; {name}"
                    )
                    rstate.ops.append(RenderedOp(f"lookup {name}", len(lines), 1))
        _render_map_terminator(st.terminator, lines, rstate, names)
        rstate.ir_range = (start_line, len(lines))
        states.append(rstate)
        lines.append("")
    return RenderedIr(text="\n".join(lines).rstrip() + "\n", states=states)


def _render_map_terminator(
    term: ir.Terminator,
    lines: list[str],
    rstate: RenderedState,
    names: dict[int, str],
) -> None:
    match term.WhichOneof("kind"):
        case "send":
            s = term.send
            name = _value_name(names, s.bitmap_value_id)
            suffix = f", delta={s.delta:+d}" if s.delta else ""
            lines.append(f"    send v{s.bitmap_value_id}{suffix}  ; {name}")
            rstate.ops.append(RenderedOp(f"send {name}", len(lines), 1))
        case "drop":
            lines.append("    drop")
            rstate.ops.append(RenderedOp("drop", len(lines), 1))
        case "goto":
            lines.append(f"    goto {term.goto.target_state}")
            rstate.ops.append(
                RenderedOp(f"goto {term.goto.target_state}", len(lines), 1)
            )
        case "dispatch":
            d = term.dispatch
            name = _value_name(names, d.value_id)
            lines.append(f"    dispatch v{d.value_id}  ; {name}")
            rstate.ops.append(RenderedOp(f"dispatch {name}", len(lines), 0))
            for case_ in d.cases:
                lines.append(f"        {case_.match:#06x} -> {case_.target_state}")
                rstate.ops.append(
                    RenderedOp(
                        f"{name} == {case_.match:#x} -> {case_.target_state}",
                        len(lines),
                        2,
                    )
                )
            match d.default.WhichOneof("kind"):
                case "drop":
                    lines.append("        default -> drop")
                    rstate.ops.append(RenderedOp("default -> drop", len(lines), 1))
                case "goto":
                    target = d.default.goto.target_state
                    lines.append(f"        default -> goto {target}")
                    rstate.ops.append(
                        RenderedOp(f"default -> goto {target}", len(lines), 1)
                    )


class _Table:
    """Table-shaped (key_width/action_width/entries) without nanuk_spec."""

    def __init__(self, key_width: int, action_width: int, entries: dict):
        self.key_width = key_width
        self.action_width = action_width
        self.entries = entries


# Playground control plane: every declared 48-bit-key table knows the two
# demo MACs (matches the docs' examples); other widths start empty.
_DEMO_ENTRIES = {0xAABBCCDDEE01: 0x4, 0xAABBCCDDEE02: 0x8}
_LAST_MAP_PROGRAM = None
_PP_IR = None  # lazily built l2l3l4 parser IR for the composed MAP run


def _default_tables(program: ir.MapProgram) -> list:
    tables: list = []
    for t in program.tables:
        while len(tables) < t.table_id:
            tables.append(_Table(0, 0, {}))
        entries = dict(_DEMO_ENTRIES) if t.key_width == 48 else {}
        tables.append(_Table(t.key_width, t.action_width, entries))
    return tables


def _pp_context(packet: bytes):
    global _PP_IR
    if _PP_IR is None:
        from nanuk_lang.programs.l2l3l4 import make_parser

        _PP_IR = make_parser().build_ir()
    return interp(_PP_IR, packet, check=False)


def _compile_map(source: str, build_map_ir) -> str:
    global _LAST_PROGRAM, _LAST_MAP_PROGRAM
    try:
        program = build_map_ir()
        validate_map(program)
        asm_text = to_map_asm(program, check=False)
    except (ValidationError, LowerError) as e:
        return _err("compile", str(e), _edsl_line(e))
    except Exception as e:
        kind = "compile" if type(e).__name__ == "CompileError" else "runtime"
        return _err(kind, f"{type(e).__name__}: {e}", _edsl_line(e))

    rendered = render_map_ir(program)
    names = [st.name for st in program.states]
    edsl = _edsl_ranges(source, set(names))
    asm = _asm_ranges(asm_text, names)
    states = []
    for rstate in rendered.states:
        a_lo, _ = asm[rstate.name]
        cursor = a_lo + 1
        ops = []
        for op in rstate.ops:
            asm_lines = list(range(cursor, cursor + op.asm_count))
            cursor += op.asm_count
            ops.append(
                {"label": op.label, "ir_line": op.ir_line, "asm_lines": asm_lines}
            )
        states.append({
            "name": rstate.name,
            "edsl": list(edsl[rstate.name]) if rstate.name in edsl else None,
            "ir": list(rstate.ir_range),
            "asm": list(asm[rstate.name]),
            "ops": ops,
        })
    _LAST_MAP_PROGRAM = program
    _LAST_PROGRAM = None
    return json.dumps({
        "ok": True,
        "kind": "map",
        "ir_text": rendered.text,
        "asm_text": asm_text,
        "states": states,
    })


def _run_map_packet(packet: bytes) -> str:
    """Composed run: the baked l2l3l4 parser gates, then the MAP executes
    with the playground's demo table entries (ingress fixed at 0)."""
    program = globals()["_LAST_MAP_PROGRAM"]
    pp = _pp_context(packet)
    if pp.verdict != 0:
        return json.dumps({
            "ok": True,
            "kind": "map",
            "result": {
                "gated": True,
                "pp_verdict": pp.verdict,
                "pp_error": pp.error,
            },
        })
    r = interp_map(program, packet, pp, _default_tables(program), 0, check=False)
    return json.dumps({
        "ok": True,
        "kind": "map",
        "result": {
            "gated": False,
            "verdict": r.verdict,
            "error": r.error,
            "egress": r.egress,
            "delta": r.delta,
            "steps": r.steps,
            "frame": r.frame.hex() if r.frame is not None else None,
        },
    })
