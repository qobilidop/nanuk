# nanuk — Project Design

**Date:** 2026-07-11
**Status:** Approved scope for the main track; satellite tracks defined with entry criteria. Scope frozen — new ideas go to *Parked* by default.

## Thesis

nanuk is an educational project that builds a **programmable packet processor from chip to programming language** — the entire vertical stack, small enough that one person can understand every layer, real enough that the final demo is unmodified Linux hosts exchanging traffic through your own RTL.

- **Parser-first.** Packet parsing is the most self-contained piece of a packet processor: crisp input (bytes), crisp output (extracted headers + a verdict), and genuinely interesting to make programmable. The full processor (match-action, deparser, traffic manager) comes later or not at all.
- **ISA-based, not PISA-based.** The parser is a tiny programmable processor with its own instruction set, in the spirit of Xsight Labs' open xISA — not a P4/PISA parse-graph abstraction. Rationale: (1) xISA proves real silicon works this way; (2) the ISA route unlocks mature tooling (Sail, RISC-V-style conformance methodology, Isla); (3) an ISA is the truthful layer — real chips implement parse graphs on programmable parser engines anyway; (4) PISA/P4 can sit *on top* later as a frontend, turning BMv2 into a differential-testing oracle rather than a competitor.
- **General across switch and NIC.** Nothing in the ISA or contract assumes one or the other. (The system-level demo uses the switch shape because it avoids PCIe/DMA/driver work.)
- **Eventually a book and course.** Deferred. The only concession now: decision records and lab notes as we go.

## The external contract

A *programmable packet processor* here means: a device whose function *packets in → packets out (+ metadata/verdict)* is determined by a program loaded after fabrication.

For the parser specifically: **raw bytes in → extracted header vector + verdict (accept / drop / next-stage) + payload offset out**, plus a program-load mechanism. Pinning this contract down precisely (packet-side interface, header-vector format, verdict encoding) is a first-class early deliverable — every test harness, the dumb forwarder, and the SimBricks glue consume it.

## Architecture: the layer cake

Each layer is a stable contract with exactly one owner of its truth:

| Layer | Artifact | Home |
|---|---|---|
| Frontend | Python eDSL (reference frontend; P4 subset possible later) | `lang/` |
| Middle | **nanuk IR** — protobuf, ONNX-style interchange, *extracted* from the working eDSL | `compiler/` |
| ISA | Assembly text + binary encoding — **encodings defined solely by the Sail spec**; one shared assembler | `spec/` |
| Golden model | Sail ISA spec + generated emulator — the single source of truth for semantics | `spec/` |
| RTL | Amaranth (canonical implementation), cosimulated against Sail | `hw/` |
| System | SimBricks: QEMU/gem5 hosts + Verilator'd nanuk switch + ns-3/OMNeT++ | `hw/`, `examples/` |
| Silicon | Tiny Tapeout capstone | `hw/` |

Two portable interchange boundaries: the **IR above** (parser-level semantics: extract field, branch on EtherType, emit header vector — before instruction selection) and **assembly/binary below** (defined by Sail). The compiler lives between them. The IR is the **hub** all satellite tracks hang off.

### Key architecture decisions

- **HDL: Amaranth.** The whole stack stays in Python; concise FSM/datapath code; emits Verilog for Verilator, SimBricks, OpenLane, and Tiny Tapeout. Best-in-class among Python HDLs (MyHDL stagnant; PyRTL/PyMTL3 academic). Hand-written SystemVerilog becomes the first *port* exercise, making it a comparison chapter rather than the main narrative.
- **IR: protobuf, extracted not speculated.** IRs designed before their consumers exist tend to be wrong. Stage 2 builds eDSL → assembly monolithically; stage 3 hardens the eDSL's internal representation into the public `.proto`. The ONNX analogy is deliberate — serializable, language-neutral, N frontends × M tools — but at parser-semantics altitude, since the ISA already provides the lower interchange boundary.
- **MLIR: a spoke, not infrastructure.** The main pipeline lowers IR → assembly directly in Python, no LLVM/C++ anywhere in the main build. MLIR is a satellite (see below) that round-trips through the protobuf IR, exactly as ONNX-MLIR / Torch-MLIR / StableHLO do.

