"""Differential: map_interp(program) vs run_map(assemble(map_lower(program)))
— the MAP compiler's translation-validation-lite. ALL MatchActionResult fields must
agree, including steps and budget exhaustion.

Gated on NANUK_COSIM=1 (needs nanuk-map-emu from the devcontainer build)."""

import os
import random
from dataclasses import dataclass

import pytest

from nanuk.ir import nanuk_ir_pb2 as ir
from nanuk.ir.map_interp import map_interp
from nanuk.ir.map_lower import to_map_asm
from nanuk.isa.map_asm import assemble
from nanuk.testkit.map_harness import Table, run_map

from tests.ir.irbuild import flood_table_decl, l2fwd_program, load, load_md, send, store_md

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="needs nanuk-map-emu (linux container)",
)


@dataclass(frozen=True)
class StubPP:
    hdr_present: list
    hdr_offset: list


def pp_with(h2_off=14) -> StubPP:
    return StubPP(
        hdr_present=[1, 0, 1] + [0] * 13,
        hdr_offset=[0, 0, h2_off] + [0] * 13,
    )


FLOOD = Table(key_width=16, action_width=16,
              entries={i: (0xF & ~(1 << i)) for i in range(4)})


def md_with_ingress(ingress: int) -> list[int]:
    return [ingress, 0, 0, 0, 0, 0, 0, 0]


def assert_same(program, packet, pp, tables, md_in, info):
    binary = assemble(to_map_asm(program))
    golden = run_map(binary, packet, pp, tables, md_in)
    itp = map_interp(program, packet, pp, tables, md_in)
    for field in ("verdict", "error", "md", "delta", "steps", "frame"):
        g, i = getattr(golden, field), getattr(itp, field)
        if field == "md":
            g, i = tuple(g), tuple(i)
        assert g == i, (
            f"{field} diverged ({info}): emu={g} map_interp={i}"
        )


