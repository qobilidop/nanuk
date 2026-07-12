"""Symbolic-executor differential validation over the real nanuk-lang
programs: every witness reproduces its exact prediction on pp_interp AND the
golden emulator, all states are reachable, and — the headline payoff —
symex INVENTS a valid nanukproto tunnel packet from constraints alone.

Gated on NANUK_COSIM=1 (needs nanuk-emu)."""

import os
from pathlib import Path

import pytest

from nanuk.ir.pp_interp import pp_interp
from nanuk.ir.pp_symex import reachable_states, symex
from nanuk.testkit.load import load_example
make_parser = load_example("l2l3l4/parse.py").make_parser
from nanuk.isa.pp_asm import assemble
from nanuk.testkit.pp_harness import run_program

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-emu (linux container)",
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_witnesses_reproduce_on_golden_model():
    parser = make_parser()
    prog = parser.build_ir()
    binary = assemble(parser.compile())
    paths = symex(prog)
    assert len(paths) >= 8  # eth x {vlan loop, ipv4, options...} fan-out
    for p in paths:
        assert p.witness is not None
        golden = run_program(binary, p.witness)
        it = pp_interp(prog, p.witness)
        assert (golden.verdict, golden.error, golden.steps) == (
            p.verdict, p.error, p.steps,
        ), f"golden diverged on {p.trace}"
        assert (it.verdict, it.error, it.steps) == (p.verdict, p.error, p.steps)
    assert reachable_states(prog) == {st.name for st in prog.states}


def test_symex_invents_a_valid_tunnel_packet():
    """From constraints alone, symex produces a packet the golden model
    recognizes as a well-formed nanukproto tunnel frame."""
    _ex = load_example("nanukproto/parse.py"); H_NK, make_nk_parser = _ex.H_NK, _ex.make_parser

    parser = make_nk_parser()
    prog = parser.build_ir()
    binary = assemble(parser.compile())

    nk_paths = [
        p for p in symex(prog)
        if p.verdict == 0 and any("nk_body" in s for s in p.trace)
    ]
    assert nk_paths, "no accepting path through the tunnel states"
    for p in nk_paths[:3]:
        golden = run_program(binary, p.witness)
        assert golden.verdict == 0
        assert golden.hdr(H_NK) is not None, (
            "the invented packet must carry a recognized nanukproto header"
        )
        off = golden.hdr(H_NK)
        assert p.witness[off : off + 2] == b"\x4e\x4b"
