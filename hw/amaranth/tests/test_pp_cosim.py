"""Differential cosimulation: ParserProcessor (pysim) vs the nanuk-emu golden
model, over the l2l3l4 demo corpus (the packets of
tests/golden/test_demo.py, rebuilt with scapy the same way) plus
seeded-random packets. The entire output contract is diffed field by field.

Requires the nanuk-emu binary (built in the linux devcontainer); gated on
NANUK_COSIM=1 so the module always imports cleanly.
"""

import os
import random
from pathlib import Path

import pytest

from nanuk.isa.asm import assemble
from nanuk.testkit.harness import run_program
from nanuk.testkit.testkit import l2l3l4_packets

from nanuk_amaranth.pp_sim_util import run_pp

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-emu (linux container)",
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_ASM = REPO_ROOT / "examples" / "l2l3l4" / "parse.asm"


@pytest.fixture(scope="module")
def prog() -> bytes:
    return assemble(DEMO_ASM.read_text())


def assert_contract_matches(name: str, golden, rtl):
    """Diff the ENTIRE output contract, field by field."""
    assert rtl.verdict == golden.verdict, f"{name}: verdict"
    assert rtl.error == golden.error, f"{name}: error"
    assert rtl.payload_offset == golden.payload_offset, f"{name}: payload_offset"
    assert rtl.steps == golden.steps, f"{name}: steps"
    for i in range(16):
        assert bool(rtl.hdr_present[i]) == bool(golden.hdr_present[i]), (
            f"{name}: hdr_present[{i}]"
        )
        assert rtl.hdr_offset[i] == golden.hdr_offset[i], f"{name}: hdr_offset[{i}]"
    for i in range(8):
        assert rtl.smd[i] == golden.smd[i], f"{name}: smd[{i}]"


def test_demo_corpus_cosim(prog):
    named = l2l3l4_packets()
    rtl_results = run_pp(prog, [pkt for _, pkt in named])
    for (name, pkt), rtl in zip(named, rtl_results):
        golden = run_program(prog, pkt)
        assert_contract_matches(name, golden, rtl)


def test_random_packets_cosim(prog):
    rng = random.Random(0x4E414E) # "NAN"
    packets = [rng.randbytes(rng.randint(0, 300)) for _ in range(20)]
    rtl_results = run_pp(prog, packets)
    for i, (pkt, rtl) in enumerate(zip(packets, rtl_results)):
        golden = run_program(prog, pkt)
        assert_contract_matches(f"random[{i}] len={len(pkt)}", golden, rtl)
