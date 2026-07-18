"""The SIIT translator program (examples/siit) against the golden emulators.

One rig: every committed conformance vector (benchmarks/siit/vectors/*.json,
generated from the reference translator) runs through the composed
PP -> MAP golden-model pipeline with the DEMO_SIIT table plane, and the
result must reproduce the vector byte-for-byte -- `sent` vectors must emit
exactly the reference's output frame, `drop` vectors must be refused by the
PP (structural refusal) or dropped by the MAP (value decision).

Plus two canaries: the per-packet step budget (both engines are 256-step
machines; the worst vector must clear the budget with air) and imem usage
(both programs must fit the ~1K-word instruction memories).

KNOWN-IMPOSSIBLE VECTOR (strict xfail below, not a bug in the program):
`edge_min_frame_46` is inexpressible on the Nanuk core, provably. Its input
is `udp46_len0_ttl64`'s 42-byte frame plus 18 bytes of Ethernet minimum-frame
padding, and the two committed vectors share an identical 42-byte prefix but
demand different SEND deltas (+20 vs +2, because the reference strips the
padding: the emitted frame always ends at HEADROOM + plen, so stripping pad
means right-aligning the output against a frame end the program would have
to locate). Neither ISA exposes the physical frame length (a deliberate v0.1
decision -- "no program in any corpus ever reads the frame length"), and
every read past a frame's end is a *terminal* error halt, so no probe can
measure it: a program that translates udp46_len0_ttl64 correctly performs
reads only within the shared 42-byte prefix, hence executes identically on
edge_min_frame_46 and emits the same delta -- producing 18 trailing padding
bytes the expected output does not have. Any program that instead reads into
the padding region faults udp46_len0_ttl64. So no program passes both.
The gap: stripping L2 padding requires knowing plen. Recorded for the lab
notes / audit; the other 67 vectors are the program's contract.
"""

from pathlib import Path

import pytest

from nanuk.isa import map_asm, pp_asm
from nanuk.testkit import map_harness
from nanuk.testkit.siit_ref import load_vectors
from nanuk.testkit.testkit import siit_tables

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
PP = pp_asm.assemble((EXAMPLES / "siit" / "parse.asm").read_text())
MP = map_asm.assemble((EXAMPLES / "siit" / "translate.asm").read_text())

IMEM_WORDS = 1024  # per-processor instruction memory (~1K words)
STEP_BUDGET = 256  # per-processor watchdog (spec/sail params)
STEP_CANARY = 200  # rig-level headroom line: worst case must sit below this

IMPOSSIBLE = {
    # See module docstring: padding strip requires the physical frame length,
    # which neither ISA exposes. strict=True so an unexpected pass trips.
    "edge_min_frame_46": "L2 padding strip requires plen; not program-visible",
}


def _run(vec):
    return map_harness.run_pipeline(
        PP, MP, bytes.fromhex(vec["in"]), siit_tables(), md_in=(0,) * 8
    )


def _params():
    return [
        pytest.param(
            vec,
            id=vec["name"],
            marks=(
                [pytest.mark.xfail(reason=IMPOSSIBLE[vec["name"]], strict=True)]
                if vec["name"] in IMPOSSIBLE
                else []
            ),
        )
        for vec in load_vectors()
    ]


@pytest.mark.parametrize("vec", _params())
def test_vector_on_golden_model(vec):
    pp, r = _run(vec)
    if vec["verdict"] == "sent":
        assert r is not None, (
            f"PP refused a sent vector: verdict={pp.verdict} error={pp.error}"
        )
        assert r.sent and r.error == 0, (
            f"MAP did not send: verdict={r.verdict} error={r.error}"
        )
        # THE WHOLE FRAME -- never a field at a time (repo lesson: partial
        # asserts once passed while the MAC was mangled).
        assert r.frame == bytes.fromhex(vec["out"])
    else:
        # Drop vectors: PP refusal (short-circuit) or MAP drop both satisfy
        # the oracle; the split (structural vs value) is the programs' design.
        assert r is None or not r.sent


def test_step_budget_canary():
    """Worst-case step counts across the whole corpus, both engines."""
    worst_pp = worst_map = 0
    worst_pp_name = worst_map_name = ""
    for vec in load_vectors():
        pp, r = _run(vec)
        if pp.steps > worst_pp:
            worst_pp, worst_pp_name = pp.steps, vec["name"]
        if r is not None and r.steps > worst_map:
            worst_map, worst_map_name = r.steps, vec["name"]
    assert worst_pp < STEP_CANARY and worst_map < STEP_CANARY, (
        f"step canary: PP worst {worst_pp} ({worst_pp_name}), "
        f"MAP worst {worst_map} ({worst_map_name}), line {STEP_CANARY}, "
        f"budget {STEP_BUDGET}"
    )


def test_imem_usage_canary():
    """Both programs must fit the ~1K-word instruction memories."""
    pp_words, mp_words = len(PP) // 4, len(MP) // 4
    assert pp_words <= IMEM_WORDS and mp_words <= IMEM_WORDS, (
        f"imem canary: parse.asm {pp_words} words, "
        f"translate.asm {mp_words} words, limit {IMEM_WORDS}"
    )
