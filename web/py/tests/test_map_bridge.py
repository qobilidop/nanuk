"""Bridge MAP path: compile_map detection, provenance ranges, and the
composed parser->MAP run with the demo control plane."""

import json
from pathlib import Path

import bridge

MAP_SRC = (
    Path(__file__).resolve().parents[2] / "src" / "programs" / "map_l2fwd.py"
).read_text()

# A plain IPv4/UDP packet to aa:bb:cc:dd:ee:01 (playground FDB -> port 2).
KNOWN = (
    "aabbccddee01" "020000000002" "0800"
    "45000021000100004011" "0000" "0a0000010a000002"
    "003500350000" "0000" "68690000"
)
UNKNOWN = "020000000099" + KNOWN[12:]
RUNT = "aabb"


def compile_map():
    return json.loads(bridge.compile_source(MAP_SRC))


def test_compile_map_kind_and_panes():
    out = compile_map()
    assert out["ok"] and out["kind"] == "map"
    assert "lookup(t0, v1) miss -> flood" in out["ir_text"]
    assert "lookup  r0, 0, r0, flood" in out["asm_text"]
    names = [s["name"] for s in out["states"]]
    assert names == ["forward", "flood"]
    fw = out["states"][0]
    assert fw["edsl"] is not None
    # Every op maps to exactly one asm line here (no dispatch in this state).
    for op in fw["ops"]:
        assert len(op["asm_lines"]) == 1


def test_run_known_dmac_unicast():
    compile_map()
    out = json.loads(bridge.run_packet(KNOWN))
    assert out["ok"] and out["kind"] == "map"
    r = out["result"]
    assert r["gated"] is False
    assert r["verdict"] == 0
    assert r["egress"] == 0x4
    assert r["delta"] == 0
    assert r["frame"] == KNOWN


def test_run_unknown_dmac_floods():
    compile_map()
    out = json.loads(bridge.run_packet(UNKNOWN))
    r = out["result"]
    assert r["verdict"] == 0
    assert r["egress"] == 0xE  # ingress fixed at 0


def test_run_gated_by_parser():
    compile_map()
    out = json.loads(bridge.run_packet(RUNT))
    r = out["result"]
    assert r["gated"] is True
    assert r["pp_verdict"] == 2  # runt: header violation


def test_parser_programs_still_work_after_map():
    compile_map()
    l2l3l4 = (
        Path(__file__).resolve().parents[2] / "src" / "programs" / "l2l3l4.py"
    ).read_text()
    out = json.loads(bridge.compile_source(l2l3l4))
    assert out["ok"] and out["kind"] == "parser"
    run = json.loads(bridge.run_packet(KNOWN))
    assert run["kind"] == "parser"
    assert run["result"]["verdict"] == 0
