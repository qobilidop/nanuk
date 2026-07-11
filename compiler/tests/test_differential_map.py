"""Differential: interp_map(program) vs run_map(assemble(lower_map(program)))
— the MAP compiler's translation-validation-lite. ALL MapResult fields must
agree, including steps and budget exhaustion.

Gated on NANUK_COSIM=1 (needs nanuk-map-emu from the devcontainer build)."""

import os
import random
from dataclasses import dataclass

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.interp_map import interp_map
from nanuk_ir.lower_map import to_map_asm
from nanuk_spec.map_asm import assemble
from nanuk_spec.map_harness import Table, run_map

from tests.test_validate_map import l2fwd_program, load, send

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-map-emu (linux container)",
)


@dataclass(frozen=True)
class StubPP:
    hdr_present: list
    hdr_offset: list
    smd: list


def pp_with(h2_off=14) -> StubPP:
    return StubPP(
        hdr_present=[1, 0, 1] + [0] * 13,
        hdr_offset=[0, 0, h2_off] + [0] * 13,
        smd=[0x1234, 0, 0, 0, 0, 0x4E4B, 0, 0],
    )


def assert_same(program, packet, pp, tables, ingress, info):
    binary = assemble(to_map_asm(program))
    golden = run_map(binary, packet, pp, tables, ingress)
    itp = interp_map(program, packet, pp, tables, ingress)
    for field in ("verdict", "error", "egress", "delta", "steps", "frame"):
        assert getattr(golden, field) == getattr(itp, field), (
            f"{field} diverged ({info}): "
            f"emu={getattr(golden, field)} interp={getattr(itp, field)}"
        )


def ttl_program() -> ir.MapProgram:
    return ir.MapProgram(
        ir_version=1,
        tables=[ir.TableDecl(table_id=0, key_width=48, action_width=8)],
        states=[
            ir.MapState(
                name="ttl",
                ops=[
                    load(1, hdr=2, off=8, n=1),
                    ir.MapOp(const=ir.MapConst(value_id=2, imm=1)),
                ],
                terminator=ir.Terminator(
                    dispatch=ir.Dispatch(
                        value_id=1,
                        cases=[
                            ir.Case(match=0, target_state="expired"),
                            ir.Case(match=1, target_state="expired"),
                        ],
                        default=ir.Terminator(goto=ir.Goto(target_state="dec")),
                    )
                ),
            ),
            ir.MapState(
                name="dec",
                ops=[
                    load(3, hdr=2, off=8, n=1),
                    ir.MapOp(add=ir.MapAdd(value_id=4, src_value_id=3, imm=-1)),
                    ir.MapOp(
                        store=ir.MapStore(value_id=4, hdr_id=2, byte_offset=8, nbytes=1)
                    ),
                    ir.MapOp(csum=ir.CsumUpdate(hdr_id=2, byte_offset=0)),
                ],
                terminator=ir.Terminator(goto=ir.Goto(target_state="fwd")),
            ),
            ir.MapState(
                name="fwd",
                ops=[
                    load(5, hdr=0, off=0, n=6),
                    ir.MapOp(
                        lookup=ir.Lookup(
                            value_id=6, table_id=0, key_value_id=5, miss_state="flood"
                        )
                    ),
                ],
                terminator=send(6),
            ),
            ir.MapState(
                name="flood",
                ops=[ir.MapOp(load_md=ir.MapLoadMd(value_id=7, field=9))],
                terminator=send(7),
            ),
            ir.MapState(name="expired", terminator=ir.Terminator(drop=ir.Drop())),
        ],
    )


IPV4 = bytes(14) + bytes.fromhex("450000730000400040110000c0a80001c0a800c7") + bytes(30)

L2 = Table(key_width=48, action_width=8, entries={0x000000000000: 0x4, 0xA1B2C3D4E5F6: 0x2})


def test_l2fwd_differential():
    prog = l2fwd_program()
    for i, packet in enumerate(
        [bytes(64), bytes.fromhex("a1b2c3d4e5f6") + bytes(58), IPV4, bytes(14)]
    ):
        for ingress in range(4):
            assert_same(prog, packet, pp_with(), [L2], ingress, f"l2fwd pkt{i}")


def test_ttl_differential():
    prog = ttl_program()
    for ttl in (0, 1, 2, 64, 255):
        pkt = bytearray(IPV4)
        pkt[22] = ttl
        assert_same(prog, bytes(pkt), pp_with(), [L2], 1, f"ttl={ttl}")


def test_absent_header_and_send_range_differential():
    # h2 absent: LD from hdr 2 must error identically.
    pp = StubPP([1] + [0] * 15, [0] * 16, [0] * 8)
    assert_same(ttl_program(), IPV4, pp, [L2], 0, "absent h2")
    # Send delta out of range for a short packet.
    p = ir.MapProgram(
        ir_version=1,
        states=[
            ir.MapState(
                name="s",
                ops=[ir.MapOp(load_md=ir.MapLoadMd(value_id=1, field=9))],
                terminator=send(1, delta=-30),
            )
        ],
    )
    assert_same(p, bytes(8), StubPP([0] * 16, [0] * 16, [0] * 8), [], 0, "send range")


def test_random_program_differential():
    """Random-but-valid MAP IR programs over random packets/tables."""
    rng = random.Random(0x4D4150)  # "MAP"
    for trial in range(20):
        vid = iter(range(1, 100)).__next__
        ops = [
            ir.MapOp(load=ir.MapLoad(value_id=1, hdr_id=15,
                                     byte_offset=rng.randrange(0, 8), nbytes=6)),
            ir.MapOp(
                lookup=ir.Lookup(value_id=2, table_id=0, key_value_id=1,
                                 miss_state="flood")
            ),
            ir.MapOp(add=ir.MapAdd(value_id=3, src_value_id=2,
                                   imm=rng.randrange(-10, 10))),
        ]
        prog = ir.MapProgram(
            ir_version=1,
            tables=[ir.TableDecl(table_id=0, key_width=48, action_width=8)],
            states=[
                ir.MapState(name="s", ops=ops, terminator=send(3)),
                ir.MapState(
                    name="flood",
                    ops=[ir.MapOp(load_md=ir.MapLoadMd(value_id=9, field=9))],
                    terminator=send(9, delta=rng.choice([0, 4, 32])),
                ),
            ],
        )
        packet = rng.randbytes(rng.randrange(33, 300))
        entries = {rng.getrandbits(48): rng.getrandbits(8) for _ in range(3)}
        if rng.random() < 0.5:
            entries[int.from_bytes(packet[:6], "big")] = rng.getrandbits(8)
        tables = [Table(key_width=48, action_width=8, entries=entries)]
        assert_same(
            prog, packet, StubPP([0] * 16, [0] * 16, [0] * 8), tables,
            rng.randrange(4), f"random trial {trial}",
        )
