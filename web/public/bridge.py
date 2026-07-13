"""Nanuk playground bridge: runs inside Pyodide (and under pytest).

Renders the IR pane text deterministically (so per-state line ranges are
known exactly), tracks per-op asm emission counts mirroring the v0
lowering, and exposes compile_source()/run_packet() as the JSON API
consumed by web/src/lib/py.ts. Line numbers are 1-based inclusive
everywhere."""

from dataclasses import dataclass, field

from nanuk.ir import nanuk_ir_pb2 as ir


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


def render_ir(program: ir.ParserProgram) -> RenderedIr:
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
                case "emit_md":
                    s = op.emit_md
                    name = _value_name(names, s.value_id)
                    lines.append(f"    md[{s.slot}] = v{s.value_id}  ; {name}")
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "load_md":
                    s = op.load_md
                    name = _value_name(names, s.value_id)
                    lines.append(f"    v{s.value_id} = md[{s.slot}]  ; {name}")
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

from nanuk.ir.pp_interp import pp_interp
from nanuk.ir.pp_lower import LowerError, to_pp_asm_annotated
from nanuk.ir.pp_validate import ValidationError, pp_validate
from nanuk.isa.pp_asm import assemble_with_lines
from nanuk.isa.pp_iss import run_pp_iss
from nanuk.isa.map_iss import run_map_iss
from nanuk.isa.map_asm import assemble_with_lines as map_assemble_with_lines

_EDSL_FILENAME = "<edsl>"
_LAST_PROGRAM = None
_LAST_ASM = None     # {"prog", "line_map", "bindings"} for the parser program
_LAST_STATES = None  # provenance dicts for the compiled program (either kind)
_LAST_MAP_ASM = None
_PP_RIG = None       # baked l2l3l4 rig for composed runs (built lazily)


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


def _provenance(rendered: RenderedIr, source: str, asm_text: str, program) -> list:
    """The cross-pane provenance records: per state, its eDSL/IR/asm line
    ranges; per op, its IR line and the asm lines it emitted (an ordered
    walk — op order and per-op emission counts mirror the lowering)."""
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
    return states


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
    except Exception as e:  # CompileError included: nanuk.lang may not be imported yet
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
        pp_validate(program)
        asm_text, bindings = to_pp_asm_annotated(program, check=False)
    except (ValidationError, LowerError) as e:
        return _err("compile", str(e), _edsl_line(e))
    except Exception as e:
        kind = "compile" if type(e).__name__ == "CompileError" else "runtime"
        return _err(kind, f"{type(e).__name__}: {e}", _edsl_line(e))

    rendered = render_ir(program)
    states = _provenance(rendered, source, asm_text, program)
    prog_bytes, line_map = assemble_with_lines(asm_text)
    _LAST_PROGRAM = program
    globals()["_LAST_ASM"] = {
        "prog": prog_bytes, "line_map": line_map, "bindings": bindings,
    }
    globals()["_LAST_STATES"] = states
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
    events: list = []
    result = pp_interp(_LAST_PROGRAM, packet, check=False, trace=events)
    iss_res = run_pp_iss(
        _LAST_ASM["prog"], packet, line_map=_LAST_ASM["line_map"]
    )
    trace = _build_trace(
        "parser", _LAST_PROGRAM, _LAST_STATES, events, iss_res,
        _LAST_ASM["bindings"],
    )
    _stamp_result_match(
        trace,
        (result.verdict, result.error, result.payload_offset, result.steps,
         result.hdr_present, result.hdr_offset, result.md),
        (iss_res.verdict, iss_res.error, iss_res.payload_offset, iss_res.steps,
         iss_res.hdr_present, iss_res.hdr_offset, iss_res.md),
        iss_res.steps,
    )
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
            "md": result.md,
        },
        "trace": trace,
    })


# --- MAP programs (M3): render, compile, composed run ------------------------

from nanuk.ir.map_interp import map_interp
from nanuk.ir.map_lower import to_map_asm_annotated
from nanuk.ir.map_validate import map_validate


