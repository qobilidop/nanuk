"""Translation validation, the light way: pp_interp(IR) and
emulate(lower(IR)) must agree on EVERY ParserResult field — including
`steps` and budget exhaustion, since the interpreter's cost accounting
mirrors the v0 lowering instruction-for-instruction.

Gated behind NANUK_COSIM=1 (needs the built nanuk-pp-emu golden model)."""

import os
import random

import pytest

from nanuk.ir import nanuk_ir_pb2 as ir
from nanuk.ir.pp_interp import pp_interp
from nanuk.ir.pp_lower import to_pp_asm
from nanuk.isa.pp_asm import assemble
from nanuk.testkit.pp_harness import run_program

from test_roundtrip import rich_program

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="differential rig needs NANUK_COSIM=1 and a built nanuk-pp-emu",
)

FIELDS = ("verdict", "error", "payload_offset", "steps",
          "hdr_present", "hdr_offset", "md")


def assert_same(program: ir.ParserProgram, packet: bytes, label: str) -> None:
    ir_result = pp_interp(program, packet)
    emu_result = run_program(assemble(to_pp_asm(program)), packet)
    for field in FIELDS:
        assert getattr(ir_result, field) == getattr(emu_result, field), (
            f"{label}: field {field!r} diverges: "
            f"pp_interp={getattr(ir_result, field)!r} "
            f"emu={getattr(emu_result, field)!r} packet={packet.hex()}"
        )


def budget_loop() -> ir.ParserProgram:
    """Extract + self-goto forever: exhausts the step budget on any packet
    long enough to extract from, exercising error-2 + steps parity."""
    return ir.ParserProgram(ir_version=1, states=[
        ir.ParserState(
            name="spin",
            ops=[ir.ParserOp(extract=ir.Extract(value_id=1, bit_offset=0, width=8))],
            terminator=ir.Terminator(goto=ir.Goto(target_state="spin")),
        ),
    ])


def const_program() -> ir.ParserProgram:
    """MOVI two literals into the md window, then accept. Packet-independent,
    so it runs on every edge packet (empty included) and exercises the Movi op
    end to end — the interpreter's one-step cost model vs the lowered `movi`
    word — the RTL-drift lesson applied at IR level: a new op nobody fuzzes is
    silently untested."""
    return ir.ParserProgram(ir_version=1, states=[
        ir.ParserState(
            name="lit",
            ops=[
                ir.ParserOp(movi=ir.Movi(value_id=1, imm=0x0B, debug_name="bitmap")),
                ir.ParserOp(emit_md=ir.MdStore(value_id=1, slot=1, nunits=1)),
                ir.ParserOp(movi=ir.Movi(value_id=2, imm=0xBEEF, debug_name="k")),
                ir.ParserOp(emit_md=ir.MdStore(value_id=2, slot=2, nunits=1)),
            ],
            terminator=ir.Terminator(halt=ir.Halt(drop=False)),
        ),
    ])


def edge_packets() -> list[bytes]:
    return [
        b"",                        # empty: extracts fail immediately
        b"\x00",                    # 1 byte
        b"\xbe\xef" + b"\x00" * 5,  # 7 bytes: rich_program's advi 7 lands exactly
        b"\xbe\xef" + b"\x00" * 6,  # 8 bytes: one to spare
        bytes(range(64)),           # plenty
    ]


@pytest.mark.parametrize("pkt", edge_packets(), ids=lambda p: f"len{len(p)}")
def test_rich_program_edges(pkt):
    assert_same(rich_program(), pkt, "rich/edge")


@pytest.mark.parametrize("pkt", edge_packets(), ids=lambda p: f"len{len(p)}")
def test_budget_loop_edges(pkt):
    assert_same(budget_loop(), pkt, "loop/edge")


@pytest.mark.parametrize("pkt", edge_packets(), ids=lambda p: f"len{len(p)}")
def test_const_program_edges(pkt):
    assert_same(const_program(), pkt, "const/edge")


@pytest.mark.parametrize("seed", range(10))
def test_rich_program_random_packets(seed):
    rng = random.Random(3000 + seed)
    for i in range(10):
        pkt = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 65)))
        assert_same(rich_program(), pkt, f"rich/seed={seed} pkt={i}")
