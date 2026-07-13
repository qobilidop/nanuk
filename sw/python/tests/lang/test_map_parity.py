"""M3 parity gate: the eDSL demo MAP programs are behaviorally identical to
the hand-written examples/*.asm through the golden emulator (full MatchActionResult
diff except `steps` — instruction schedules may differ), and map_interp
agrees with each eDSL program's own lowering on ALL fields including steps.

Gated on NANUK_COSIM=1 (needs both emulator binaries)."""

import os
from pathlib import Path

import pytest
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

from nanuk.ir.map_interp import map_interp
from nanuk.testkit.load import load_example
make_l2fwd = load_example("map_l2fwd/fwd.py").make_l2fwd
make_ttl = load_example("map_ttl/fwd.py").make_ttl
_ex = load_example("nanukproto/tunnel.py"); make_tunnel_pop, make_tunnel_push = _ex.make_tunnel_pop, _ex.make_tunnel_push
from nanuk.isa.pp_asm import assemble as pp_assemble
from nanuk.testkit.pp_harness import VERDICT_ACCEPT, run_program
from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.testkit.map_harness import Table, run_map
from nanuk.testkit.testkit import (
    DMAC,
    NO_TABLE,
    demo_flood_table,
    demo_l2_table,
    demo_tun_table,
    map_packets,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-pp-emu + nanuk-map-emu (linux container)",
)

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLES = REPO_ROOT / "examples"

L2_TABLE = demo_l2_table()
TUN_TABLE = demo_tun_table()
FLOOD_TABLE = demo_flood_table()

DEMOS = {
    "l2fwd": (make_l2fwd, "map_l2fwd/fwd.asm",
              [L2_TABLE, NO_TABLE, NO_TABLE, FLOOD_TABLE], "l2l3l4/parse.asm"),
    "ttl": (make_ttl, "map_ttl/fwd.asm",
            [L2_TABLE, NO_TABLE, NO_TABLE, FLOOD_TABLE], "l2l3l4/parse.asm"),
    "push": (
        make_tunnel_push,
        "nanukproto/tunnel_push.asm",
        [NO_TABLE, TUN_TABLE, NO_TABLE, FLOOD_TABLE],
        "l2l3l4/parse.asm",
    ),
    "pop": (make_tunnel_pop, "nanukproto/tunnel_pop.asm",
            [NO_TABLE, NO_TABLE, NO_TABLE, FLOOD_TABLE],
            "nanukproto/parse_tunnel.asm"),
}


def tunnel_frames() -> list[tuple[str, bytes]]:
    inner = bytes(Ether(dst=DMAC) / IP(dst="10.1.0.9", ttl=17) / UDP(dport=4242))
    pp = run_program(pp_assemble((EXAMPLES / "l2l3l4" / "parse.asm").read_text()), inner)
    pushed = run_map(
        map_assemble((EXAMPLES / "nanukproto" / "tunnel_push.asm").read_text()),
        inner, pp, [NO_TABLE, TUN_TABLE, NO_TABLE, FLOOD_TABLE], pp.md,
    )
    assert pushed.sent and pushed.delta == 22
    return [("tunnel_frame", pushed.frame), ("plain_frame", inner)]


@pytest.mark.parametrize("name", DEMOS)
def test_edsl_matches_hand_asm(name):
    make, hand_path, tables, pp_path = DEMOS[name]
    pp_prog = pp_assemble((EXAMPLES / pp_path).read_text())
    hand = map_assemble((EXAMPLES / hand_path).read_text())
    edsl = map_assemble(make().compile())
    packets = map_packets() if name != "pop" else map_packets() + tunnel_frames()
    compared = 0
    for pname, pkt in packets:
        for ingress in (0, 2):
            pp = run_program(pp_prog, pkt, [ingress])
            if pp.verdict != VERDICT_ACCEPT:
                continue
            g = run_map(hand, pkt, pp, tables, pp.md)
            e = run_map(edsl, pkt, pp, tables, pp.md)
            for field in ("verdict", "error", "md", "delta", "frame"):
                assert getattr(g, field) == getattr(e, field), (
                    f"{name}/{pname}/in{ingress}: {field} "
                    f"hand={getattr(g, field)} edsl={getattr(e, field)}"
                )
            compared += 1
    assert compared > 0


