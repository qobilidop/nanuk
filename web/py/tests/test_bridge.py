"""compile_source/run_packet: the JSON API the SPA consumes."""

import json

from nanuk.examples.l2l3l4 import parse as l2l3l4  # noqa: F401  (env sanity)

import bridge

GOOD = """\
from nanuk.lang import Header, Parser

eth = Header("eth", dst=48, src=48, ethertype=16)

def make_parser():
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(eth, hdr_id=0)
        s.smd(s.extract(eth.dst), slot=0)
        s.advance(eth.byte_len)
        s.accept()

    return p

def build_ir():
    return make_parser().build_ir()
"""


def compile_ok(source: str) -> dict:
    out = json.loads(bridge.compile_source(source))
    assert out["ok"], out
    return out


def test_compile_good_source():
    out = compile_ok(GOOD)
    assert "start:" in out["ir_text"]
    assert "start:" in out["asm_text"]
    (state,) = out["states"]
    assert state["name"] == "start"
    # eDSL range covers the decorated function (def line through body)
    lo, hi = state["edsl"]
    assert GOOD.splitlines()[lo - 1].strip().startswith("@p.state")
    assert lo < hi
    # asm range starts at the label line
    assert out["asm_text"].splitlines()[state["asm"][0] - 1] == "start:"
    # ordered-walk op mapping: every op's asm lines are inside the state range
    for op in state["ops"]:
        for ln in op["asm_lines"]:
            assert state["asm"][0] < ln <= state["asm"][1]


def test_ops_partition_the_asm_block():
    out = compile_ok(GOOD)
    (state,) = out["states"]
    claimed = [ln for op in state["ops"] for ln in op["asm_lines"]]
    lo, hi = state["asm"]
    assert sorted(claimed) == list(range(lo + 1, hi + 1))  # label line excluded


def test_syntax_error_reports_line():
    out = json.loads(bridge.compile_source("def broken(:\n"))
    assert not out["ok"]
    assert out["error"]["kind"] == "syntax"
    assert out["error"]["line"] == 1


def test_missing_build_ir():
    out = json.loads(bridge.compile_source("x = 1\n"))
    assert not out["ok"]
    assert out["error"]["kind"] == "no_build_ir"


def test_user_exception_reports_edsl_line():
    src = "raise RuntimeError('boom')\n\ndef build_ir():\n    pass\n"
    out = json.loads(bridge.compile_source(src))
    assert not out["ok"]
    assert out["error"]["kind"] == "runtime"
    assert out["error"]["line"] == 1
    assert "boom" in out["error"]["message"]


def test_compile_error_kind():
    src = (
        "from nanuk.lang import Header\n"
        "h = Header('bad', x=3)\n"  # 3 bits: not whole bytes -> CompileError
        "def build_ir():\n    pass\n"
    )
    out = json.loads(bridge.compile_source(src))
    assert not out["ok"]
    assert out["error"]["kind"] == "compile"


def test_run_before_compile_and_bad_hex():
    bridge._LAST_PROGRAM = None
    out = json.loads(bridge.run_packet("aabb"))
    assert not out["ok"] and out["error"]["kind"] == "no_program"
    compile_ok(GOOD)
    out = json.loads(bridge.run_packet("zz"))
    assert not out["ok"] and out["error"]["kind"] == "bad_hex"


def test_run_returns_full_parse_result():
    compile_ok(GOOD)
    out = json.loads(bridge.run_packet("aa bb cc dd ee 01" + " 00" * 8))
    assert out["ok"]
    r = out["result"]
    assert r["verdict"] == 0
    assert r["payload_offset"] == 14
    assert r["smd"][:3] == [0xAABB, 0xCCDD, 0xEE01]
    assert len(r["hdr_present"]) == 16 and len(r["smd"]) == 8
