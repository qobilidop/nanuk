"""MAP ISS vs the golden model: every demo program over the shared MAP
corpus (composed with the l2l3l4 parser for the inbound contract), plus
random frames and a random-words decoder fuzz. All six MapResult fields
compared, including the transmitted frame.
"""

import random
import struct
from pathlib import Path

import pytest

from nanuk.isa.asm import assemble as pp_assemble
from nanuk.isa.iss_map import run_map_iss
from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.testkit.harness import VERDICT_ACCEPT, run_program
from nanuk.testkit.map_harness import Table, run_map
from nanuk.testkit.testkit import demo_l2_table, demo_tun_table, map_packets

REPO_ROOT = Path(__file__).resolve().parents[4]
PP_ASM = REPO_ROOT / "examples" / "l2l3l4" / "parse.asm"

DEMOS = {
    "map_l2fwd": (REPO_ROOT / "examples" / "map_l2fwd" / "fwd.asm", [demo_l2_table(both=True)]),
    "map_ttl": (REPO_ROOT / "examples" / "map_ttl" / "fwd.asm", [demo_l2_table()]),
    "tunnel_push": (
        REPO_ROOT / "examples" / "nanukproto" / "tunnel_push.asm",
        [demo_l2_table(), demo_tun_table()],
    ),
    "tunnel_pop": (
        REPO_ROOT / "examples" / "nanukproto" / "tunnel_pop.asm",
        [demo_l2_table(both=True)],
    ),
}

INGRESS = 0


def fields(r):
    return (r.verdict, r.error, r.egress, r.delta, r.steps, r.frame)


@pytest.fixture(scope="module")
def pp_prog() -> bytes:
    return pp_assemble(PP_ASM.read_text())


@pytest.mark.parametrize("demo", DEMOS)
def test_map_iss_matches_emulator_on_corpus(demo, pp_prog):
    asm_path, tables = DEMOS[demo]
    map_prog = map_assemble(asm_path.read_text())
    for name, pkt in map_packets():
        pp = run_program(pp_prog, pkt)
        if pp.verdict != VERDICT_ACCEPT:
            continue  # the pipeline gates; the MAP never sees these
        want = run_map(map_prog, pkt, pp, tables, INGRESS)
        got = run_map_iss(map_prog, pkt, pp, tables, INGRESS)
        assert fields(got) == fields(want), (demo, name)


def test_map_iss_matches_emulator_random_frames(pp_prog):
    rng = random.Random(0x4E414E)
    asm_path, tables = DEMOS["map_l2fwd"]
    map_prog = map_assemble(asm_path.read_text())
    checked = 0
    for _ in range(80):
        pkt = bytes(rng.randrange(256) for _ in range(rng.randrange(14, 300)))
        pp = run_program(pp_prog, pkt)
        if pp.verdict != VERDICT_ACCEPT:
            continue
        want = run_map(map_prog, pkt, pp, tables, INGRESS)
        got = run_map_iss(map_prog, pkt, pp, tables, INGRESS)
        assert fields(got) == fields(want)
        checked += 1
    assert checked > 5  # the corpus generator must not gate everything away


def test_map_iss_matches_emulator_random_words(pp_prog):
    # Decoder fuzz against the golden decode's totality; fixed accepted
    # parse supplies the inbound contract.
    rng = random.Random(0x4E4150)
    pkt = map_packets()[0][1]
    pp = run_program(pp_prog, pkt)
    assert pp.verdict == VERDICT_ACCEPT
    tables = [Table(key_width=8, action_width=8, entries={0x01: 0x2})]
    for i in range(40):
        words = [rng.randrange(1 << 32) for _ in range(8)]
        prog = b"".join(struct.pack(">I", w) for w in words)
        want = run_map(prog, pkt, pp, tables, INGRESS)
        got = run_map_iss(prog, pkt, pp, tables, INGRESS)
        assert fields(got) == fields(want), i
