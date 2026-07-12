"""ISS vs the golden model (parser): the tripwire for the fourth
implementation. Runs the l2l3l4 demo over the shared corpus, random
packets, and the nanukproto tunnel program; a random-words leg fuzzes
the decoder against the golden decode (illegal/reserved paths).
"""

import random
import struct
from pathlib import Path

import pytest

from nanuk.isa.asm import assemble
from nanuk.isa.iss import run_iss
from tests.support.harness import run_program
from tests.support.testkit import l2l3l4_packets

REPO_ROOT = Path(__file__).resolve().parents[3]
PROGRAMS = {
    "l2l3l4": REPO_ROOT / "python" / "nanuk" / "examples" / "l2l3l4" / "parse.asm",
    "nanukproto": REPO_ROOT / "python" / "nanuk" / "examples" / "nanukproto" / "parse_tunnel.asm",
}


def fields(r):
    return (
        r.verdict,
        r.error,
        r.payload_offset,
        r.steps,
        list(r.hdr_present),
        list(r.hdr_offset),
        list(r.smd),
    )


@pytest.mark.parametrize("prog_name", PROGRAMS)
def test_iss_matches_emulator_on_corpus(prog_name):
    binary = assemble(PROGRAMS[prog_name].read_text())
    for name, pkt in l2l3l4_packets():
        assert fields(run_iss(binary, pkt)) == fields(run_program(binary, pkt)), (
            prog_name,
            name,
        )


def test_iss_matches_emulator_random_packets():
    rng = random.Random(0x4E414E)
    binary = assemble(PROGRAMS["l2l3l4"].read_text())
    for i in range(60):
        pkt = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 300)))
        assert fields(run_iss(binary, pkt)) == fields(run_program(binary, pkt)), i


def test_iss_matches_emulator_random_words():
    # Decoder fuzz: random words exercise illegal/reserved paths against
    # the golden decode's totality.
    rng = random.Random(0x4E414F)
    for i in range(40):
        words = [rng.randrange(1 << 32) for _ in range(8)]
        prog = b"".join(struct.pack(">I", w) for w in words)
        pkt = bytes(rng.randrange(256) for _ in range(64))
        assert fields(run_iss(prog, pkt)) == fields(run_program(prog, pkt)), i