def render_map_ir(program: ir.MatchActionProgram) -> RenderedIr:
    """MAP sibling of render_ir; asm emission counts mirror map_lower."""
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
                    name = md.debug_name or f"md{md.slot}"
                    names[md.value_id] = name
                    lines.append(f"    v{md.value_id} = md[{md.slot}]  ; {name}")
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
                    name = f"csum(hdr={cs.hdr_id})"
                    names[cs.value_id] = name
                    lines.append(
                        f"    v{cs.value_id} = csum(hdr={cs.hdr_id}, "
                        f"off={cs.byte_offset:+d}, len=v{cs.len_value_id})"
                    )
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "store_md":
                    sm = op.store_md
                    name = _value_name(names, sm.value_id)
                    lines.append(
                        f"    md[{sm.slot}] = v{sm.value_id}  ; {name}"
                        if sm.nunits == 1 else
                        f"    md[{sm.slot}..{sm.slot + sm.nunits - 1}] = "
                        f"v{sm.value_id}  ; {name}"
                    )
                    rstate.ops.append(RenderedOp(f"md[{sm.slot}] = {name}", len(lines), 1))
                case "and_imm":
                    ai = op.and_imm
                    name = f"{_value_name(names, ai.src_value_id)} & {ai.imm:#x}"
                    names[ai.value_id] = name
                    lines.append(f"    v{ai.value_id} = v{ai.src_value_id} & {ai.imm:#06x}")
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
                case "shift":
                    sh = op.shift
                    name = f"{_value_name(names, sh.src_value_id)} << {sh.amount}"
                    names[sh.value_id] = name
                    lines.append(f"    v{sh.value_id} = v{sh.src_value_id} << {sh.amount}")
                    rstate.ops.append(RenderedOp(name, len(lines), 1))
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
            suffix = f" delta={s.delta:+d}" if s.delta else ""
            lines.append(f"    send{suffix}")
            rstate.ops.append(RenderedOp(f"send{suffix}", len(lines), 1))
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
    """Table-shaped (key_width/action_width/entries) without nanuk.testkit."""

    def __init__(self, key_width: int, action_width: int, entries: dict):
        self.key_width = key_width
        self.action_width = action_width
        self.entries = entries


# Playground control plane: every declared 48-bit-key table knows the two
# demo MACs (matches the docs' examples); every declared 16-bit-key table
# gets the system flood entries ({ingress -> flood bitmap}, the
# nanuk_switch convention at t3); other widths start empty.
_DEMO_ENTRIES = {0xAABBCCDDEE01: 0x4, 0xAABBCCDDEE02: 0x8}
_FLOOD_ENTRIES = {i: (0xF & ~(1 << i)) for i in range(4)}
_LAST_MAP_PROGRAM = None


def _default_tables(program: ir.MatchActionProgram) -> list:
    tables: list = []
    for t in program.tables:
        while len(tables) < t.table_id:
            tables.append(_Table(0, 0, {}))
        if t.key_width == 48:
            entries = dict(_DEMO_ENTRIES)
        elif t.key_width == 16:
            entries = dict(_FLOOD_ENTRIES)
        else:
            entries = {}
        tables.append(_Table(t.key_width, t.action_width, entries))
    return tables


def _make_pp_parser():
    """The baked composed-run parser: eth -> 802.1Q (incl. QinQ) -> IPv4
    (with options) -> UDP. A copy of examples/l2l3l4/parse.py (the bridge is
    playground toolchain and must not import example content); the composed
    bridge tests pin its behavior against the example's corpus."""
    from nanuk.lang import Header, Parser

    eth = Header("eth", dst=48, src=48, ethertype=16)
    vlan = Header("vlan", tci=16, ethertype=16)
    ipv4 = Header(
        "ipv4",
        version=4, ihl=4, tos=8, total_len=16, ident=16,
        flags_frag=16, ttl=8, proto=8, csum=16, src=32, dst=32,
    )
    udp = Header("udp", sport=16, dport=16, length=16, csum=16)
    ETY_VLAN, ETY_IPV4, PROTO_UDP = 0x8100, 0x0800, 17

    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(eth, hdr_id=0)
        s.smd(s.extract(eth.dst), slot=1)  # slot 0 is the system's
        ety = s.extract(eth.ethertype)
        s.advance(eth.byte_len)
        s.dispatch(ety, {ETY_VLAN: vlan_tag, ETY_IPV4: ipv4_check},
                   default=s.accept)

    @p.state()
    def vlan_tag(s):
        s.mark(vlan, hdr_id=1)
        s.smd(s.extract(vlan.tci), slot=4)
        ety = s.extract(vlan.ethertype)
        s.advance(vlan.byte_len)
        s.dispatch(ety, {ETY_VLAN: vlan_tag, ETY_IPV4: ipv4_check},
                   default=s.accept)

    @p.state()
    def ipv4_check(s):
        s.mark(ipv4, hdr_id=2)
        version = s.extract(ipv4.version)
        s.dispatch(version, {4: ipv4_body}, default=s.drop)

    @p.state()
    def ipv4_body(s):
        s.mark(ipv4)
        ihl = s.extract(ipv4.ihl)
        proto = s.extract(ipv4.proto)
        s.advance(ihl << 2)
        s.dispatch(proto, {PROTO_UDP: udp_hdr}, default=s.accept)

    @p.state()
    def udp_hdr(s):
        s.mark(udp, hdr_id=3)
        s.smd(s.extract(udp.dport), slot=5)
        s.advance(udp.byte_len)
        s.accept()

    return p


