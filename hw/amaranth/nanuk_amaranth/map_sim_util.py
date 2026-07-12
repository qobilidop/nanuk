"""pysim driver for MatchActionProcessor: load program/window/tables/ctx, run, snapshot.

Used by the unit tests, the MAP cosim rig, and the composed-pipeline rig.
Mirrors the nanuk-map-emu CLI contract (spec/emulator/map_main.c): the
window is filled per packet (headroom zeros + frame + zero padding — start
does NOT clear it), keys/actions are masked to table widths before poking
(as emu_map_table_add does), and the transmitted frame is read back as
window[32 - delta : 32 + min(plen, 256)) when the verdict is sent.
"""

from dataclasses import dataclass

from amaranth.sim import Simulator

from .map import (
    BUF_BYTES,
    HEADROOM_BYTES,
    IMEM_WORDS,
    VERDICT_SENT,
    WIN_BYTES,
    MatchActionProcessor,
)
from .pp_sim_util import PPResult, run_pp

# One instruction can cost up to ~64 scan cycles (LOOKUP) or ~120 (CSUM);
# 256 instructions bounded well under this.
_MAX_RUN_CYCLES = 65536


@dataclass(frozen=True)
class MAPResult:
    """MatchActionProcessor's outbound contract; field names match map_harness.MatchActionResult
    so the cosim rig can diff them directly (frame includes the >256B tail
    passthrough, same rule as run_map)."""

    verdict: int
    error: int
    egress: int
    delta: int
    steps: int
    frame: bytes | None
    regs: list[int]


def _to_words(prog) -> list[int]:
    if isinstance(prog, (bytes, bytearray)):
        if len(prog) % 4:
            raise ValueError("program byte length must be a multiple of 4")
        return [int.from_bytes(prog[i : i + 4], "big") for i in range(0, len(prog), 4)]
    return list(prog)


def _mask(value: int, width: int) -> int:
    if width <= 0:
        return 0
    if width >= 64:
        return value & ((1 << 64) - 1)
    return value & ((1 << width) - 1)


def run_map(prog, packets, ctxs, tables) -> list[MAPResult]:
    """Run each packet through one MatchActionProcessor instance.

    prog: MAP program (bytes or word list). packets: list of frames.
    ctxs: list of (pp_result, ingress) — pp_result needs .hdr_present,
    .hdr_offset, .smd (ParserResult or PPResult shape). tables: list of
    nanuk.testkit.map_harness.Table, index = table id.
    """
    words = _to_words(prog)
    if len(words) > IMEM_WORDS:
        raise ValueError("program does not fit in imem")
    packets = [bytes(p) for p in packets]

    dut = MatchActionProcessor()
    results: list[MAPResult] = []

    async def bench(ctx):
        ctx.set(dut.prog_we, 1)
        for addr, w in enumerate(words):
            ctx.set(dut.prog_addr, addr)
            ctx.set(dut.prog_data, w)
            await ctx.tick()
        ctx.set(dut.prog_we, 0)

        # Tables persist across packets: config + add once.
        for tid, table in enumerate(tables):
            ctx.set(dut.tbl_cfg_we, 1)
            ctx.set(dut.tbl_cfg_id, tid)
            ctx.set(dut.tbl_cfg_kw, table.key_width)
            ctx.set(dut.tbl_cfg_aw, table.action_width)
            await ctx.tick()
            ctx.set(dut.tbl_cfg_we, 0)
            for key, action in table.entries.items():
                ctx.set(dut.tbl_add_we, 1)
                ctx.set(dut.tbl_add_id, tid)
                ctx.set(dut.tbl_add_key, _mask(key, table.key_width))
                ctx.set(dut.tbl_add_action, _mask(action, table.action_width))
                await ctx.tick()
                ctx.set(dut.tbl_add_we, 0)

        for packet, (pp, ingress) in zip(packets, ctxs):
            # Whole window per packet: headroom zeros + frame + padding.
            win = bytes(HEADROOM_BYTES) + packet[:BUF_BYTES]
            win = win.ljust(WIN_BYTES, b"\x00")
            ctx.set(dut.win_we, 1)
            for addr in range(WIN_BYTES):
                ctx.set(dut.win_addr, addr)
                ctx.set(dut.win_data, win[addr])
                await ctx.tick()
            ctx.set(dut.win_we, 0)

            ctx.set(dut.plen, min(len(packet), 0xFFFF))
            ctx.set(dut.ingress, ingress)
            smd = 0
            for i, v in enumerate(pp.smd):
                smd |= (v & 0xFFFF) << (16 * i)
            ctx.set(dut.smd_in, smd)
            hp = 0
            ho = 0
            for i in range(16):
                if pp.hdr_present[i]:
                    hp |= 1 << i
                ho |= (pp.hdr_offset[i] & 0xFFFF) << (16 * i)
            ctx.set(dut.hdr_present_in, hp)
            ctx.set(dut.hdr_offset_in, ho)

            ctx.set(dut.start, 1)
            await ctx.tick()
            ctx.set(dut.start, 0)

            for _ in range(_MAX_RUN_CYCLES):
                if ctx.get(dut.done):
                    break
                await ctx.tick()
            else:
                raise TimeoutError("MatchActionProcessor did not assert done")

            verdict = ctx.get(dut.verdict)
            delta = ctx.get(dut.delta)
            frame = None
            if verdict == VERDICT_SENT:
                start = HEADROOM_BYTES - delta
                end = HEADROOM_BYTES + min(len(packet), BUF_BYTES)
                out = bytearray()
                for addr in range(start, end):
                    ctx.set(dut.win_rd_addr, addr)
                    await ctx.tick()
                    out.append(ctx.get(dut.win_rd_data))
                # Tail passthrough, same rule as map_harness.run_map: bytes
                # beyond the window never entered the engine's custody.
                frame = bytes(out) + packet[BUF_BYTES:]
            results.append(
                MAPResult(
                    verdict=verdict,
                    error=ctx.get(dut.error),
                    egress=ctx.get(dut.egress),
                    delta=delta,
                    steps=ctx.get(dut.steps),
                    frame=frame,
                    regs=[ctx.get(r) for r in dut.regs],
                )
            )

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()
    return results


def run_map_one(prog, packet, pp, tables, ingress) -> MAPResult:
    """Single-packet convenience wrapper (argument order matches
    nanuk.testkit.map_harness.run_map)."""
    return run_map(prog, [packet], [(pp, ingress)], tables)[0]


def run_pipeline_rtl(
    pp_prog, map_prog, packet, tables, ingress
) -> tuple[PPResult, MAPResult | None]:
    """PP-RTL -> MAP-RTL composition with run_pipeline's gating."""
    pp = run_pp(pp_prog, [bytes(packet)])[0]
    if pp.verdict != 0:
        return pp, None
    return pp, run_map_one(map_prog, packet, pp, tables, ingress)
