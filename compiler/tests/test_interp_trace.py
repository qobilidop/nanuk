"""Interp trace recorder: event stream shape, step accounting, and the
off-by-default guarantee (results identical with and without a trace)."""

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.interp import interp
from nanuk_ir.interp_map import interp_map

PKT = bytes.fromhex("aabb") + bytes(20)


def three_state_program() -> ir.Program:
    return ir.Program(ir_version=1, states=[
        ir.State(
            name="start",
            ops=[ir.Op(extract=ir.Extract(value_id=1, bit_offset=0, width=16,
                                          debug_name="first16"))],
            terminator=ir.Terminator(dispatch=ir.Dispatch(
                value_id=1,
                cases=[
                    ir.Case(match=0xAABB, target_state="match"),
                    ir.Case(match=0x1111, target_state="other"),
                ],
                default=ir.Terminator(halt=ir.Halt(drop=True)),
            )),
        ),
        ir.State(
            name="match",
            ops=[ir.Op(advance=ir.Advance(const_bytes=2))],
            terminator=ir.Terminator(halt=ir.Halt(drop=False)),
        ),
        ir.State(
            name="other",
            ops=[],
            terminator=ir.Terminator(halt=ir.Halt(drop=True)),
        ),
    ])


def test_trace_events_and_steps():
    events = []
    r = interp(three_state_program(), PKT, trace=events)
    assert r.verdict == 0
    kinds = [(e.state, e.kind, e.index) for e in events]
    assert kinds == [
        ("start", "op", 0),         # extract
        ("start", "term_case", 0),  # 0xAABB matches -> 2 steps
        ("match", "op", 0),         # advance
        ("match", "term", 0),       # halt
    ]
    steps = [e.steps_after for e in events]
    assert steps == [1, 3, 4, 5]
    assert steps[-1] == r.steps
    assert events[0].values == {1: 0xAABB}
    assert events[2].cursor == 2  # snapshot after the advance
    assert events[0].cursor == 0


def test_trace_dispatch_default():
    pkt = bytes.fromhex("beef") + bytes(20)
    events = []
    r = interp(three_state_program(), pkt, trace=events)
    assert r.verdict == 1
    assert [(e.kind, e.index) for e in events] == [
        ("op", 0), ("term_case", 0), ("term_case", 1), ("term_default", 0),
    ]
    assert events[-1].steps_after == r.steps == 6


def test_trace_error_mid_op():
    prog = ir.Program(ir_version=1, states=[
        ir.State(name="start",
                 ops=[ir.Op(advance=ir.Advance(const_bytes=300))],
                 terminator=ir.Terminator(halt=ir.Halt(drop=False))),
    ])
    events = []
    r = interp(prog, bytes(16), trace=events)
    assert (r.verdict, r.error) == (2, 1)
    assert [(e.kind, e.index) for e in events] == [("op", 0)]
    assert events[0].steps_after == r.steps == 1
    assert events[0].cursor == 0


def test_trace_none_is_default_and_unchanged():
    assert interp(three_state_program(), PKT) == interp(
        three_state_program(), PKT, trace=[]
    )


class _Pp:
    hdr_present = [1] + [0] * 15
    hdr_offset = [0] * 16
    smd = [0] * 8


class _Tbl:
    key_width, action_width = 48, 8
    entries = {0xAB: 0x2}


def map_prog() -> ir.MapProgram:
    return ir.MapProgram(
        ir_version=1,
        tables=[ir.TableDecl(table_id=0, key_width=48, action_width=8,
                             debug_name="fdb")],
        states=[
            ir.MapState(
                name="start",
                ops=[
                    ir.MapOp(const=ir.MapConst(value_id=1, imm=0xAB,
                                               debug_name="val")),
                    ir.MapOp(store=ir.MapStore(value_id=1, hdr_id=15,
                                               byte_offset=0, nbytes=1)),
                    ir.MapOp(lookup=ir.Lookup(value_id=2, table_id=0,
                                              key_value_id=1,
                                              miss_state="bye")),
                ],
                terminator=ir.Terminator(send=ir.MapSend(bitmap_value_id=2)),
            ),
            ir.MapState(name="bye", ops=[],
                        terminator=ir.Terminator(drop=ir.Drop())),
        ],
    )


def test_map_trace_store_lookup_and_miss_events():
    events = []
    r = interp_map(map_prog(), bytes(20), _Pp(), [_Tbl()], 0, trace=events)
    assert r.verdict == 0
    st = next(e for e in events if e.kind == "op" and e.index == 1)
    assert st.writes == ((32, b"\xab"),)
    lk = next(e for e in events if e.kind == "op" and e.index == 2)
    assert lk.lookup == (0, 0xAB, True, 0x2)
    assert events[-1].kind == "term"

    # Miss path: empty table -> lookup event, control transfer, drop.
    class Empty:
        key_width, action_width, entries = 48, 8, {}

    events = []
    r = interp_map(map_prog(), bytes(20), _Pp(), [Empty()], 0, trace=events)
    assert r.verdict == 1
    lk = next(e for e in events if e.kind == "op" and e.index == 2)
    assert lk.lookup == (0, 0xAB, False, 0)
    assert events[-1].state == "bye" and events[-1].kind == "term"
