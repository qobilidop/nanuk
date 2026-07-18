"""NanukCore cosim: the committed SIIT vector corpus, streamed through the
real RTL and diffed against the chained ISS oracle.

This mirrors test_core.py's streaming BFM (drive-then-sample pre-edge,
randomized valid/ready gaps) and its chained-ISS oracle (run_pp_iss ->
gate on accept -> run_map_iss), but swaps the l2l3l4/map_l2fwd demo
programs for the SIIT translator (examples/siit/{parse.asm,translate.asm})
with its EAMT table plane (nanuk.testkit.testkit.siit_tables()), and the
hand-built demo cases for the full committed vector suite
(nanuk.testkit.siit_ref.load_vectors()).

The vectors themselves are already proven against the reference SW
translator (sw/python/tests/test_siit_vectors.py) and against the golden
ISS pipeline (sw/python/tests/test_siit_program.py); this leg's job is
narrower and orthogonal: RTL == ISS, byte for byte, on every one of them.
Where a vector says "sent" we additionally check the RTL frame against the
vector's own `out` field -- redundant with RTL==ISS==vector (proven
elsewhere), but cheap, and it catches harness bugs in this file rather than
RTL drift.

The corpus includes two frames over 256 bytes (edge_tail_passthrough_46/64)
that exercise NanukCore's tail-buffer path beyond its prefetch window, and
two IHL 11/12 IPv4-options vectors -- both ride the same streaming BFM as
everything else; no special-casing needed since NanukCore's default
max_frame (2048) comfortably covers the whole corpus.
"""

import random
from pathlib import Path

from amaranth.sim import Simulator

from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.isa.map_iss import run_map_iss
from nanuk.isa.pp_asm import assemble as pp_assemble
from nanuk.isa.pp_iss import VERDICT_ACCEPT as PP_ACCEPT
from nanuk.isa.pp_iss import VERDICT_DROP as PP_DROP
from nanuk.isa.pp_iss import run_pp_iss
from nanuk.testkit.siit_ref import load_vectors
from nanuk.testkit.testkit import siit_tables

