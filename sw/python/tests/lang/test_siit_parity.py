"""SIIT translator eDSL twins (examples/siit/{parse,translate}.py): parity at
every semantic level, over all committed conformance vectors.

Legs:
  (a) the twin program pair, lowered and assembled, is behaviorally identical to
      the hand-written parse.asm / translate.asm through the golden emulators —
      verdict / error / md / frame (schedules may differ, so not steps);
  (b) pp_interp / map_interp on the twins' IR agree with the emulator running the
      twins' own lowering (map_interp mirrors the cost model, so steps too);
  (c) the ISS (run_pp_iss / run_map_iss) on the assembled words agrees with the
      emulator on ALL fields including steps, with frame bytes identical (same
      words, so this is the honest level-diff);
  (d) a pure-Python ISS <-> interp cross-check (ungated), the flagship level-diff
      without an emulator;
  (e) PP symbolic execution: every witness reproduces its prediction on interp
      AND emulator, all parser states are reachable, and the witness corpus
      reaches every verdict the parser can produce (program coverage, PP only —
      MAP symex stays parked).

Emulator legs are gated on NANUK_COSIM=1; the ISS<->interp leg is pure Python.
"""

import os
from pathlib import Path

import pytest

from nanuk.ir.map_interp import map_interp
from nanuk.ir.map_lower import to_map_asm
from nanuk.ir.pp_interp import (
    ERR_HDR_VIOLATION,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
    pp_interp,
)
from nanuk.ir.pp_lower import to_pp_asm
from nanuk.ir.pp_symex import reachable_states, symex
from nanuk.isa import map_asm, pp_asm
from nanuk.isa.map_iss import run_map_iss
from nanuk.isa.pp_iss import run_pp_iss
from nanuk.testkit import map_harness
from nanuk.testkit.load import load_example
from nanuk.testkit.pp_harness import run_program
from nanuk.testkit.siit_ref import load_vectors
from nanuk.testkit.testkit import siit_tables

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLES = REPO_ROOT / "examples"

_parse = load_example("siit/parse.py")
_translate = load_example("siit/translate.py")

PP_IR = _parse.build_ir()
MAP_IR = _translate.build_ir()
TWIN_PP = pp_asm.assemble(_parse.build())
TWIN_MAP = map_asm.assemble(_translate.build())
HAND_PP = pp_asm.assemble((EXAMPLES / "siit" / "parse.asm").read_text())
HAND_MAP = map_asm.assemble((EXAMPLES / "siit" / "translate.asm").read_text())

TABLES = siit_tables()
VECTORS = load_vectors()
IDS = [v["name"] for v in VECTORS]
SEED = (0,) * 8

cosim = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-pp-emu + nanuk-map-emu (linux container)",
)

MAP_FIELDS = ("verdict", "error", "md", "delta", "frame")
PP_FIELDS = ("verdict", "error", "payload_offset", "hdr_present", "hdr_offset", "md")


def _accepted_vectors():
    """Vectors the twin PP accepts (so the MAP runs): sent + value-decision
    drops. Structural drops (non-IP, IHL<5, truncation) short-circuit at the PP
    and never reach the MAP."""
    out = []
    for vec in VECTORS:
        pkt = bytes.fromhex(vec["in"])
        if pp_interp(PP_IR, pkt).verdict == VERDICT_ACCEPT:
            out.append(vec)
    return out


ACCEPTED = _accepted_vectors()
ACCEPTED_IDS = [v["name"] for v in ACCEPTED]


# -- leg (a): twin pipeline == hand pipeline (behavior, not steps) -----------
@cosim
@pytest.mark.parametrize("vec", VECTORS, ids=IDS)
def test_twin_pipeline_matches_hand(vec):
    pkt = bytes.fromhex(vec["in"])
    tp, tr = map_harness.run_pipeline(TWIN_PP, TWIN_MAP, pkt, TABLES, SEED)
    hp, hr = map_harness.run_pipeline(HAND_PP, HAND_MAP, pkt, TABLES, SEED)
    assert (tp.verdict, tp.error) == (hp.verdict, hp.error), "PP verdict/error"
    if hr is None:
        assert tr is None, "twin MAP ran where hand short-circuited at the PP"
        return
    for f in ("verdict", "error", "md", "frame"):
        assert getattr(tr, f) == getattr(hr, f), f"{vec['name']}: MAP {f}"
    # And the sent frames are the reference's committed bytes.
    if vec["verdict"] == "sent":
        assert tr.frame == bytes.fromhex(vec["out"])


# -- leg (b): interp on twin IR == emulator on twin asm ----------------------
@cosim
@pytest.mark.parametrize("vec", VECTORS, ids=IDS)
def test_pp_interp_matches_emulator(vec):
    pkt = bytes.fromhex(vec["in"])
    ri = pp_interp(PP_IR, pkt)
    re = run_program(pp_asm.assemble(to_pp_asm(PP_IR)), pkt)
    for f in ("verdict", "error", "payload_offset", "steps",
              "hdr_present", "hdr_offset", "md"):
        assert getattr(ri, f) == getattr(re, f), f"{vec['name']}: PP {f}"


