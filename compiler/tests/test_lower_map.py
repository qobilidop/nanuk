"""MAP lowering: golden assembly for the L2-forward IR; assembles with the
real map assembler; register exhaustion raises with a live-value message."""

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.lower import LowerError
from nanuk_ir.lower_map import to_map_asm
from nanuk_spec.map_asm import assemble

from tests.irbuild import l2fwd_program, load


def test_l2fwd_golden_asm():
    # The key register dies at the lookup, so the result reuses it (rd == rs
    # is well-defined: the key is read before the result is written).
    asm = to_map_asm(l2fwd_program())
    expected = """\
forward:
    ld      r0, 0, 0, 6            ; v1
    lookup  r0, 0, r0, flood       ; lookup t0[v1]
    send    r0, 0                  ; lookup t0[v1]

flood:
    ldmd    r0, 9                  ; flood
    send    r0, 0                  ; flood
"""
    assert asm == expected


def test_lowered_asm_assembles():
    binary = assemble(to_map_asm(l2fwd_program()))
    assert len(binary) == 5 * 4  # the 5-instruction switch


def test_out_of_registers():
    # Four genuinely-overlapping live ranges: each load is consumed by a
    # store AFTER all four loads, so liveness cannot free anything early.
    p = ir.MapProgram(
        ir_version=1,
        states=[
            ir.MapState(
                name="s",
                ops=[load(i, n=1, off=i - 1) for i in range(1, 5)]
                + [
                    ir.MapOp(
                        store=ir.MapStore(
                            value_id=i, hdr_id=15, byte_offset=8 + i, nbytes=1
                        )
                    )
                    for i in (4, 3, 2, 1)
                ],
                terminator=ir.Terminator(drop=ir.Drop()),
            )
        ],
    )
    with pytest.raises(LowerError, match="out of registers"):
        to_map_asm(p)


def test_dispatch_lowering_cost_shape():
    # dispatch with 2 cases -> movi+beq per case + default terminator.
    p = ir.MapProgram(
        ir_version=1,
        states=[
            ir.MapState(
                name="s",
                ops=[load(1, n=2)],
                terminator=ir.Terminator(
                    dispatch=ir.Dispatch(
                        value_id=1,
                        cases=[
                            ir.Case(match=0x0800, target_state="a"),
                            ir.Case(match=0x8100, target_state="b"),
                        ],
                        default=ir.Terminator(drop=ir.Drop()),
                    )
                ),
            ),
            ir.MapState(name="a", terminator=ir.Terminator(drop=ir.Drop())),
            ir.MapState(name="b", terminator=ir.Terminator(drop=ir.Drop())),
        ],
    )
    asm = to_map_asm(p)
    assert asm.count("movi    r3") == 2
    assert asm.count("beq") == 2
    binary = assemble(asm)
    assert len(binary) == (1 + 4 + 1 + 1 + 1) * 4
