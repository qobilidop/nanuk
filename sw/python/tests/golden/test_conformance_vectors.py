"""The conformance-vector legs for the two Python-reachable implementations.

The committed vectors at spec/vectors/ are the executable spec. The
emulator leg doubles as the regeneration tripwire (spec/vectors/gen.py must
be a no-op against the committed files); the ISS leg holds the fourth
implementation to the same files. The RTL leg lives in
hw/amaranth/tests/test_vectors_cosim.py.
"""

from functools import cache

import pytest

from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.isa.map_iss import run_map_iss
from nanuk.isa.pp_asm import assemble as pp_assemble
from nanuk.isa.pp_iss import run_pp_iss
from nanuk.testkit.map_harness import run_pipeline
from nanuk.testkit.pp_harness import VERDICT_ACCEPT, run_program
from nanuk.testkit.vectors import VECTORS_DIR, load_all

REPO_ROOT = VECTORS_DIR.parents[1]

FILES = load_all()
CASES = [(f, v) for f in FILES for v in f.vectors]
IDS = [f"{f.path.parent.name}-{f.path.stem}-{v.name}" for f, v in CASES]


@cache
def pp_prog(rel: str) -> bytes:
    return pp_assemble((REPO_ROOT / rel).read_text())


@cache
def map_prog(rel: str) -> bytes:
    return map_assemble((REPO_ROOT / rel).read_text())


def pp_fields(r):
    return (
        r.verdict,
        r.error,
        r.payload_offset,
        r.steps,
        list(r.hdr_present),
        list(r.hdr_offset),
        list(r.md),
    )


def pp_expected(e):
    return (
        e.verdict,
        e.error,
        e.payload_offset,
        e.steps,
        list(e.hdr_present),
        list(e.hdr_offset),
        list(e.md),
    )


def map_fields(r):
    return (r.verdict, r.error, list(r.md), r.delta, r.steps, r.frame)


def map_expected(e):
    return (e.verdict, e.error, list(e.md), e.delta, e.steps, e.frame)


@pytest.mark.parametrize("f,v", CASES, ids=IDS)
def test_emulator_matches_vectors(f, v):
    if f.suite == "pp":
        got = run_program(pp_prog(f.programs["pp"]), v.frame, v.md_in)
        assert pp_fields(got) == pp_expected(v.pp)
        return
    pp, mp = run_pipeline(
        pp_prog(f.programs["pp"]), map_prog(f.programs["map"]), v.frame, f.tables, v.md_in
    )
    assert pp_fields(pp) == pp_expected(v.pp)
    if v.map is None:
        assert mp is None
    else:
        assert mp is not None and map_fields(mp) == map_expected(v.map)


@pytest.mark.parametrize("f,v", CASES, ids=IDS)
def test_iss_matches_vectors(f, v):
    pp = run_pp_iss(pp_prog(f.programs["pp"]), v.frame, v.md_in)
    assert pp_fields(pp) == pp_expected(v.pp)
    if f.suite == "pp":
        return
    if pp.verdict != VERDICT_ACCEPT:
        assert v.map is None
        return
    mp = run_map_iss(map_prog(f.programs["map"]), v.frame, pp, f.tables, pp.md)
    assert v.map is not None and map_fields(mp) == map_expected(v.map)
