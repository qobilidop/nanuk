"""Bridge SIIT path: the baked PP twin + EAMT plane held identical to the
examples/testkit sources, the translate seed compiling to the MAP panes
(reg-reg ALU rendered), and the frozen five presets running composed through
the bridge to the reference's committed bytes."""

import json
from pathlib import Path

from nanuk.ir.map_lower import to_map_asm
from nanuk.testkit.load import load_example
from nanuk.testkit.testkit import siit_tables

import bridge

WEB = Path(__file__).resolve().parents[2]
REPO = WEB.parent
SIIT_SEED = (WEB / "src" / "programs" / "siit.py").read_text()
VECTORS = REPO / "benchmarks" / "siit" / "vectors"


def _compile_siit() -> dict:
    out = json.loads(bridge.compile_source(SIIT_SEED))
    assert out["ok"], out
    return out


def _vector(group: str, name: str) -> dict:
    vecs = json.loads((VECTORS / f"{group}.json").read_text())
    return next(v for v in vecs if v["name"] == name)


# --- tripwires: the bridge's inlined copies == the examples/testkit sources --


def test_pp_rig_mirrors_siit_parse_example():
    """The baked SIIT parser is a copy of examples/siit/parse.py; hold the two
    identical at the assembly level (the bridge must not import example
    content)."""
    assert bridge._make_siit_pp_parser().compile() == load_example("siit/parse.py").build()


def test_baked_tables_match_testkit():
    """The baked t0/t1/t2 EAMT constants equal testkit.siit_tables(DEMO_SIIT)
    (the wheels never ship testkit — the scapy boundary)."""
    ref = siit_tables()
    baked = bridge._siit_tables()
    assert len(baked) == len(ref) == 3
    for b, r in zip(baked, ref):
        assert (b.key_width, b.action_width, b.entries) == (
            r.key_width, r.action_width, r.entries
        )


def test_translate_seed_lowers_like_example():
    """The editor seed's MAP compiles to the same asm as
    examples/siit/translate.py (a standalone copy, not imported)."""
    ns: dict = {}
    exec(compile(SIIT_SEED, "<siit-seed>", "exec"), ns)
    seed_asm = to_map_asm(ns["build_map_ir"](), check=False)
    assert seed_asm == to_map_asm(load_example("siit/translate.py").build_ir(), check=False)


# --- compile: MAP kind, panes, and the reg-reg ALU rendered -----------------


def test_compile_siit_kind_and_alu_rendered():
    out = _compile_siit()
    assert out["kind"] == "map"
    names = [s["name"] for s in out["states"]]
    assert names[0] == "entry" and "refuse" in names
    # bin_op (reg-reg ALU) must render into the IR + asm panes (it didn't before
    # SIIT needed it): the pool6 prefix build uses an OR, patches use xor/add.
    assert " or " in out["ir_text"] and " xor " in out["ir_text"]
    asm_mnemonics = {ln.split()[0] for ln in out["asm_text"].splitlines() if ln.startswith("    ")}
    assert {"or", "xor", "sub"} <= asm_mnemonics
    # Every op still maps to exactly one asm line (partition invariant).
    for st in out["states"]:
        claimed = [ln for op in st["ops"] for ln in op["asm_lines"]]
        lo, hi = st["asm"]
        assert sorted(claimed) == list(range(lo + 1, hi + 1)), st["name"]


# --- composed run: the frozen five presets == the committed vectors ---------

PRESETS = [
    ("udp46", "udp46_len25_ttl64"),
    ("udp64", "udp64_len25_ttl64"),
    ("edge", "edge_eamt_dst_46"),
    ("icmp46", "icmp46_len25_ttl64"),
]


def test_sent_presets_reproduce_vector_bytes():
    _compile_siit()
    for group, name in PRESETS:
        vec = _vector(group, name)
        out = json.loads(bridge.run_packet(vec["in"]))
        assert out["ok"] and out["kind"] == "map", name
        r = out["result"]
        assert r["gated"] is False, name
        assert r["verdict"] == 0, (name, r)  # sent
        assert r["frame"] == vec["out"], name
        # the two engines agree, aligned end to end
        assert out["trace"]["pp"]["divergence"] is None, name
        assert out["trace"]["map"]["divergence"] is None, name
        assert out["trace"]["map"]["result_match"] is True, name


def test_ttl_expired_preset_drops():
    _compile_siit()
    vec = _vector("negative", "neg_v4_ttl_expired")
    out = json.loads(bridge.run_packet(vec["in"]))
    assert out["ok"] and out["kind"] == "map"
    r = out["result"]
    # PP accepts a well-formed v4/UDP frame; the MAP makes the value decision.
    assert r["gated"] is False
    assert r["verdict"] == 1  # drop
    assert r["frame"] is None
