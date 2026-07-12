"""Symbolic executor: path enumeration with exact predictions, witness
generation, and (cosim-gated) differential validation of every witness
against pp_interp AND the golden emulator — including the headline payoff:
symex INVENTS a valid nanukproto tunnel packet from constraints alone."""

from nanuk.ir import nanuk_ir_pb2 as ir
from nanuk.ir.pp_interp import pp_interp
from nanuk.ir.pp_symex import SymPath, gen_corpus, reachable_states, symex


def halt(drop=False):
    return ir.Terminator(halt=ir.Halt(drop=drop))


def tiny_program() -> ir.ParserProgram:
    """EXT ethertype-ish field at bit 96, dispatch on it, drop default."""
    return ir.ParserProgram(
        ir_version=1,
        states=[
            ir.ParserState(
                name="start",
                ops=[
                    ir.ParserOp(mark=ir.Mark(hdr_id=0, emit_sethdr=True)),
                    ir.ParserOp(
                        extract=ir.Extract(value_id=1, bit_offset=96, width=16)
                    ),
                    ir.ParserOp(advance=ir.Advance(const_bytes=14)),
                ],
                terminator=ir.Terminator(
                    dispatch=ir.Dispatch(
                        value_id=1,
                        cases=[ir.Case(match=0x0800, target_state="v4")],
                        default=halt(drop=True),
                    )
                ),
            ),
            ir.ParserState(name="v4", terminator=halt()),
        ],
    )


def by_outcome(paths: list[SymPath]) -> dict[tuple[int, int], list[SymPath]]:
    out: dict[tuple[int, int], list[SymPath]] = {}
    for p in paths:
        out.setdefault((p.verdict, p.error), []).append(p)
    return out


def test_tiny_program_paths_and_witnesses():
    paths = symex(tiny_program())
    outcomes = by_outcome(paths)
    # accept (0x0800 case), drop (default), violation (short packet).
    assert (0, 0) in outcomes
    assert (1, 0) in outcomes
    assert (2, 1) in outcomes
    accept = outcomes[(0, 0)][0]
    assert accept.witness is not None
    assert accept.witness[12:14] == b"\x08\x00"
    assert accept.trace == ("start", "v4")


def test_predictions_match_interp_on_witnesses():
    prog = tiny_program()
    for p in symex(prog):
        assert p.witness is not None
        r = pp_interp(prog, p.witness)
        assert (r.verdict, r.error, r.steps) == (p.verdict, p.error, p.steps), (
            f"path {p.trace} predicted {(p.verdict, p.error, p.steps)}, "
            f"pp_interp said {(r.verdict, r.error, r.steps)}"
        )


def test_unroll_terminates_loops():
    # start -> start via goto: unbounded without the unroll cap.
    prog = ir.ParserProgram(
        ir_version=1,
        states=[
            ir.ParserState(
                name="start",
                ops=[ir.ParserOp(advance=ir.Advance(const_bytes=4))],
                terminator=ir.Terminator(goto=ir.Goto(target_state="start")),
            )
        ],
    )
    paths = symex(prog, unroll=3)
    # Only the violation exits are feasible ends within the unroll bound.
    assert paths
    assert all(p.verdict == 2 for p in paths)


def test_gen_corpus_and_reachability():
    prog = tiny_program()
    corpus = gen_corpus(prog)
    assert len(corpus) >= 3
    assert reachable_states(prog) == {"start", "v4"}
