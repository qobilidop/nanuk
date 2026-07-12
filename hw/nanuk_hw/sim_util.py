"""pysim driver for NanukCore: load program/packets, run, snapshot outputs.

Used by the unit tests and the cosim rig (and later by the switch tests).
"""

from dataclasses import dataclass

from amaranth.sim import Simulator

from .core import BUF_BYTES, IMEM_WORDS, NanukCore

# A run is at most 256 executed instructions; each takes 2 cycles
# (FETCH + EXEC) plus start/finish overhead.
_MAX_RUN_CYCLES = 2048


@dataclass(frozen=True)
class CoreResult:
    """The core's output contract, plus a register-file peek for unit tests.

    Field names and shapes match nanuk_spec.harness.ParseResult so the cosim
    rig can diff them directly.
    """

    verdict: int
    error: int
    payload_offset: int
    steps: int
    hdr_present: list[int]
    hdr_offset: list[int]
    smd: list[int]
    regs: list[int]  # r0-r3 at halt (RTL-only observability aid)


def _to_words(prog) -> list[int]:
    """Program as a list of 32-bit words, from bytes (big-endian, as emitted
    by nanuk_isa.asm) or an iterable of ints."""
    if isinstance(prog, (bytes, bytearray)):
        if len(prog) % 4:
            raise ValueError("program byte length must be a multiple of 4")
        return [
            int.from_bytes(prog[i:i + 4], "big") for i in range(0, len(prog), 4)
        ]
    return list(prog)


def _snapshot(ctx, dut: NanukCore) -> CoreResult:
    hp = ctx.get(dut.hdr_present)
    ho = ctx.get(dut.hdr_offset)
    smd = ctx.get(dut.smd)
    return CoreResult(
        verdict=ctx.get(dut.verdict),
        error=ctx.get(dut.error),
        payload_offset=ctx.get(dut.payload_offset),
        steps=ctx.get(dut.steps),
        hdr_present=[(hp >> i) & 1 for i in range(16)],
        hdr_offset=[(ho >> (16 * i)) & 0xFFFF for i in range(16)],
        smd=[(smd >> (16 * i)) & 0xFFFF for i in range(8)],
        regs=[ctx.get(r) for r in dut.regs],
    )


def run_core(prog, packets, *, plens=None) -> list[CoreResult]:
    """Run each packet through one NanukCore instance (program loaded once;
    `start` between packets exercises the architectural-state clear).

    packets: list of byte strings. plens: optional per-packet override of the
    `plen` input (defaults to len(packet)). Returns one CoreResult per packet.
    """
    words = _to_words(prog)
    if len(words) > IMEM_WORDS:
        raise ValueError("program does not fit in imem")
    packets = [bytes(p) for p in packets]
    if plens is None:
        plens = [len(p) for p in packets]

    dut = NanukCore()
    results: list[CoreResult] = []

    async def bench(ctx):
        # Load the program through the imem write port.
        ctx.set(dut.prog_we, 1)
        for addr, w in enumerate(words):
            ctx.set(dut.prog_addr, addr)
            ctx.set(dut.prog_data, w)
            await ctx.tick()
        ctx.set(dut.prog_we, 0)

        for packet, plen in zip(packets, plens):
            # Fill the whole buffer: packet bytes then zero padding, exactly
            # like the golden harness's zeroed buffer (start does not clear
            # the packet buffer, so stale bytes must be overwritten).
            buf = packet[:BUF_BYTES].ljust(BUF_BYTES, b"\x00")
            ctx.set(dut.pkt_we, 1)
            for addr in range(BUF_BYTES):
                ctx.set(dut.pkt_addr, addr)
                ctx.set(dut.pkt_data, buf[addr])
                await ctx.tick()
            ctx.set(dut.pkt_we, 0)

            ctx.set(dut.plen, plen)
            ctx.set(dut.start, 1)
            await ctx.tick()
            ctx.set(dut.start, 0)

            for _ in range(_MAX_RUN_CYCLES):
                if ctx.get(dut.done):
                    break
                await ctx.tick()
            else:
                raise TimeoutError("core did not assert done")
            results.append(_snapshot(ctx, dut))

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()
    return results


def run_one(prog, packet=b"", *, plen=None) -> CoreResult:
    """Single-packet convenience wrapper around run_core."""
    return run_core(prog, [packet], plens=None if plen is None else [plen])[0]
