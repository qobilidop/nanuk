"""The v2 trace API: per-step aligned records, reg annotations, and the
divergence verdict, for parser runs and both phases of composed MAP runs."""

import json
from pathlib import Path

import bridge

PROGRAMS = Path(__file__).resolve().parents[2] / "src" / "programs"
L2L3L4_SRC = (PROGRAMS / "l2l3l4.py").read_text()
MAP_SRC = (PROGRAMS / "map_l2fwd.py").read_text()

QINQ = (
    "aabbccddee01" "020000000002"
    "8100" "00c8" "8100" "012c" "0800"
    "45000021000100004011" "0000" "0a0000010a000002"
    "003500350000" "0000" "68690000"
)
KNOWN = (
    "aabbccddee01" "020000000002" "0800"
    "45000021000100004011" "0000" "0a0000010a000002"
    "003500350000" "0000" "68690000"
)
RUNT = "aabb"  # header violation -> the parser gates the pipeline


def run(source: str, packet_hex: str) -> dict:
    out = json.loads(bridge.compile_source(source))
    assert out["ok"], out
    run_out = json.loads(bridge.run_packet(packet_hex))
    assert run_out["ok"], run_out
    return run_out


def test_parser_trace_shape_and_agreement():
    out = run(L2L3L4_SRC, QINQ)
    compiled = json.loads(bridge.compile_source(L2L3L4_SRC))
    asm_lines = compiled["asm_text"].splitlines()
    trace = out["trace"]
    assert trace["steps"] == out["result"]["steps"]
    assert len(trace["records"]) == trace["steps"]
    assert trace["divergence"] is None
    assert trace["result_match"] is True
    first = trace["records"][0]
    assert first["step"] == 0 and first["pc"] == 0
    assert first["state"] == compiled["states"][0]["name"]
    for rec in trace["records"]:
        assert asm_lines[rec["asm_line"] - 1].startswith("    ")
        assert len(rec["regs"]) == 4
        for r in rec["regs"]:
            int(r, 16)
        assert rec["cursor"] is not None
    # reg annotations name live values, never the scratch register
    assert any(rec["reg_names"] for rec in trace["records"])
    assert all("r3" not in rec["reg_names"] for rec in trace["records"])
    # ir lines point into the rendered IR text
    n_ir = len(compiled["ir_text"].splitlines())
    assert all(
        rec["ir_line"] is None or 1 <= rec["ir_line"] <= n_ir
        for rec in trace["records"]
    )


def test_parser_trace_error_run_still_traces():
    out = run(L2L3L4_SRC, "aabb")  # runt: header violation
    assert out["result"]["verdict"] == 2
    trace = out["trace"]
    assert trace["steps"] == out["result"]["steps"] == len(trace["records"])
    assert trace["divergence"] is None and trace["result_match"] is True


def test_map_composed_trace_phases():
    out = run(MAP_SRC, KNOWN)
    assert out["result"]["gated"] is False
    pp, mp = out["trace"]["pp"], out["trace"]["map"]
    for phase in (pp, mp):
        assert phase["divergence"] is None and phase["result_match"] is True
        assert len(phase["records"]) == phase["steps"]
    lookups = [r["lookup"] for r in mp["records"] if r["lookup"]]
    assert lookups and lookups[0][2] is True  # FDB hit for the known MAC
    assert mp["records"][-1]["op_label"].startswith("send")


def test_map_gated_trace_has_pp_only():
    out = run(MAP_SRC, RUNT)
    assert out["result"]["gated"] is True
    assert out["trace"]["map"] is None
    assert out["trace"]["pp"]["steps"] > 0


def test_budget_exhaustion_traces_256():
    src = """\
from nanuk_lang import Header, Parser

eth = Header("eth", dst=48, src=48, ethertype=16)

def make_parser():
    p = Parser()

    @p.state(start=True)
    def spin(s):
        s.mark(eth, hdr_id=0)
        s.extract(eth.dst)
        s.goto(spin)

    return p

def build_ir():
    return make_parser().build_ir()
"""
    out = run(src, KNOWN)
    assert out["result"]["error"] == 2
    trace = out["trace"]
    assert trace["steps"] == 256 and len(trace["records"]) == 256
    assert trace["result_match"] is True
