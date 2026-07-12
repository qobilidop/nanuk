"""The flagship level-diff, proven honest: ISS (assembled words) vs
interp (IR) are step-exact on every demo program — same result fields,
same step counts, and the alignment invariant holds: at every interp
event boundary the architectural state matches the ISS trace at that
step index (parser: cursor/hdr/SMD; MAP: window writes and lookups).

Pure Python (interp + ISS), no emulator: ungated.
"""

import importlib.util
import random
from pathlib import Path

import pytest

from nanuk_ir.interp import interp
from nanuk_ir.interp_map import interp_map
from nanuk_ir.lower import to_asm
from nanuk_ir.lower_map import to_map_asm
from nanuk_isa.asm import assemble_with_lines
from nanuk_isa.iss import run_iss
from nanuk_isa.iss_map import run_map_iss
from nanuk_isa.map_asm import assemble_with_lines as map_assemble_with_lines
from nanuk_lang.programs.l2l3l4 import build_ir as l2l3l4_ir
from nanuk_lang.programs.map_demos import (
    make_l2fwd,
    make_ttl,
    make_tunnel_pop,
    make_tunnel_push,
)
from nanuk_spec.testkit import (
    NO_TABLE,
    demo_l2_table,
    demo_tun_table,
    l2l3l4_packets,
    map_packets,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "nanukproto_parse", REPO_ROOT / "examples" / "nanukproto" / "parse.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
nanukproto_ir = _mod.build_ir

PARSER_PROGRAMS = {"l2l3l4": l2l3l4_ir, "nanukproto": nanukproto_ir}

MAP_DEMOS = {
    "l2fwd": (make_l2fwd, [demo_l2_table()]),
    "ttl": (make_ttl, [demo_l2_table()]),
    "push": (make_tunnel_push, [NO_TABLE, demo_tun_table()]),
    "pop": (make_tunnel_pop, []),
}


def random_packets(seed: int, n: int = 40) -> list[tuple[str, bytes]]:
    rng = random.Random(seed)
    return [
        (f"rand{i}", bytes(rng.randrange(256) for _ in range(rng.randrange(0, 300))))
        for i in range(n)
    ]


def check_parser(program, pkt: bytes, label: str) -> None:
    events = []
    ri = interp(program, pkt, trace=events)
    binary, lines = assemble_with_lines(to_asm(program))
    rs = run_iss(binary, pkt, line_map=lines)
    assert (
        ri.verdict, ri.error, ri.payload_offset, ri.steps,
        ri.hdr_present, ri.hdr_offset, ri.smd,
    ) == (
        rs.verdict, rs.error, rs.payload_offset, rs.steps,
        rs.hdr_present, rs.hdr_offset, rs.smd,
    ), label
    assert ri.steps == rs.steps == len(rs.trace), label
    for ev in events:
        step = rs.trace[ev.steps_after - 1]
        assert (ev.cursor, ev.hdr_present, ev.hdr_offset, ev.smd) == (
            step.cursor, step.hdr_present, step.hdr_offset, step.smd,
        ), (label, ev.state, ev.kind, ev.index)


@pytest.mark.parametrize("name", PARSER_PROGRAMS)
def test_parser_iss_interp_parity(name):
    program = PARSER_PROGRAMS[name]()
    for pname, pkt in l2l3l4_packets() + random_packets(0x4E414E):
        check_parser(program, pkt, f"{name}/{pname}")


def check_map(program, pkt: bytes, pp, tables, ingress: int, label: str) -> None:
    events = []
    ri = interp_map(program, pkt, pp, tables, ingress, trace=events)
    binary, lines = map_assemble_with_lines(to_map_asm(program))
    rs = run_map_iss(binary, pkt, pp, tables, ingress, line_map=lines)
    assert (ri.verdict, ri.error, ri.egress, ri.delta, ri.steps, ri.frame) == (
        rs.verdict, rs.error, rs.egress, rs.delta, rs.steps, rs.frame,
    ), label
    assert ri.steps == rs.steps == len(rs.trace), label
    prev = 0
    for ev in events:
        span = rs.trace[prev : ev.steps_after]
        got_writes = tuple(w for s in span for w in s.writes)
        assert got_writes == (ev.writes or ()), (label, ev.state, ev.kind)
        if ev.lookup is not None:
            assert any(s.lookup == ev.lookup for s in span), (label, ev.state)
        prev = ev.steps_after


def pp_for(pkt: bytes, parser):
    return interp(parser, pkt, check=False)


@pytest.mark.parametrize("name", MAP_DEMOS)
def test_map_iss_interp_parity(name):
    make, tables = MAP_DEMOS[name]
    program = make().build_ir()
    pp_parser = nanukproto_ir() if name == "pop" else l2l3l4_ir()
    packets = map_packets() + random_packets(0x4E4150, 20)
    if name == "pop":
        packets = packets + [("tunnel_frame", _tunnel_frame())]
    checked = 0
    for pname, pkt in packets:
        pp = pp_for(pkt, pp_parser)
        if pp.verdict != 0:
            continue  # the pipeline gates non-accepted parses
        for ingress in (0, 2):
            check_map(program, pkt, pp, tables, ingress, f"{name}/{pname}/in{ingress}")
            checked += 1
    assert checked > 0


def _tunnel_frame() -> bytes:
    inner = map_packets()[0][1]
    pp = pp_for(inner, l2l3l4_ir())
    assert pp.verdict == 0
    pushed = interp_map(
        make_tunnel_push().build_ir(), inner, pp,
        [NO_TABLE, demo_tun_table()], 0,
    )
    assert pushed.verdict == 0 and pushed.frame is not None
    return pushed.frame
