# Stage 4 (demo-first): Amaranth RTL + SimBricks E2E Demo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `ping` between two simulated Linux hosts through the nanuk parser RTL: Amaranth parser core (cosimulated against the Sail golden model), a parser-gated store-and-forward flood switch, and a SimBricks network component wrapping the Verilator'd switch.

**Sequencing note (deviation from the project design's stage order):** stages 2 (eDSL) and 3 (protobuf IR) are deferred — neither is on the demo's critical path. Demo programs use stage-1 assembly. The eDSL/IR stages slot back in after the demo.

**Architecture:** `hw/` holds the Amaranth core and switch (Python, pysim tests, Verilog export); the cosim rig reuses `spec/python`'s harness to diff RTL vs `nanuk-emu` over the demo corpus; `hw/simbricks/` holds the C++ SimBricks net component and experiment configs, modeled on SimBricks' own `net_switch`/Menshen integrations.

**Tech Stack:** Amaranth ≥0.5 (+builtin-yosys for Verilog export) · pysim · Verilator (added to devcontainer) · SimBricks (their Docker images for QEMU hosts) · pytest.

## Global constraints

- The Sail model stays the single source of truth: the RTL must reproduce the full output contract (verdict, error, payload_offset, hdr_present/offsets, SMD, steps) bit-for-bit over the corpus.
- Total semantics carry over: all five error codes behave identically in RTL.
- Parameterized widths (full config now; nano config later).
- The switch's forwarding policy in v0 is deliberately dumb: **flood-if-accepted, drop otherwise**. DMAC learning is out of scope (SMD slots stay informational for now). This is enough for every demo beat: baseline ping floods; VLAN-unaware program drops VLAN frames (beat 2); custom-protocol program gates its own traffic (beat 3).

## Frozen interface contracts

### nanuk_core (Amaranth component)

Single clock domain. Load program + packet, pulse `start`, wait for `done`, read outputs.

| Signal | Dir | Width | Meaning |
|---|---|---|---|
| `prog_we` / `prog_addr` / `prog_data` | in | 1 / 10 / 32 | imem write port |
| `pkt_we` / `pkt_addr` / `pkt_data` | in | 1 / 8 / 8 | packet buffer write port |
| `plen` | in | 16 | packet length (bytes), registered at `start` |
| `start` | in | 1 | one-cycle pulse; resets arch state (not imem) and runs |
| `done` | out | 1 | level; outputs valid while high; cleared by next `start` |
| `verdict` / `error` | out | 8 / 8 | per state.sail codes |
| `payload_offset` | out | 16 | cursor at halt |
| `steps` | out | 32 | instructions executed |
| `hdr_present` | out | 16 | bitmap, bit i = header i present |
| `hdr_offset` | out | 256 | 16 × 16b, header i at bits [16i+15:16i] |
| `smd` | out | 128 | 8 × 16b, slot i at bits [16i+15:16i] |

Execution: one instruction per cycle (EXT via combinational extraction — fine for sim; PPA later). `start` clears regs/cursor/pc/hdr/smd/status but **not** imem (program persists across packets).

### nanuk_switch (Amaranth component)

`NPORTS = 4`. Store-and-forward, one frame in flight globally (round-robin over ports). Max frame 2048 B.

Per port i: RX stream `rx{i}_valid/rx{i}_data[8]/rx{i}_last/rx{i}_ready`; TX stream `tx{i}_valid/tx{i}_data[8]/tx{i}_last/tx{i}_ready`. Plus the core's program-load port re-exported (`prog_we/prog_addr/prog_data`).

Frame flow: accept one full frame from a port → mirror first min(len, 256) bytes into the core while buffering the whole frame → `start`, wait `done` → verdict accept ⇒ transmit buffered frame on all other ports (sequentially); else discard → next port.

### SimBricks component (`nanuk_net`)

C++ binary modeled on SimBricks `sims/net/net_switch` (their simple switch): one SimBricks Ethernet interface per port, synchronized mode. Event loop: incoming SimBricks eth messages → drive the port's RX stream cycle-by-cycle into the Verilator'd `nanuk_switch`; bytes appearing on TX streams → assemble frames → outgoing eth messages. Program binary loaded into the core via the prog port at init (path from argv). Exact channel API copied from the current SimBricks source at implementation time (recon task).

## Tasks

1. **Plan + toolchain**: this doc; add `verilator` + deps to devcontainer; rebuild image.
2. **`hw/` project + nanuk_core + cosim rig** *(subagent-friendly; self-contained given this contract + spec/model + spec/python)*:
   - `hw/pyproject.toml` (uv; amaranth[builtin-yosys], pytest, path dep on `spec/python` for the harness).
   - `hw/nanuk_hw/core.py` implementing the contract; `hw/tests/test_core.py` (pysim unit tests per instruction, mirroring `spec/test/*.sail` cases).
   - `hw/tests/test_cosim.py`: assemble `examples/l2l3l4/parse.asm`, run the 9-case demo corpus (rebuild packets with scapy exactly as `spec/python/tests/test_demo.py` does) on both `nanuk-emu` and pysim; diff the full output contract. Plus randomized packets (seeded) for the same program.
   - `hw/export.py`: emit `build/nanuk_switch.v` via amaranth.back.verilog (core alone first; switch added by task 3).
3. **nanuk_switch** + pysim tests (frame in → flood out when accepted; drop when program drops; back-to-back frames; oversize guard) + Verilog export + Verilator lint/smoke (C++ testbench pushes one frame through the Verilated model).
4. **SimBricks recon**: clone into `third_party/simbricks` (gitignored), read `sims/net/net_switch` + Menshen integration + orchestration docs; decide build path (their Docker image vs building libsimbricks in our container); write findings into `hw/simbricks/README.md`.
5. **`nanuk_net` component**: C++ glue per recon; build in container; unit-smoke with SimBricks' channel tools if available.
6. **E2E experiment**: SimBricks orchestration config — 2 QEMU hosts (their base image) + 2 NIC sims + `nanuk_net` with the l2l3l4 program; assert ping success from the experiment output. Stretch (beat 2): rerun with an eth-only program and show VLAN-tagged traffic dropping while plain traffic flows.

Done criterion: the experiment run completes with successful pings through the Verilator'd nanuk switch, reproducibly, documented in `hw/simbricks/README.md`.
