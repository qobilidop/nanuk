"""MAP ISS vs the golden model: every demo program over the shared MAP
corpus (composed with the l2l3l4 parser for the inbound contract), plus
random frames and a random-words decoder fuzz. All six MatchActionResult fields
compared, including the transmitted frame.
"""

import random
import struct
from pathlib import Path

import pytest

from nanuk.isa.pp_asm import assemble as pp_assemble
from nanuk.isa.map_iss import run_map_iss
from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.testkit.pp_harness import VERDICT_ACCEPT, run_program
from nanuk.testkit.map_harness import Table, run_map
from nanuk.testkit.testkit import NO_TABLE, demo_flood_table, demo_l2_table, demo_tun_table, map_packets

REPO_ROOT = Path(__file__).resolve().parents[4]
PP_ASM = REPO_ROOT / "examples" / "l2l3l4" / "parse.asm"

_FLOOD = demo_flood_table()

DEMOS = {
    "map_l2fwd": (
        REPO_ROOT / "examples" / "map_l2fwd" / "fwd.asm",
        [demo_l2_table(both=True), NO_TABLE, NO_TABLE, _FLOOD],
    ),
    "map_ttl": (
        REPO_ROOT / "examples" / "map_ttl" / "fwd.asm",
        [demo_l2_table(), NO_TABLE, NO_TABLE, _FLOOD],
    ),
    "tunnel_push": (
        REPO_ROOT / "examples" / "nanukproto" / "tunnel_push.asm",
        [demo_l2_table(), demo_tun_table(), NO_TABLE, _FLOOD],
    ),
    "tunnel_pop": (
        REPO_ROOT / "examples" / "nanukproto" / "tunnel_pop.asm",
        [demo_l2_table(both=True), NO_TABLE, NO_TABLE, _FLOOD],
    ),
}

MD_IN = [0, 0, 0, 0, 0, 0, 0, 0]  # ingress port 0 in slot 0, by convention


def fields(r):
    return (r.verdict, r.error, tuple(r.md), r.delta, r.steps, r.frame)


@pytest.fixture(scope="module")
def pp_prog() -> bytes:
    return pp_assemble(PP_ASM.read_text())


@pytest.mark.parametrize("demo", DEMOS)
def test_map_iss_matches_emulator_on_corpus(demo, pp_prog):
    asm_path, tables = DEMOS[demo]
    map_prog = map_assemble(asm_path.read_text())
    for name, pkt in map_packets():
        pp = run_program(pp_prog, pkt, MD_IN)
        if pp.verdict != VERDICT_ACCEPT:
            continue  # the pipeline gates; the MAP never sees these
        want = run_map(map_prog, pkt, pp, tables, pp.md)
        got = run_map_iss(map_prog, pkt, pp, tables, pp.md)
        assert fields(got) == fields(want), (demo, name)


def test_map_iss_matches_emulator_random_frames(pp_prog):
    rng = random.Random(0x4E414E)
    asm_path, tables = DEMOS["map_l2fwd"]
    map_prog = map_assemble(asm_path.read_text())
    checked = 0
    for _ in range(80):
        pkt = bytes(rng.randrange(256) for _ in range(rng.randrange(14, 300)))
        pp = run_program(pp_prog, pkt, MD_IN)
        if pp.verdict != VERDICT_ACCEPT:
            continue
        want = run_map(map_prog, pkt, pp, tables, pp.md)
        got = run_map_iss(map_prog, pkt, pp, tables, pp.md)
        assert fields(got) == fields(want)
        checked += 1
    assert checked > 5  # the corpus generator must not gate everything away


def test_map_iss_matches_emulator_random_words(pp_prog):
    # Decoder fuzz against the golden decode's totality; fixed accepted
    # parse supplies the inbound contract.
    rng = random.Random(0x4E4150)
    pkt = map_packets()[0][1]
    pp = run_program(pp_prog, pkt, MD_IN)
    assert pp.verdict == VERDICT_ACCEPT
    tables = [Table(key_width=8, action_width=8, entries={0x01: 0x2})]
    for i in range(40):
        words = [rng.randrange(1 << 32) for _ in range(8)]
        prog = b"".join(struct.pack(">I", w) for w in words)
        want = run_map(prog, pkt, pp, tables, pp.md)
        got = run_map_iss(prog, pkt, pp, tables, pp.md)
        assert fields(got) == fields(want), i


def test_map_iss_matches_emulator_new_instructions(pp_prog):
    # Directed coverage for the redesign's new/changed instructions:
    # stmd/ldmd round-trip, andi/shli/csum recompute, bare send, and the
    # illegal edges (ldmd slot 8, stmd overflow, old register SEND word).
    from nanuk.isa.map_asm import assemble

    pkt = map_packets()[0][1]
    pp = run_program(pp_prog, pkt, MD_IN)
    assert pp.verdict == VERDICT_ACCEPT
    programs = [
        "    ldmd r0, 0\n    stmd r0, 1, 5\n    send 0\n",
        (
            "    ld r2, 2, 0, 1\n    andi r2, r2, 0x000F\n    shli r2, r2, 2\n"
            "    st rz, 2, 10, 2\n    csum r3, 2, 0, r2\n    st r3, 2, 10, 2\n"
            "    stmd r3, 1, 1\n    send 0\n"
        ),
        "    csum r0, h_frame, 0, rz\n    stmd r0, 1, 2\n    send 0\n",
        "    ldmd r0, 8\n    drop\n",
        "    stmd r0, 4, 6\n    drop\n",
        "    send -4\n",
    ]
    for i, src in enumerate(programs):
        prog = assemble(src)
        want = run_map(prog, pkt, pp, [], pp.md)
        got = run_map_iss(prog, pkt, pp, [], pp.md)
        assert fields(got) == fields(want), i
    # Old register-coded SEND word (rs = r1): illegal everywhere.
    prog = struct.pack(">I", 0x2C82C000)
    want = run_map(prog, pkt, pp, [], pp.md)
    got = run_map_iss(prog, pkt, pp, [], pp.md)
    assert fields(got) == fields(want)