from nanuk_amaranth.core import (
    STAGE_MAP,
    STAGE_PP,
    VERDICT_DROP,
    VERDICT_ERROR,
    VERDICT_SENT,
    NanukCore,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples"

PP_PROG = pp_assemble((EXAMPLES_DIR / "siit" / "parse.asm").read_text())
MAP_PROG = map_assemble((EXAMPLES_DIR / "siit" / "translate.asm").read_text())
TABLES = siit_tables()
MD_IN = (0,) * 8


def oracle(packet):
    """The chained ISS: (verdict, error{stage,code}, md, frame|None)."""
    pp = run_pp_iss(PP_PROG, packet, MD_IN)
    if pp.verdict != PP_ACCEPT:
        verdict = VERDICT_DROP if pp.verdict == PP_DROP else VERDICT_ERROR
        error = 0 if pp.verdict == PP_DROP else (STAGE_PP << 4) | pp.error
        return verdict, error, tuple(pp.md), None
    mp = run_map_iss(MAP_PROG, packet, pp, TABLES, pp.md)
    if mp.verdict == 0:
        return VERDICT_SENT, 0, tuple(mp.md), mp.frame
    if mp.verdict == 1:
        return VERDICT_DROP, 0, tuple(mp.md), None
    return VERDICT_ERROR, (STAGE_MAP << 4) | mp.error, tuple(mp.md), None


def _words(prog: bytes) -> list[int]:
    return [int.from_bytes(prog[i : i + 4], "big") for i in range(0, len(prog), 4)]


async def _drive_ctrl(ctx, dut, sel, addr, data):
    ctx.set(dut.ctrl_sel, sel)
    ctx.set(dut.ctrl_addr, addr)
    ctx.set(dut.ctrl_data, data)
    ctx.set(dut.ctrl_we, 1)
    await ctx.tick()
    ctx.set(dut.ctrl_we, 0)


async def load_core(ctx, dut):
    for addr, w in enumerate(_words(PP_PROG)):
        await _drive_ctrl(ctx, dut, 0, addr, w)
    for addr, w in enumerate(_words(MAP_PROG)):
        await _drive_ctrl(ctx, dut, 1, addr, w)
    for tid, table in enumerate(TABLES):
        await _drive_ctrl(
            ctx, dut, 2, tid, (table.action_width << 8) | table.key_width
        )
        for key, action in table.entries.items():
            await _drive_ctrl(ctx, dut, 3, tid, key)
            await _drive_ctrl(ctx, dut, 3, (1 << 15) | tid, action)


_MAX_RUN_CYCLES = 200_000


async def run_packet(ctx, dut, packet, rng):
    """Stream one packet in/out; returns (verdict, error, md, frame|None).

    All sampling happens pre-edge: the DUT acts on the values present
    before each tick, so acceptance/capture must be judged on the same
    pre-tick snapshot the DUT saw (donor repo lesson: an off-by-one here
    once slipped through)."""
    md = 0
    for k, v in enumerate(MD_IN):
        md |= (v & 0xFFFF) << (16 * k)
    ctx.set(dut.md_in, md)

    out = bytearray()
    i = 0
    valid_now = False
    for _ in range(_MAX_RUN_CYCLES):
        # Drive the input side for THIS cycle.
        if i < len(packet):
            valid_now = rng.random() < 0.8
            ctx.set(dut.in_tvalid, 1 if valid_now else 0)
            if valid_now:
                ctx.set(dut.in_tdata, packet[i])
                ctx.set(dut.in_tlast, 1 if i == len(packet) - 1 else 0)
        else:
            valid_now = False
            ctx.set(dut.in_tvalid, 0)
        out_ready = rng.random() < 0.8
        ctx.set(dut.out_tready, 1 if out_ready else 0)

        # Pre-edge snapshot: what the DUT sees this cycle.
        accepted = valid_now and ctx.get(dut.in_tready)
        out_beat = out_ready and ctx.get(dut.out_tvalid)
        if out_beat:
            out.append(ctx.get(dut.out_tdata))
        strobed = ctx.get(dut.result_valid)
        if strobed:
            result = (
                ctx.get(dut.result_verdict),
                ctx.get(dut.result_error),
                tuple((ctx.get(dut.md_out) >> (16 * k)) & 0xFFFF for k in range(8)),
            )
            ctx.set(dut.in_tvalid, 0)
            await ctx.tick()
            verdict, error, md_res = result
            frame = bytes(out) if verdict == VERDICT_SENT else None
            if verdict != VERDICT_SENT:
                assert not out, "non-sent packet produced output bytes"
            return verdict, error, md_res, frame

        await ctx.tick()
        if accepted:
            i += 1
    raise TimeoutError("core did not strobe a result")


def _first_diff(a: bytes, b: bytes) -> int | None:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def test_siit_vectors_cosim():
    """Every committed SIIT vector, streamed through NanukCore, diffed
    against the chained ISS oracle -- and, for `sent` vectors, against the
    vector's own recorded output frame."""
    vectors = load_vectors()
    assert vectors, "sanity: there must be at least one committed vector"

    dut = NanukCore(max_frame=2048)
    rng = random.Random(0)

    async def bench(ctx):
        await load_core(ctx, dut)
        for vec in vectors:
            packet = bytes.fromhex(vec["in"])
            got = await run_packet(ctx, dut, packet, rng)
            want = oracle(packet)
            name = vec["name"]
            assert got[0] == want[0], f"{name}: verdict got {got[0]} want {want[0]}"
            assert got[1] == want[1], (
                f"{name}: error got {got[1]:#x} want {want[1]:#x}"
            )
            assert got[2] == want[2], f"{name}: md got {got[2]} want {want[2]}"
            if got[3] != want[3]:
                diff = _first_diff(got[3] or b"", want[3] or b"")
                raise AssertionError(
                    f"{name}: frame mismatch, first differing byte {diff}\n"
                    f"  got  {got[3] and got[3].hex()}\n"
                    f"  want {want[3] and want[3].hex()}"
                )
            if vec["verdict"] == "sent":
                vec_out = bytes.fromhex(vec["out"])
                if got[3] != vec_out:
                    diff = _first_diff(got[3] or b"", vec_out)
                    raise AssertionError(
                        f"{name}: RTL frame diverges from committed vector "
                        f"`out`, first differing byte {diff}\n"
                        f"  rtl {got[3] and got[3].hex()}\n"
                        f"  vec {vec_out.hex()}"
                    )

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()