@cosim
@pytest.mark.parametrize("vec", ACCEPTED, ids=ACCEPTED_IDS)
def test_map_interp_matches_emulator(vec):
    pkt = bytes.fromhex(vec["in"])
    pp = run_program(TWIN_PP, pkt)
    binary = map_asm.assemble(to_map_asm(MAP_IR))
    g = map_harness.run_map(binary, pkt, pp, TABLES, pp.md)
    i = map_interp(MAP_IR, pkt, pp, TABLES, pp.md)
    for f in ("verdict", "error", "md", "delta", "steps", "frame"):
        gv, iv = getattr(g, f), getattr(i, f)
        if f == "md":
            gv, iv = tuple(gv), tuple(iv)
        assert gv == iv, f"{vec['name']}: MAP {f} emu={gv} interp={iv}"


# -- leg (c): ISS == emulator on the SAME words (all fields, incl steps) -----
@cosim
@pytest.mark.parametrize("vec", VECTORS, ids=IDS)
def test_pp_iss_matches_emulator(vec):
    pkt = bytes.fromhex(vec["in"])
    binary, lines = pp_asm.assemble_with_lines(_parse.build())
    rs = run_pp_iss(binary, pkt, line_map=lines)
    re = run_program(binary, pkt)
    for f in ("verdict", "error", "payload_offset", "steps",
              "hdr_present", "hdr_offset", "md"):
        assert getattr(rs, f) == getattr(re, f), f"{vec['name']}: PP {f}"


@cosim
@pytest.mark.parametrize("vec", ACCEPTED, ids=ACCEPTED_IDS)
def test_map_iss_matches_emulator(vec):
    pkt = bytes.fromhex(vec["in"])
    pp = run_program(TWIN_PP, pkt)
    binary, lines = map_asm.assemble_with_lines(_translate.build())
    rs = run_map_iss(binary, pkt, pp, TABLES, pp.md, line_map=lines)
    re = map_harness.run_map(binary, pkt, pp, TABLES, pp.md)
    for f in ("verdict", "error", "md", "delta", "steps", "frame"):
        assert getattr(rs, f) == getattr(re, f), f"{vec['name']}: MAP {f}"


# -- leg (d): ISS <-> interp, pure Python (ungated) --------------------------
@pytest.mark.parametrize("vec", VECTORS, ids=IDS)
def test_pp_iss_interp_agree(vec):
    pkt = bytes.fromhex(vec["in"])
    ri = pp_interp(PP_IR, pkt)
    binary, lines = pp_asm.assemble_with_lines(to_pp_asm(PP_IR))
    rs = run_pp_iss(binary, pkt, line_map=lines)
    assert ri.steps == rs.steps == len(rs.trace), vec["name"]
    for f in ("verdict", "error", "payload_offset", "steps",
              "hdr_present", "hdr_offset", "md"):
        assert getattr(ri, f) == getattr(rs, f), f"{vec['name']}: PP {f}"


@pytest.mark.parametrize("vec", ACCEPTED, ids=ACCEPTED_IDS)
def test_map_iss_interp_agree(vec):
    pkt = bytes.fromhex(vec["in"])
    pp = pp_interp(PP_IR, pkt, check=False)
    binary, lines = map_asm.assemble_with_lines(to_map_asm(MAP_IR))
    rs = run_map_iss(binary, pkt, pp, TABLES, pp.md, line_map=lines)
    ri = map_interp(MAP_IR, pkt, pp, TABLES, pp.md)
    assert ri.steps == rs.steps == len(rs.trace), vec["name"]
    for f in ("verdict", "error", "delta", "steps", "frame"):
        assert getattr(ri, f) == getattr(rs, f), f"{vec['name']}: MAP {f}"
    assert tuple(ri.md) == tuple(rs.md), f"{vec['name']}: MAP md"


# -- leg (e): PP symbolic execution — witnesses + program coverage -----------
@cosim
def test_pp_symex_witnesses_and_program_coverage():
    paths = symex(PP_IR)
    assert len(paths) >= 8, "parser fan-out should yield many feasible paths"
    verdicts: set[tuple[int, int]] = set()
    for p in paths:
        assert p.witness is not None
        golden = run_program(TWIN_PP, p.witness)
        it = pp_interp(PP_IR, p.witness)
        assert (golden.verdict, golden.error, golden.steps) == (
            p.verdict, p.error, p.steps
        ), f"golden diverged on {p.trace}"
        assert (it.verdict, it.error, it.steps) == (p.verdict, p.error, p.steps), (
            f"interp diverged on {p.trace}"
        )
        verdicts.add((p.verdict, p.error))

    # Every defined parser state is on some feasible path (no dead code).
    assert reachable_states(PP_IR) == {st.name for st in PP_IR.states}

    # Program coverage: the corpus reaches every verdict this PP can produce —
    # accept, halt-drop (non-IP EtherType / IHL < 5), and the header-violation
    # error the EXT/ADVANCE truncation windows raise.
    assert {v for v, _ in verdicts} == {VERDICT_ACCEPT, VERDICT_DROP, VERDICT_ERROR}
    assert (VERDICT_ERROR, ERR_HDR_VIOLATION) in verdicts