## Methodology

1. **Interface contract first.** The parser's external contract is designed before implementation, and designed so a dumb fixed-function forwarder can consume it.
2. **The evaluation ladder defines "done."** Test rigs are built from day one, not at the end:
   1. *Instruction-level conformance* — RTL cosimulated against the Sail-generated emulator (RISC-V-style methodology).
   2. *Program-level differential testing* — same program + same pcap corpus into golden model and implementation; diff header vectors and verdicts.
   3. *System-level end-to-end* — SimBricks full-system simulation; real Linux, real TCP, through nanuk.
   4. *PPA* — Yosys/OpenLane synthesis reports; packets-per-cycle, headers-per-cycle.
3. **Total, deterministic semantics — no "undefined" anywhere.** Every ISA and IR behavior is defined (out-of-bounds reads, over-depth parses, all of it). This is main-track load-bearing: you cannot diff against an oracle that shrugs. It is also what makes the formal-methods satellite cheap.
4. **Bounded iteration by construction.** Parse loops are statically bounded (max parse depth, bounded header stacks à la MPLS/VLAN). Hardware wants this; symbolic execution requires it; the ISA guarantees it.
5. **Single source of truth per layer.** Sail owns semantics *and* instruction encodings (the assembler is its consumer — the MLIR backend emits assembly text and stops there). The IR owns program interchange. The rigs own "done."
6. **v0 minimalism in ISA design.** The smallest ISA that parses Ethernet/VLAN/IPv4/UDP, versioned (`v0.x`), grown only when a demo program cannot be written. xISA is the inspiration, not a spec to clone.
7. **Extract, don't speculate.** Applies to the IR (from the working compiler), the book (from lab notes), and satellite tooling (from proven rigs).
8. **Reproducible environment + CI from day one.** Devcontainer (or Nix) covering the full toolchain (Sail, protoc, Amaranth, Verilator; SimBricks containerized); GitHub Actions running the pcap/conformance suites from stage 1. For a future course, "one command and it builds" is half the pedagogy.
9. **Decision records.** Short ADRs/lab notes per significant decision — raw material for the book, near-zero cost now.

## Main track

Five stages, each independently demoable. Stages 1–3 demo via the pcap differential rig (native, fast, CI-friendly); live end-to-end arrives in stage 4 where its payoff lives.

### Stage 1 — ISA + Sail spec + golden model
Parser ISA v0 sketched (done — see [Parser ISA v0 design](2026-07-11-parser-isa-v0-design.md)), then encoded in Sail. Sail generates the emulator (C backend). Build: assembler (Python), packet I/O harness around the generated emulator (load program, feed pcap bytes, capture header vector + verdict). **Done:** hand-written assembly programs parse Ethernet/VLAN/IPv4/UDP over a pcap corpus in CI.

### Stage 2 — Python eDSL
eDSL → assembly directly, monolithic, no IR yet. **Done:** stage-1 demo programs rewritten in the eDSL, bit-identical rig results.

### Stage 3 — IR extraction
Refactor: the eDSL's internal representation becomes the public protobuf IR; pipeline becomes eDSL → IR → assembly. The IR must be **closed under optimization** (expressive enough for optimized programs, e.g. merged/widened extracts — the MLIR satellite depends on this). **Done:** same rig results through the split pipeline; `.proto` published; satellite entry criteria for MLIR/formal/P4 tracks unlocked.

