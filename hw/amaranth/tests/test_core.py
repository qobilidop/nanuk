"""NanukCore cosim: the streaming face against the chained ISS oracle.

Each packet is streamed in with randomized valid gaps and drained with
randomized ready gaps; the oracle is run_pp_iss -> (gate) -> run_map_iss
with the PP's md output seeding the MAP — the shared-window pass-through
in software form. The whole external contract is compared: verdict,
error (stage+code), md_out, and the output frame bytes.
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
from nanuk.testkit.testkit import (
    DMAC,
    NO_TABLE,
    demo_flood_table,
    demo_l2_table,
    demo_tun_table,
)

from nanuk_amaranth.core import (
    CORE_ERR_OVERFLOW,
    STAGE_CORE,
    STAGE_MAP,
    STAGE_PP,
    VERDICT_DROP,
    VERDICT_ERROR,
    VERDICT_SENT,
    NanukCore,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"

_MAX_RUN_CYCLES = 200_000


def _words(prog: bytes) -> list[int]:
    return [int.from_bytes(prog[i : i + 4], "big") for i in range(0, len(prog), 4)]


def oracle(pp_prog, map_prog, packet, tables, md_in):
    """The chained ISS: (verdict, error{stage,code}, md, frame|None)."""
    pp = run_pp_iss(pp_prog, packet, md_in)
    if pp.verdict != PP_ACCEPT:
        verdict = VERDICT_DROP if pp.verdict == PP_DROP else VERDICT_ERROR
        error = 0 if pp.verdict == PP_DROP else (STAGE_PP << 4) | pp.error
        return verdict, error, tuple(pp.md), None
    mp = run_map_iss(map_prog, packet, pp, tables, pp.md)
    if mp.verdict == 0:
        return VERDICT_SENT, 0, tuple(mp.md), mp.frame
    if mp.verdict == 1:
        return VERDICT_DROP, 0, tuple(mp.md), None
    return VERDICT_ERROR, (STAGE_MAP << 4) | mp.error, tuple(mp.md), None


async def _drive_ctrl(ctx, dut, sel, addr, data):
    ctx.set(dut.ctrl_sel, sel)
    ctx.set(dut.ctrl_addr, addr)
    ctx.set(dut.ctrl_data, data)
    ctx.set(dut.ctrl_we, 1)
    await ctx.tick()
    ctx.set(dut.ctrl_we, 0)


async def load_core(ctx, dut, pp_prog, map_prog, tables):
    for addr, w in enumerate(_words(pp_prog)):
        await _drive_ctrl(ctx, dut, 0, addr, w)
    for addr, w in enumerate(_words(map_prog)):
        await _drive_ctrl(ctx, dut, 1, addr, w)
    for tid, table in enumerate(tables):
        await _drive_ctrl(
            ctx, dut, 2, tid, (table.action_width << 8) | table.key_width
        )
        for key, action in table.entries.items():
            await _drive_ctrl(ctx, dut, 3, tid, key)
            await _drive_ctrl(ctx, dut, 3, (1 << 15) | tid, action)


async def run_packet(ctx, dut, packet, md_in, rng):
    """Stream one packet in/out; returns (verdict, error, md, frame|None).

    All sampling happens pre-edge: the DUT acts on the values present
    before each tick, so acceptance/capture must be judged on the same
    pre-tick snapshot the DUT saw."""
    md = 0
    for k, v in enumerate(md_in):
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
                tuple(
                    (ctx.get(dut.md_out) >> (16 * k)) & 0xFFFF
                    for k in range(8)
                ),
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


def run_cases(pp_prog, map_prog, tables, cases, max_frame=2048, seed=0):
    """cases: list of (packet, md_in). Compares core vs oracle for each."""
    dut = NanukCore(max_frame=max_frame)
    rng = random.Random(seed)

    async def bench(ctx):
        await load_core(ctx, dut, pp_prog, map_prog, tables)
        for packet, md_in in cases:
            got = await run_packet(ctx, dut, packet, md_in, rng)
            want = oracle(pp_prog, map_prog, packet, tables, md_in)
            assert got[0] == want[0], f"verdict: got {got[0]} want {want[0]}"
            assert got[1] == want[1], f"error: got {got[1]:#x} want {want[1]:#x}"
            assert got[2] == want[2], f"md: got {got[2]} want {want[2]}"
            assert got[3] == want[3], (
                f"frame: got {got[3] and got[3].hex()} "
                f"want {want[3] and want[3].hex()}"
            )

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()


def _progs():
    pp = pp_assemble((EXAMPLES / "l2l3l4" / "parse.asm").read_text())
    l2fwd = map_assemble((EXAMPLES / "map_l2fwd" / "fwd.asm").read_text())
    ttl = map_assemble((EXAMPLES / "map_ttl" / "fwd.asm").read_text())
    return pp, l2fwd, ttl


L2_TABLES = [demo_l2_table(both=True), NO_TABLE, NO_TABLE, demo_flood_table()]


def _eth_ipv4_udp(dst=DMAC, ttl=64, payload=b"hi"):
    from scapy.layers.inet import IP, UDP
    from scapy.layers.l2 import Ether

    return bytes(Ether(dst=dst) / IP(dst="10.0.0.2", ttl=ttl) / UDP() / payload)


def test_core_l2fwd_hit_miss_and_gate():
    pp, l2fwd, _ = _progs()
    cases = [
        (_eth_ipv4_udp(), [0]),                       # FDB hit -> port 2
        (_eth_ipv4_udp(dst="02:00:00:00:00:99"), [1]),  # miss -> flood
        (b"\x00" * 10, [2]),                          # runt: PP drops, gated
    ]
    run_cases(pp, l2fwd, L2_TABLES, cases)


def test_core_ttl_rewrite_and_expiry():
    pp, _, ttl = _progs()
    cases = [
        (_eth_ipv4_udp(ttl=64), [0]),   # checksum rewrite via CSUM sequence
        (_eth_ipv4_udp(ttl=1), [3]),    # expired -> drop
    ]
    run_cases(pp, ttl, L2_TABLES, cases)


def test_core_tunnel_push_pop():
    pp_l2l3l4 = pp_assemble((EXAMPLES / "l2l3l4" / "parse.asm").read_text())
    pp_tunnel = pp_assemble(
        (EXAMPLES / "nanukproto" / "parse_tunnel.asm").read_text()
    )
    push = map_assemble(
        (EXAMPLES / "nanukproto" / "tunnel_push.asm").read_text()
    )
    pop = map_assemble((EXAMPLES / "nanukproto" / "tunnel_pop.asm").read_text())
    push_tables = [NO_TABLE, demo_tun_table(), NO_TABLE, demo_flood_table()]
    pop_tables = [NO_TABLE, NO_TABLE, NO_TABLE, demo_flood_table()]

    inner = _eth_ipv4_udp()
    run_cases(pp_l2l3l4, push, push_tables, [(inner, [0])])

    # The pop leg consumes the oracle-pushed frame (delta +22 -> -22).
    pushed = oracle(pp_l2l3l4, push, inner, push_tables, [0])
    assert pushed[0] == VERDICT_SENT and len(pushed[3]) == len(inner) + 22
    run_cases(pp_tunnel, pop, pop_tables, [(pushed[3], [1]), (inner, [2])])


def test_core_tail_passthrough():
    pp, l2fwd, _ = _progs()
    long_pkt = _eth_ipv4_udp(payload=bytes(range(256)) + bytes(60))
    assert len(long_pkt) > 256
    run_cases(pp, l2fwd, L2_TABLES, [(long_pkt, [0])])


def test_core_map_error_stage_nibble():
    # A MAP program that LDMDs slot 9: illegal -> stage 1, code 3.
    pp, _, _ = _progs()
    bad = map_assemble("    ldmd r0, 9\n    drop\n")
    dut_cases = [(_eth_ipv4_udp(), [0])]
    run_cases(pp, bad, L2_TABLES, dut_cases)
    want = oracle(pp, bad, dut_cases[0][0], L2_TABLES, [0])
    assert want[0] == VERDICT_ERROR and want[1] == (STAGE_MAP << 4) | 3


def test_core_frame_overflow():
    pp, l2fwd, _ = _progs()
    dut = NanukCore(max_frame=256)
    rng = random.Random(7)
    oversize = bytes(300)
    ok_pkt = _eth_ipv4_udp()

    async def bench(ctx):
        await load_core(ctx, dut, pp, l2fwd, L2_TABLES)
        got = await run_packet(ctx, dut, oversize, [5], rng)
        assert got[0] == VERDICT_ERROR
        assert got[1] == (STAGE_CORE << 4) | CORE_ERR_OVERFLOW
        assert got[2][0] == 5  # md_in snapshot
        assert got[3] is None
        # The core recovers: a normal packet right after still works.
        got2 = await run_packet(ctx, dut, ok_pkt, [0], rng)
        want2 = oracle(pp, l2fwd, ok_pkt, L2_TABLES, [0])
        assert got2 == want2

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()


def test_core_back_to_back_packets():
    pp, l2fwd, _ = _progs()
    cases = [
        (_eth_ipv4_udp(), [0]),
        (_eth_ipv4_udp(dst="02:00:00:00:00:99"), [1]),
        (_eth_ipv4_udp(), [2]),
        (b"\x00" * 10, [3]),
        (_eth_ipv4_udp(dst="aa:bb:cc:dd:ee:02"), [0]),
    ]
    run_cases(pp, l2fwd, L2_TABLES, cases, seed=42)
