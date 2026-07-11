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