@pytest.mark.parametrize("name", DEMOS)
def test_interp_map_matches_own_lowering(name):
    make, _, tables, pp_path = DEMOS[name]
    mp = make()
    program = mp.build_ir()
    binary = map_assemble(mp.compile())
    pp_prog = pp_assemble((EXAMPLES / pp_path).read_text())
    packets = map_packets() if name != "pop" else map_packets() + tunnel_frames()
    for pname, pkt in packets:
        pp = run_program(pp_prog, pkt, [1])
        if pp.verdict != VERDICT_ACCEPT:
            continue
        g = run_map(binary, pkt, pp, tables, pp.md)
        i = map_interp(program, pkt, pp, tables, pp.md)
        for field in ("verdict", "error", "md", "delta", "steps", "frame"):
            g_v, i_v = getattr(g, field), getattr(i, field)
            if field == "md":
                g_v, i_v = tuple(g_v), tuple(i_v)
            assert g_v == i_v, (
                f"{name}/{pname}: {field} emu={g_v} map_interp={i_v}"
            )


# --------------------------------------------------------------------------
# E3 (calc) — the benchmark that changed the ISA. Its own parity gate, because
# it needs its own parser and its own packets: the calculator speaks a custom
# protocol, and every other demo here speaks Ethernet/IPv4.
# --------------------------------------------------------------------------

import struct  # noqa: E402

make_calc = load_example("calc/calc.py").make_calc

C_DST = b"\xaa\xbb\xcc\xdd\xee\xff"
C_SRC = b"\x00\x11\x22\x33\x44\x55"
REFLECT = Table(key_width=16, action_width=16, entries={p: 1 << p for p in range(8)})
CALC_TABLES = [REFLECT, NO_TABLE, NO_TABLE, NO_TABLE]

CALC_CASES = [
    (b"+", 7, 5, 12),
    (b"+", 0xFFFFFFFF, 1, 0),
    (b"-", 100, 58, 42),
    (b"-", 0, 1, 0xFFFFFFFF),
    (b"&", 0xF0F0F0F0, 0xFF00FF00, 0xF000F000),
    (b"|", 0xF0F0F0F0, 0x0F0F0F0F, 0xFFFFFFFF),
    (b"^", 0xDEADBEEF, 0xFFFFFFFF, 0x21524110),
    (b"*", 6, 7, None),  # unimplemented: must drop
]


def calc_frame(op: bytes, a: int, b: int) -> bytes:
    body = b"P4" + bytes([1]) + op + struct.pack("!III", a, b, 0)
    return C_DST + C_SRC + struct.pack("!H", 0x1234) + body


def test_calc_edsl_matches_hand_asm():
    pp_prog = pp_assemble((EXAMPLES / "calc" / "parse.asm").read_text())
    hand = map_assemble((EXAMPLES / "calc" / "calc.asm").read_text())
    edsl = map_assemble(make_calc().compile())
    for op, a, b, want in CALC_CASES:
        pkt = calc_frame(op, a, b)
        pp = run_program(pp_prog, pkt, [2])
        assert pp.verdict == VERDICT_ACCEPT
        g = run_map(hand, pkt, pp, CALC_TABLES, pp.md)
        e = run_map(edsl, pkt, pp, CALC_TABLES, pp.md)
        for field in ("verdict", "error", "md", "delta", "frame"):
            assert getattr(g, field) == getattr(e, field), (
                f"calc {op!r}: {field} hand={getattr(g, field)} edsl={getattr(e, field)}"
            )
        if want is not None:
            got = struct.unpack("!I", g.frame[14 + 12 : 14 + 16])[0]
            assert got == want, f"{a} {op.decode()} {b} = {got}, want {want}"


def test_calc_interp_agrees_with_its_own_lowering():
    """map_interp mirrors the lowering's cost model instruction for instruction,
    so ALL fields compare -- including steps."""
    pp_prog = pp_assemble((EXAMPLES / "calc" / "parse.asm").read_text())
    mp = make_calc()
    lowered = map_assemble(mp.compile())
    prog_ir = mp.build_ir()
    for op, a, b, _want in CALC_CASES:
        pkt = calc_frame(op, a, b)
        pp = run_program(pp_prog, pkt, [2])
        emu = run_map(lowered, pkt, pp, CALC_TABLES, pp.md)
        interp = map_interp(prog_ir, pkt, pp, CALC_TABLES, pp.md)
        for field in ("verdict", "error", "md", "delta", "frame", "steps"):
            assert getattr(emu, field) == getattr(interp, field), (
                f"calc {op!r}: {field} emu={getattr(emu, field)} "
                f"interp={getattr(interp, field)}"
            )