def _pp_rig():
    """The baked l2l3l4 parser, assembled and provenance-rendered once
    (composed MAP runs trace the PP phase too; its panes aren't shown, so
    provenance is built against an empty source)."""
    global _PP_RIG
    if _PP_RIG is None:
        program = _make_pp_parser().build_ir()
        asm_text, bindings = to_pp_asm_annotated(program, check=False)
        prog_bytes, line_map = assemble_with_lines(asm_text)
        states = _provenance(render_ir(program), "", asm_text, program)
        _PP_RIG = {
            "ir": program, "prog": prog_bytes, "line_map": line_map,
            "bindings": bindings, "states": states,
        }
    return _PP_RIG


def _pp_context(packet: bytes):
    return pp_interp(_pp_rig()["ir"], packet, check=False)


def _compile_map(source: str, build_map_ir) -> str:
    global _LAST_PROGRAM, _LAST_MAP_PROGRAM
    try:
        program = build_map_ir()
        map_validate(program)
        asm_text, bindings = to_map_asm_annotated(program, check=False)
    except (ValidationError, LowerError) as e:
        return _err("compile", str(e), _edsl_line(e))
    except Exception as e:
        kind = "compile" if type(e).__name__ == "CompileError" else "runtime"
        return _err(kind, f"{type(e).__name__}: {e}", _edsl_line(e))

    rendered = render_map_ir(program)
    states = _provenance(rendered, source, asm_text, program)
    prog_bytes, line_map = map_assemble_with_lines(asm_text)
    _LAST_MAP_PROGRAM = program
    _LAST_PROGRAM = None
    globals()["_LAST_MAP_ASM"] = {
        "prog": prog_bytes, "line_map": line_map, "bindings": bindings,
    }
    globals()["_LAST_STATES"] = states
    return json.dumps({
        "ok": True,
        "kind": "map",
        "ir_text": rendered.text,
        "asm_text": asm_text,
        "states": states,
    })


def _run_map_packet(packet: bytes) -> str:
    """Composed run: the baked l2l3l4 parser gates, then the MAP executes
    with the playground's demo table entries (ingress fixed at 0 in md
    slot 0, the nanuk_switch convention). Both phases are traced (the PP
    phase against the baked rig's provenance)."""
    program = globals()["_LAST_MAP_PROGRAM"]
    md_in = [0] * 8  # slot 0 = ingress port 0
    rig = _pp_rig()
    pp_events: list = []
    pp = pp_interp(rig["ir"], packet, md_in, check=False, trace=pp_events)
    pp_iss = run_pp_iss(rig["prog"], packet, md_in, line_map=rig["line_map"])
    pp_trace = _build_trace(
        "parser", rig["ir"], rig["states"], pp_events, pp_iss, rig["bindings"]
    )
    _stamp_result_match(
        pp_trace,
        (pp.verdict, pp.error, pp.payload_offset, pp.steps,
         pp.hdr_present, pp.hdr_offset, pp.md),
        (pp_iss.verdict, pp_iss.error, pp_iss.payload_offset, pp_iss.steps,
         pp_iss.hdr_present, pp_iss.hdr_offset, pp_iss.md),
        pp_iss.steps,
    )
    if pp.verdict != 0:
        return json.dumps({
            "ok": True,
            "kind": "map",
            "result": {
                "gated": True,
                "pp_verdict": pp.verdict,
                "pp_error": pp.error,
            },
            "trace": {"pp": pp_trace, "map": None},
        })
    tables = _default_tables(program)
    events: list = []
    r = map_interp(program, packet, pp, tables, pp.md, check=False, trace=events)
    map_asm = globals()["_LAST_MAP_ASM"]
    iss_res = run_map_iss(
        map_asm["prog"], packet, pp, tables, pp.md, line_map=map_asm["line_map"]
    )
    map_trace = _build_trace(
        "map", program, _LAST_STATES, events, iss_res, map_asm["bindings"]
    )
    _stamp_result_match(
        map_trace,
        (r.verdict, r.error, tuple(r.md), r.delta, r.steps, r.frame),
        (iss_res.verdict, iss_res.error, tuple(iss_res.md), iss_res.delta,
         iss_res.steps, iss_res.frame),
        iss_res.steps,
    )
    return json.dumps({
        "ok": True,
        "kind": "map",
        "result": {
            "gated": False,
            "verdict": r.verdict,
            "error": r.error,
            "md": list(r.md),
            "egress": r.md[0],
            "delta": r.delta,
            "steps": r.steps,
            "frame": r.frame.hex() if r.frame is not None else None,
        },
        "trace": {"pp": pp_trace, "map": map_trace},
    })


