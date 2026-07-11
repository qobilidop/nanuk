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
    build_ir = namespace.get("build_ir")
    if not callable(build_ir):
        return _err("no_build_ir", "the program must define a build_ir() function")
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
    return json.dumps({
        "ok": True,
        "ir_text": rendered.text,
        "asm_text": asm_text,
        "states": states,
    })


def run_packet(packet_hex: str) -> str:
    if _LAST_PROGRAM is None:
        return _err("no_program", "compile a program first")
    cleaned = "".join(packet_hex.split())
    try:
        packet = bytes.fromhex(cleaned)
    except ValueError:
        return _err("bad_hex", "packet must be hex bytes (whitespace allowed)")
    result = interp(_LAST_PROGRAM, packet, check=False)
    return json.dumps({
        "ok": True,
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
