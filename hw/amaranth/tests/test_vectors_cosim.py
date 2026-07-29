"""RTL vs the committed conformance vectors (spec/vectors/).

The vectors carry the golden expectations in-file, so this leg needs no
emulator binary and runs ungated — the RTL answers to the same committed
truth as every other implementation. The full-contract random/corpus
differential legs stay in test_pp_cosim.py / test_map_cosim.py.
"""

from functools import cache

import pytest

from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.isa.pp_asm import assemble as pp_assemble
from nanuk.testkit.vectors import VECTORS_DIR, load_all

from nanuk_amaranth.map_sim_util import run_pipeline_rtl
from nanuk_amaranth.pp_sim_util import run_pp_one

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
def test_rtl_matches_vectors(f, v):
    if f.suite == "pp":
        got = run_pp_one(pp_prog(f.programs["pp"]), v.frame, v.md_in)
        assert pp_fields(got) == pp_expected(v.pp)
        return
    pp, mp = run_pipeline_rtl(
        pp_prog(f.programs["pp"]),
        map_prog(f.programs["map"]),
        v.frame,
        f.tables,
        v.md_in,
    )
    assert pp_fields(pp) == pp_expected(v.pp)
    if v.map is None:
        assert mp is None
    else:
        assert mp is not None and map_fields(mp) == map_expected(v.map)