def ttl_program() -> ir.MatchActionProgram:
    return ir.MatchActionProgram(
        ir_version=1,
        tables=[ir.TableDecl(table_id=0, key_width=48, action_width=8), flood_table_decl()],
        states=[
            ir.MatchActionState(
                name="ttl",
                ops=[
                    load(1, hdr=2, off=8, n=1),
                    ir.MatchActionOp(const=ir.MapConst(value_id=2, imm=1)),
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
            ir.MatchActionState(
                name="dec",
                ops=[
                    load(3, hdr=2, off=8, n=1),
                    ir.MatchActionOp(add=ir.MapAdd(value_id=4, src_value_id=3, imm=-1)),
                    ir.MatchActionOp(
                        store=ir.MapStore(value_id=4, hdr_id=2, byte_offset=8, nbytes=1)
                    ),
                    load(10, hdr=2, off=0, n=1),
                    ir.MatchActionOp(and_imm=ir.AndImm(value_id=11, src_value_id=10, imm=0x000F)),
                    ir.MatchActionOp(shift=ir.Shift(value_id=12, src_value_id=11, amount=2)),
                    ir.MatchActionOp(const=ir.MapConst(value_id=13, imm=0)),
                    ir.MatchActionOp(
                        store=ir.MapStore(value_id=13, hdr_id=2, byte_offset=10, nbytes=2)
                    ),
                    ir.MatchActionOp(
                        csum=ir.Csum(value_id=14, hdr_id=2, byte_offset=0, len_value_id=12)
                    ),
                    ir.MatchActionOp(
                        store=ir.MapStore(value_id=14, hdr_id=2, byte_offset=10, nbytes=2)
                    ),
                ],
                terminator=ir.Terminator(goto=ir.Goto(target_state="fwd")),
            ),
            ir.MatchActionState(
                name="fwd",
                ops=[
                    load(5, hdr=0, off=0, n=6),
                    ir.MatchActionOp(
                        lookup=ir.Lookup(
                            value_id=6, table_id=0, key_value_id=5, miss_state="flood"
                        )
                    ),
                    store_md(6),
                ],
                terminator=send(),
            ),
            ir.MatchActionState(
                name="flood",
                ops=[
                    load_md(7),
                    ir.MatchActionOp(
                        lookup=ir.Lookup(value_id=8, table_id=3, key_value_id=7,
                                         miss_state="expired")
                    ),
                    store_md(8),
                ],
                terminator=send(),
            ),
            ir.MatchActionState(name="expired", terminator=ir.Terminator(drop=ir.Drop())),
        ],
    )


IPV4 = bytes(14) + bytes.fromhex("450000730000400040110000c0a80001c0a800c7") + bytes(30)

L2 = Table(key_width=48, action_width=8, entries={0x000000000000: 0x4, 0xA1B2C3D4E5F6: 0x2})


def test_l2fwd_differential():
    prog = l2fwd_program()
    tables = [L2, Table(key_width=0, action_width=0), Table(key_width=0, action_width=0), FLOOD]
    for i, packet in enumerate(
        [bytes(64), bytes.fromhex("a1b2c3d4e5f6") + bytes(58), IPV4, bytes(14)]
    ):
        for ingress in range(4):
            assert_same(prog, packet, pp_with(), tables,
                        md_with_ingress(ingress), f"l2fwd pkt{i}")


def test_ttl_differential():
    prog = ttl_program()
    tables = [L2, Table(key_width=0, action_width=0), Table(key_width=0, action_width=0), FLOOD]
    for ttl in (0, 1, 2, 64, 255):
        pkt = bytearray(IPV4)
        pkt[22] = ttl
        assert_same(prog, bytes(pkt), pp_with(), tables, md_with_ingress(1), f"ttl={ttl}")


def test_absent_header_and_send_range_differential():
    # h2 absent: LD from hdr 2 must error identically.
    pp = StubPP([1] + [0] * 15, [0] * 16)
    tables = [L2, Table(key_width=0, action_width=0), Table(key_width=0, action_width=0), FLOOD]
    assert_same(ttl_program(), IPV4, pp, tables, md_with_ingress(0), "absent h2")
    # Send delta out of range for a short packet.
    p = ir.MatchActionProgram(
        ir_version=1,
        states=[
            ir.MatchActionState(
                name="s",
                ops=[load_md(1), store_md(1)],
                terminator=send(delta=-30),
            )
        ],
    )
    assert_same(p, bytes(8), StubPP([0] * 16, [0] * 16), [], md_with_ingress(0), "send range")


def test_random_program_differential():
    """Random-but-valid MAP IR programs over random packets/tables."""
    rng = random.Random(0x4D4150)  # "MAP"
    for trial in range(20):
        ops = [
            ir.MatchActionOp(load=ir.MapLoad(value_id=1, hdr_id=15,
                                     byte_offset=rng.randrange(0, 8), nbytes=6)),
            ir.MatchActionOp(
                lookup=ir.Lookup(value_id=2, table_id=0, key_value_id=1,
                                 miss_state="flood")
            ),
            ir.MatchActionOp(add=ir.MapAdd(value_id=3, src_value_id=2,
                                   imm=rng.randrange(-10, 10))),
        ]
        ops.append(store_md(3))
        prog = ir.MatchActionProgram(
            ir_version=1,
            tables=[ir.TableDecl(table_id=0, key_width=48, action_width=8),
                    flood_table_decl()],
            states=[
                ir.MatchActionState(name="s", ops=ops, terminator=send()),
                ir.MatchActionState(
                    name="flood",
                    ops=[
                        load_md(9),
                        ir.MatchActionOp(
                            lookup=ir.Lookup(value_id=10, table_id=3,
                                             key_value_id=9, miss_state="dark")
                        ),
                        store_md(10),
                    ],
                    terminator=send(delta=rng.choice([0, 4, 32])),
                ),
                ir.MatchActionState(
                    name="dark", terminator=ir.Terminator(drop=ir.Drop())
                ),
            ],
        )
        packet = rng.randbytes(rng.randrange(33, 300))
        entries = {rng.getrandbits(48): rng.getrandbits(8) for _ in range(3)}
        if rng.random() < 0.5:
            entries[int.from_bytes(packet[:6], "big")] = rng.getrandbits(8)
        tables = [Table(key_width=48, action_width=8, entries=entries),
                  Table(key_width=0, action_width=0),
                  Table(key_width=0, action_width=0), FLOOD]
        assert_same(
            prog, packet, StubPP([0] * 16, [0] * 16), tables,
            md_with_ingress(rng.randrange(4)), f"random trial {trial}",
        )