### Stage 4 — RTL + system demo
Amaranth parser core with **parameterized datapath/memory widths** (full config for SimBricks; shrunk "nano" config targeting a Tiny Tapeout tile). Instruction-level cosim rig vs. Sail. A deliberately dumb fixed-function forwarder (static L2 or simple learning) consumes the header vector — minimal harness, not scope creep; it stays dumb. SimBricks component adapters (Ethernet-channel glue for both the Verilator'd RTL and the emulator as a behavioral component — the in-system A/B swap comes free). **Done:** the three-beat demo below, plus rungs 1–2 of the ladder in CI.

### Stage 5 — Tiny Tapeout capstone
The nano configuration through OpenLane to a Tiny Tapeout submission. The physical demo is necessarily humbler (packets over slow pins/UART; real MAC/SerDes is parked). **Done:** GDS submitted; same conformance suite passing on the gate-level netlist.

## The final demo (stage 4)

One SimBricks configuration: two+ QEMU/gem5 hosts booting unmodified Linux with existing NIC models, the nanuk switch in the middle as Verilator'd RTL — the same Verilog that goes to tape-out — in synchronized deterministic mode (bit-identical runs; honest simulated-time latency numbers regardless of wall-clock). Menshen and Corundum are the integration precedents.

Three beats:
1. **"It's real."** Load the baseline Ethernet/IPv4 parser program (written in the eDSL). `ping`, `iperf`, `tcpdump` between real Linux hosts, through your parser.
2. **"It's programmable."** VLAN traffic drops — show *why* via the parser's verdict. Edit ~10 lines of eDSL, recompile, reload — same silicon, zero RTL changes — VLAN flows.
3. **"It parses protocols that don't exist."** Invent a header (toy tunnel / INT-style field), write a parser program for it, have hosts speak it via scapy, watch nanuk parse and forward a protocol no commercial switch has ever heard of.

Because SimBricks components are swappable, the identical scenario runs against the Sail emulator component and the RTL component; diff the outputs — ladder rungs 2 and 3 in one rig.

*To verify at stage 4 (low-risk):* the current adapter API for a custom multi-port Ethernet Verilator device; whether current SimBricks prefers its Python orchestration for custom components. Note: SimBricks/QEMU/gem5 want Linux — runs containerized on macOS; the pcap rigs run natively everywhere.

## Simulators: generate, reuse, glue

We build **no simulator engines**. Generated: the instruction-level emulator (by Sail). Reused: Verilator, Amaranth's simulator, QEMU/gem5, ns-3/OMNeT++. Built (thin glue, all consuming the same contract): the emulator packet-I/O harness (stage 1), the conformance/cosim rig (stage 4), the SimBricks component adapters (stage 4, ~hundreds of lines of C++ with existing integrations as templates).

## Satellite tracks

Hub-and-spoke around the protobuf IR. Every spoke terminates in the same evaluation machinery. None block the main track; each phase is independently shippable.

| Satellite | What | Entry criterion |
|---|---|---|
| **MLIR — phase A** | nanuk-IR dialect, import/export, optimization passes (dead-extract elimination, extract widening/merging, parse-state dedup, dispatch optimization). Round-trip: IR → MLIR → optimized IR. Correctness: differential pcap testing, optimized vs. unoptimized, through the golden model. | Stage 3 |
| **MLIR — phase B** | Second dialect mirroring the ISA; dialect-conversion lowering (instruction selection / register allocation as rewrites) — an alternate backend. Emits assembly *text*; the shared assembler owns encoding. Payback: differential testing of the mainline Python backend. Isolated build (or separate repo, as ONNX-MLIR is to ONNX); no LLVM in the main build, ever. | Phase A |
| **Formal — symbolic executor** | Python + Z3 interpreter over the IR (p4v / p4-symbolic precedent; tractable because parsers are bounded-loop bitvector programs). Payoffs: path-coverage packet generation (p4pktgen-style — *generates the shared pcap corpus*); safety properties (no read past packet end, no read-before-write in the header vector, unreachable states, depth bounds). | Stage 3 |
| **Formal — translation validation** | Alive2-style per-run validation (Gauntlet precedent): IR→IR for the optimizer; IR→asm for both backends, with **Isla** providing symbolic semantics of the assembly side directly from the Sail spec — no second hand-written semantics. | Symbolic executor |
| **Differential fuzzing** | Random packets (later: random IR programs) diffed across emulator / RTL / backends. ~A day of work once rigs exist; complements the symbolic executor from the opposite direction. | Stage 4 rigs |
| **HDL ports** | Same core, other HDLs — SystemVerilog first — each validated by the unchanged conformance suite. "Same contract, different expression." | Stage 4 |
| **P4 frontend** | P4-subset frontend emitting nanuk IR; BMv2 becomes a differential oracle (same P4 program, compare behaviors). Positions nanuk *under* the P4 ecosystem, not against it. | Stage 3 |
| **Workshop paper** | Short paper on the novel core: an open, Sail-specified parser ISA with generated golden model, conformance methodology, and full open stack — RISC-V-style spec-first engineering for packet engines. Primary targets: EuroP4 (@CoNEXT, CFP ~late summer) or ANRW (most remote-friendly); JOSS as a cheap citable-DOI add-on for the artifact. A HotNets-style position paper or full-stack paper stays in reserve for after the stage-4 demo. Open development continues as-is (public repos are not prior publication; anonymization is a submission-time PDF concern, and venue attendance/remote-presentation policy is checked against the CFP — email chairs early if travel is constrained). | Stage 2–3, timed to a CFP deadline |
| **Book / course** | Distilled from ADRs and lab notes. | Post-capstone |

## Repository layout

```
spec/       Sail ISA spec, generated emulator, assembler, encoding truth
lang/       Python eDSL (reference frontend)
compiler/   nanuk IR (.proto), IR→asm backend; satellite compilers isolated
hw/         Amaranth core, cosim rig, SimBricks glue, Tiny Tapeout config
examples/   Parser programs, pcap corpora, SimBricks scenarios
guide/      Lab notes, ADRs; eventually the book
docs/       Design docs (this file)
```

## Parked

Multiple engines · match-action stage / deparser · traffic manager · real MAC/SerDes · line rate · standalone (non-embedded) language · FireSim · early TAP-interface live demo (~50 lines if morale demands it) · anything not listed above — by default.

## Naming and licensing

**nanuk** — Inuktitut for polar bear; hidden "nano-" prefix; polar bear mascot. Name checked free on PyPI, npm, crates.io, RubyGems, Homebrew, conda-forge, NuGet, Maven, Docker Hub (as of 2026-07). GitHub `nanuk` username taken → repo `qobilidop/nanuk`; org fallbacks `nanuk-project` / `nanuklang`. Real-world collisions (NANUK cases, Czech "nanuk" = popsicle) → always pair the name with a tagline.

**Licenses:** Apache-2.0 for code/RTL · CC-BY-4.0 for the guide.

## References

- [xISA spec (Xsight Labs, MPLv2)](https://xsightlabs.com/wp-content/uploads/2025/03/XISA_Public-.pdf) · [announcement](https://xsightlabs.com/blog/unlocking-the-future-of-programmable-networking-introducing-the-xisa-by-xsight-labs/)
- [SimBricks](https://github.com/simbricks/simbricks) · [SIGCOMM '22 paper](https://dl.acm.org/doi/pdf/10.1145/3544216.3544253)
- [Sail](https://github.com/rems-project/sail) · [Isla](https://github.com/rems-project/isla)
- [Amaranth HDL](https://amaranth-lang.org) · [Tiny Tapeout](https://tinytapeout.com)
- Precedents: p4v (SIGCOMM '18), p4pktgen (SOSR '18), Gauntlet (OSDI '20), Alive2, ONNX-MLIR, Menshen, Corundum
