"""IR interpreter semantics, mirrored from the frozen ISA v0 semantics
(stage-1 plan / spec/model). No emulator needed: these are pure-Python
unit tests; the emulator comparison lives in test_differential.py."""

import pytest

from nanuk_ir import nanuk_ir_pb2 as ir
from nanuk_ir.interp import (
    STEP_BUDGET,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
    ERR_NONE,
    ERR_STEP_BUDGET,
    interp,
)


# -- tiny builders -----------------------------------------------------------

def prog(*states: ir.State) -> ir.Program:
    return ir.Program(ir_version=1, states=list(states))


def halt(drop: bool = False) -> ir.Terminator:
    return ir.Terminator(halt=ir.Halt(drop=drop))


def goto(target: str) -> ir.Terminator:
    return ir.Terminator(goto=ir.Goto(target_state=target))


# -- halts, run loop, budget -------------------------------------------------

def test_halt_accept():
    r = interp(prog(ir.State(name="s", terminator=halt(drop=False))), b"\x00")
    assert r.verdict == VERDICT_ACCEPT
    assert r.error == ERR_NONE
    assert r.accepted
    assert r.payload_offset == 0
    assert r.steps == 1  # the HALT itself


def test_halt_drop():
    r = interp(prog(ir.State(name="s", terminator=halt(drop=True))), b"\x00")
    assert r.verdict == VERDICT_DROP
    assert not r.accepted


def test_goto_chains_states_and_costs_one_step_each():
    p = prog(
        ir.State(name="a", terminator=goto("b")),
        ir.State(name="b", terminator=goto("c")),
        ir.State(name="c", terminator=halt()),
    )
    r = interp(p, b"")
    assert r.accepted
    assert r.steps == 3  # jmp, jmp, halt


def test_goto_loop_exhausts_step_budget():
    p = prog(
        ir.State(name="a", terminator=goto("b")),
        ir.State(name="b", terminator=goto("a")),
    )
    r = interp(p, b"\x00" * 8)
    assert r.verdict == VERDICT_ERROR
    assert r.error == ERR_STEP_BUDGET
    assert r.steps == STEP_BUDGET  # saturated: error on the 257th attempt


def test_empty_packet_is_fine():
    r = interp(prog(ir.State(name="s", terminator=halt())), b"")
    assert r.accepted


def test_start_state_is_states_zero_not_name():
    p = prog(
        ir.State(name="not_start", terminator=halt(drop=True)),
        ir.State(name="start", terminator=halt(drop=False)),
    )
    assert interp(p, b"").verdict == VERDICT_DROP


def test_outputs_are_fresh_per_run():
    p = prog(ir.State(name="s", terminator=halt()))
    a, b = interp(p, b""), interp(p, b"")
    assert a.hdr_present == [0] * 16 and a.smd == [0] * 8
    assert a.hdr_present is not b.hdr_present  # no shared mutable state
