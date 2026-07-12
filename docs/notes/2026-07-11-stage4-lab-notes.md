# Lab notes — 2026-07-11 — stages 1, 2, 4 and the e2e demo

Decision records and lessons from the build-out sprint. Raw material for the
guide; terse by design.

## Sequencing: demo-first

Stages 2 (eDSL) and 3 (IR) were deferred to go straight from stage 1 to the
stage-4 SimBricks demo — neither is on the demo's critical path (the demo
consumes stage-1 artifacts: ISA, golden model, assembler). Stage 2 landed
in parallel with stage-4 debugging anyway. Lesson: the stage list is a
dependency graph, not a schedule.

## The dumb forwarder lives in the glue, not in RTL

Planned: an Amaranth `nanuk_switch` (AXI-ish streams, frame buffers,
arbiter) around the core. Killed after reading SimBricks' Menshen
integration: the component's C++ side already buffers whole frames per
port, so it can drive the core's packet-load port directly — poke bytes,
pulse start, read verdict, flood from its own buffer. The parser stays
100% RTL and cosim-verified; the deliberately-dumb forwarding policy
(flood-if-accept) is harness, and lives wherever the harness is cheapest.
A streaming RTL shell becomes necessary only for the silicon stage.

## Sequential EXT: when the "simple" RTL is the wrong RTL

First core implemented EXT as a combinational extraction over a flattened
2048-bit view of the packet buffer. Two failures followed:

1. **Toolchain**: Verilator's generated C++ for that datapath was so large
   that gcc crashed (ICE/OOM) compiling it under emulation — across -O3,
   -O1, -O0, output splitting, and reduced parallelism. The SimBricks
   image's Verilator 4.038 made it worse (weak function splitting), but
   even Verilator 5 output crashed the emulated compiler.
2. **Honesty**: no real parser reads 2048 bits combinationally.

Fix: EXT reads up to 9 bytes sequentially from a 256×8 memory (issue /
capture cycles) — exactly the Sail `read_pkt_bits` algorithm laid out in
time. Verilog dropped 75k → 5.2k lines; compiles anywhere; architectural
contract and step counts unchanged, so the existing 29 unit tests + full
cosim re-verified the change with zero test edits. Lesson: the golden-model
rig is what makes RTL restructuring cheap; and "matches the spec's
*algorithm*, not just its results" tends to be the buildable design.

## Cross-compile split: verilate native, compile emulated

Verilation is fast and version-sensitive; C++ compilation of the result is
slow and portable. So: Verilator 5 runs natively in the Nanuk devcontainer
(arm64), and only the generated portable C++ is compiled inside the amd64
SimBricks container (with the Verilator include tree copied alongside).
Deterministic, and ~10× faster than verilating under emulation.

## SimBricks integration facts worth remembering

- `python -m simbricks.local <exp.py> --repo /simbricks` is the fully-local
  path in the current (cloud-first) SimBricks; experiment scripts export
  module-level `instantiations`, and the runtime JSON-round-trips the
  simulation — custom orchestration classes are a trap; overriding
  `SwitchNet._executable` (serialized) with a wrapper script is not.
- `sims/net/switch/net_switch.cc` has the current port/connect API;
  `sims/net/menshen/menshen_hw.cc` has the clocked-Verilator loop; the
  component argv contract is `-S <sync> -E <lat> [-u] -s <sock>...`.
- The demo: 2 QEMU hosts + i40e NICs + Nanuk switch; 10/10 pings through
  the RTL (24 frames in, 24 forwarded); with `examples/drop_all/parse.asm`
  loaded instead, traffic stops — the parser program, not the wiring, is
  what forwards.

## Sail lessons (stage 1)

- Flow typing can't see through `let` globals — asserts need literal bounds.
- Hex literals are bitvectors, not ints (`unsigned(pc) == 0x42` won't check).
- The C backend dead-code-eliminates API functions unless `--c-preserve`d;
  a model without `main` still generates a `model_main` that references
  `zmain` even under `--c-no-main` (provide a stub).

## eDSL shape that fell out (stage 2)

Header declarations + parse-graph states; the ISA's v0 limits surface as
compile errors (16-bit dispatch constants, shift-only arithmetic, 4
registers, extract-behind-cursor). The compiled demo is 37 words vs. 33
hand-written, behaviorally identical over the corpus — the price of a
compiler is four instructions.

## First PPA data point (yowasp-yosys, generic synth, no ABC)

`nanuk_core` (full config): ~80k generic cells; 35,652 flip-flops of which
34,816 are the two memories (imem 1024×32 = 32,768 + pktmem 256×8 = 2,048)
— the instruction memory alone is 94% of all state. The nano (Tiny Tapeout)
configuration therefore lives or dies on shrinking imem (e.g. 64–128 words)
and/or mapping the buffers to RAM macros rather than flops. Logic (~44k
mux/and) is dominated by memory read muxing, which real RAM ports also
eliminate. The wasm yosys build's ABC hangs on this design; generic-cell
stats were enough for the sizing conclusion.

## Beat 3 and the fuzzing dividend

Beat 3 (examples/nanukproto): an invented L2.5 tenant tunnel — one Header
declaration + three eDSL states grafted onto the standard program; tunneled
IPv4/UDP and even VLAN-inside-tunnel parse with correct inner offsets, bad
magic/version drop, plain traffic untouched. 6/6 on the golden model.

Differential fuzzing (hw/tests/test_fuzz.py) fell out of total semantics
almost for free: any word sequence is a valid program (worst case a defined
error halt) and the watchdog bounds every run, so random programs — both
field-randomized well-formed instructions and arbitrary bit patterns — need
no validity filtering at all. 90 program/packet pairs diffed emulator vs
RTL on the full contract, all agreeing.
