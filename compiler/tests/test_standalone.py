"""The IR stands alone as an interchange format: a Program built directly
from protos (no eDSL involved) lowers to assembly that assembles — and,
with the emulator available (NANUK_COSIM=1), parses packets correctly."""

import os

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.lower import to_asm
from nanuk_ir.validate import validate


def hand_built_program() -> ir.Program:
    """A tiny TLV-ish parser, written as protos by hand:

    start: record hdr 0; extract a 16-bit magic into SMD slot 0; skip the
    2-byte magic; magic 0xBEEF -> `payload`, anything else -> drop.
    payload: extract a 1-byte length, advance by length*2 (SHL+ADVR),
    record hdr 1 at the payload start; accept.
    """
    return ir.Program(
        ir_version=1,
        states=[
            ir.State(
                name="start",
                ops=[
                    ir.Op(mark=ir.Mark(hdr_id=0, emit_sethdr=True, debug_name="tlv")),
                    ir.Op(extract=ir.Extract(
                        value_id=1, bit_offset=0, width=16, debug_name="tlv.magic")),
                    ir.Op(emit_smd=ir.EmitSmd(value_id=1, slot=0)),
                    ir.Op(advance=ir.Advance(const_bytes=2)),
                ],
                terminator=ir.Terminator(dispatch=ir.Dispatch(
                    value_id=1,
                    cases=[ir.Case(match=0xBEEF, target_state="payload")],
                    default=ir.Terminator(halt=ir.Halt(drop=True)),
                )),
            ),
            ir.State(
                name="payload",
                ops=[
                    ir.Op(extract=ir.Extract(
                        value_id=2, bit_offset=0, width=8, debug_name="tlv.len")),
                    ir.Op(advance=ir.Advance(const_bytes=1)),
                    ir.Op(mark=ir.Mark(hdr_id=1, emit_sethdr=True, debug_name="body")),
                    ir.Op(shift=ir.Shift(value_id=3, src_value_id=2, amount=1)),
                    ir.Op(advance=ir.Advance(value_id=3)),
                ],
                terminator=ir.Terminator(halt=ir.Halt(drop=False)),
            ),
        ],
    )


def test_hand_built_ir_validates_and_assembles():
    from nanuk_isa.asm import assemble

    program = hand_built_program()
    validate(program)
    binary = assemble(to_asm(program))
    assert len(binary) > 0 and len(binary) % 4 == 0


@pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="cosim needs NANUK_COSIM=1 and a built nanuk-emu",
)
class TestOnEmulator:
    def test_accept_path(self):
        from nanuk_isa.asm import assemble
        from nanuk_spec.harness import VERDICT_ACCEPT, run_program

        prog = assemble(to_asm(hand_built_program()))
        # magic 0xBEEF, len 2 -> skip 4 body bytes; 1 byte of payload left.
        pkt = bytes([0xBE, 0xEF, 0x02, 0x11, 0x22, 0x33, 0x44, 0x55])
        r = run_program(prog, pkt)
        assert r.verdict == VERDICT_ACCEPT
        assert r.hdr(0) == 0
        assert r.hdr(1) == 3  # after magic + len byte
        assert r.payload_offset == 7  # 3 + 2*2
        assert r.smd[0] == 0xBEEF

    def test_drop_path(self):
        from nanuk_isa.asm import assemble
        from nanuk_spec.harness import VERDICT_DROP, run_program

        prog = assemble(to_asm(hand_built_program()))
        r = run_program(prog, bytes(8))
        assert r.verdict == VERDICT_DROP
        assert r.hdr(0) == 0
        assert r.hdr(1) is None
        assert r.payload_offset == 2