# --- Two-level trace assembly (v2 debugger) ----------------------------------


def _hex(v: int) -> str:
    return f"{v:#x}"


def _walk_index(state_msg, ev) -> int:
    """Map an pp_interp TraceEvent to its rendered-op index within the state.

    The rendered walk is: one op per state op (including zero-emission
    re-anchor marks), then for a dispatch: header, one per case, default;
    for halt/goto/send/drop: a single terminator op."""
    n = len(state_msg.ops)
    if ev.kind == "op":
        return ev.index
    term = state_msg.terminator
    if term.WhichOneof("kind") == "dispatch":
        if ev.kind == "term_case":
            return n + 1 + ev.index
        return n + 1 + len(term.dispatch.cases)  # term_default
    return n  # bare term on halt/goto/send/drop


def _build_trace(kind, program, prov_states, events, iss_res, bindings) -> dict:
    """Per-machine-step records joining the ISS trace with the covering
    pp_interp event (the step counter is the shared clock), plus the
    architectural divergence verdict."""
    prov_by_name = {s["name"]: s for s in prov_states}
    states_by_name = {st.name: st for st in program.states}
    trace = iss_res.trace
    records: list[dict] = []
    divergence = None

    def step_record(s: int, info: dict) -> dict:
        rec = trace[s]
        out = {
            "step": s,
            "pc": rec.pc,
            "asm_line": rec.line,
            "regs": [_hex(v) for v in rec.regs],
            "reg_names": bindings[rec.pc] if rec.pc < len(bindings) else {},
            "state": info["state"],
            "ir_line": info["ir_line"],
            "op_label": info["op_label"],
            "values": info["values"],
            "cursor": rec.cursor if kind == "parser" else None,
        }
        if kind == "map":
            out["writes"] = [[addr, data.hex()] for addr, data in rec.writes]
            out["lookup"] = (
                None if rec.lookup is None
                else [rec.lookup[0], _hex(rec.lookup[1]), rec.lookup[2],
                      _hex(rec.lookup[3])]
            )
        return out

    info = {
        "state": program.states[0].name, "ir_line": None,
        "op_label": "", "values": {},
    }
    prev = 0
    for ev in events:
        pstate = prov_by_name.get(ev.state)
        ridx = _walk_index(states_by_name[ev.state], ev)
        rop = (
            pstate["ops"][ridx]
            if pstate is not None and ridx < len(pstate["ops"])
            else None
        )
        label = rop["label"] if rop else ""
        values = {}
        if ev.values:
            values[label or "value"] = _hex(next(iter(ev.values.values())))
        info = {
            "state": ev.state,
            "ir_line": rop["ir_line"] if rop else None,
            "op_label": label,
            "values": values,
        }
        upto = min(ev.steps_after, len(trace))
        for s in range(prev, upto):
            records.append(step_record(s, info))
        if divergence is None and 0 < ev.steps_after <= len(trace):
            divergence = _diverged(kind, ev, trace, prev, ev.steps_after)
        prev = upto
    for s in range(prev, len(trace)):  # error tails past the last event
        records.append(step_record(s, info))
    return {"steps": iss_res.steps, "records": records, "divergence": divergence}


def _diverged(kind, ev, trace, lo, hi):
    """Architectural comparison at an event boundary; None when agreeing."""
    if kind == "parser":
        rec = trace[hi - 1]
        for what, a, b in (
            ("cursor", ev.cursor, rec.cursor),
            ("hdr_present", ev.hdr_present, rec.hdr_present),
            ("hdr_offset", ev.hdr_offset, rec.hdr_offset),
            ("md", ev.md, rec.md),
        ):
            if a != b:
                return {"step": hi - 1, "what": what}
        return None
    got_writes = tuple(w for r in trace[lo:hi] for w in r.writes)
    if got_writes != (ev.writes or ()):
        return {"step": hi - 1, "what": "window writes"}
    if ev.lookup is not None and not any(r.lookup == ev.lookup for r in trace[lo:hi]):
        return {"step": hi - 1, "what": "lookup"}
    return None


def _stamp_result_match(trace: dict, interp_fields, iss_fields, last_step: int) -> None:
    trace["result_match"] = interp_fields == iss_fields
    if trace["divergence"] is None and not trace["result_match"]:
        trace["divergence"] = {"step": max(0, last_step - 1), "what": "final result"}
