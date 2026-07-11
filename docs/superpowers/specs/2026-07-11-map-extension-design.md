# nanuk — Match-Action Extension Design

**Date:** 2026-07-11
**Status:** COMPLETE (2026-07-11) — M1 (spec/emulator/assembler/rig), M2 (RTL cosim + SimBricks table-is-the-policy beats), and M3 (eDSL/IR/interp/playground) all landed green. The MAT arc is done; deferrals live in the parked table below. The detailed MAP ISA v0 (mnemonics, encodings, table sizing) is a follow-up design doc, as [Parser ISA v0](2026-07-11-parser-isa-v0-design.md) was for the parser.

## Decision

Extend nanuk from a programmable parser to a parser + match-action pipeline, as a **full vertical** (spec → golden model → RTL → system demo → language), run **before** any satellite work. Motivations, all live: story completeness (a parser alone isn't a packet processor), demo realism (programmable forwarding instead of parser-gated flooding), design appetite (MAT is the next real ISA-design problem), and external strength (a full pipeline makes the eventual paper/book better).

This consciously lifts the scope freeze for one named item. Everything else stays parked. MLIR simultaneously drops from "queued learning goal" to "parked, unconvinced it belongs in the repo" — see the satellite table in the [project design doc](2026-07-11-nanuk-project-design.md).

## Architecture: two sibling ISA processors

A clean boundary between two engines, each with its own minimal ISA — the xISA structure (its PFE integrates a specialized Parser ISA and a separate, richer MAP ISA; the EZchip NP lineage made the same call with task-optimized cores):

- **PP — parser processor.** Exists. Parser ISA v0 stays **frozen**; no PP changes in this arc.
- **MAP — match-action processor.** New. Its own MAP ISA v0, designed under the same school rules as the parser: 32-bit encodings, small GPR file + zero register (4 as the starting default — the ISA doc revisits if lookup action data demands more), flagless compare-and-branch, step-budget watchdog, all-zeros illegal, total deterministic semantics, and every cut feature parked with a named re-entry trigger.
- **Composition: 1 PP → 1 MAP** in v0. The boundary is a contract, so other compositions (N×M, MAP-only) remain possible on paper without being built.

### Why sibling ISAs, not one ISA

1. **An ISA is the interface contract for a stage's job, and the jobs differ in shape.** Parsing is read-only scanning (cursor discipline, extract/advance, classify); match-action is decide-and-edit (table lookup as a latency event, arithmetic on action data, packet writes, send/drop). One ISA must carry the union; every stage then hauls dead capability.
2. **In a pipeline, hardware cost is per-instance.** Every stage runs every packet; unused instructions are area and complexity multiplied by instance count. The classic pro-one-ISA argument — amortizing a software ecosystem — barely applies to tiny per-stage firmware.
3. **Verification scales with ISA surface per model.** Two small total-semantics Sail models beat one union model where parser-only instructions need defined (or gated) behavior in MAP context.
4. **Pedagogy.** The project's thesis is "shape an ISA to a job." Doing it twice, for two genuinely different jobs, is the curriculum.

Honest counterexample: Intel IXP / Netronome NFP — one microengine ISA, a pooled sea of identical cores. That design trades stage fit for pooling flexibility and is notoriously hard to program; nanuk is a staged pipeline with distinct roles, not a pooled many-core.

What the siblings **share** is the design language and infrastructure: encoding style, register conventions, step budget, Sail build/CI machinery, assembler framework, eDSL/IR patterns, and the four-rung conformance ladder.

### Why no third engine (deparser / modifier)

- PISA needs a deparser because its parser rips headers into a PHV; the deparser is the tax on that representation. nanuk chose zero-copy offsets+SMD — nothing is disassembled, so nothing needs reassembly. **No deparser, by construction.**
- The EZchip-style separate modifier (TOPmodify) is a throughput/pipelining decision, not a semantic one. At nanuk's scale (1 PP + 1 MAP, run-to-completion, educational), modification folds into the MAP — which is exactly what xISA does ("flexible editing of packet headers" by the MAP program; a fixed-function MAC editor applies send-time commands).
- **Length-changing edits use the headroom trick, never a splice.** xISA evidence: `SENDOUT`/`SENDDATA` carry a signed 9-bit FrameDelta (start-of-packet = FOF − FrameDelta at transmit); `LBALLOC`/`FREBASE` prepend whole buffers in 32B-flit granularity; no "insert N bytes at offset X" instruction exists. Rationale from first principles: the payload never enters the core (only a ≤256B header window); every instruction stays O(1) — a mid-packet splice would make per-packet compute proportional to frame length; real length changes are header push/pop at the head (VLAN/MPLS/tunnel encap-decap) while mid-packet edits are same-length overwrites; physical byte movement belongs to streaming egress hardware where it is nearly free. This is the same doctrine as Linux `skb_push`/`skb_pull` and DPDK mbuf headroom, promoted into ISA semantics — and it aligns with nanuk's bounded-work-by-construction principle.

nanuk MAP v0 adopts: **in-place overwrite + signed head-delta at send**. In our single-buffer setting: a headroom region before the parsed frame, write instructions into the header window and headroom, and a `SEND` carrying a signed head delta.

## Composition contract (first deliverable of the arc)

1. **PP → MAP handoff.** Exactly what the parser already emits — verdict, header offsets, SMD — with packet bytes staying put (zero-copy). The parser's reserved `next-stage` verdict gets its consumer. Open sub-question for the ISA doc (leaning yes — cheap, enables good programs): PP passes a few registers of user metadata through to the MAP, as xISA does.
2. **MAP → egress.** Send-to-port with signed head-delta, or drop. This verdict replaces the hardcoded policy of the C++ flood forwarder in the SimBricks glue.
3. **Control plane → tables.** Tables are architectural state, not program text — programmed through a separate control interface (v0: harness/testbench writes; SimBricks: the same mechanism that loads programs today).
4. **Reparse loop (MAP → PP).** Parked; trigger: a decap-then-reparse use case that in-MAP parsing can't handle cleanly.

## MAP v0 scope

**The razor:** as simple as possible; expressive enough for the three demo programs. The ISA carries exactly what they need and nothing more.

### The three demo programs (expressiveness gate)

1. **L2 forward.** Exact-match lookup on destination MAC → egress port; miss → flood or drop (program's choice). Upgrades the SimBricks demo to real programmable forwarding; the live-reprogram beat becomes moving a MAC to a different port mid-ping.
2. **Header rewrite.** TTL decrement + in-place field rewrite (MAC swap or DSCP mark) — exercises read-modify-write and minimal ALU.
3. **nanukproto tunnel push/pop.** MAP encapsulates/decapsulates the invented protocol via headroom writes + head-delta at send — length-changing edits, and a real data-plane ending for the beat-3 story.

### Instruction families forced by the gate

(Families, not final mnemonics — the MAP ISA v0 doc freezes those.)

- **Header window read/write** — EXT-like read at offset; ST-like write at offset, including into headroom.
- **Table lookup** — `LOOKUP table, key-regs → hit/miss + action-data regs`. **Synchronous** in v0; xISA's async+LFLAG machinery is a latency-hiding accelerator, parked (trigger: RTL pipelining makes lookup latency hurt).
- **Minimal ALU** — ADD/SUB (possibly just ADDI); the parser got away with none, TTL decrement means the MAP can't.
- **Control flow** — BEQ/BNE/JMP, inherited from the parser school.
- **Terminate** — SEND (port, signed head-delta) and DROP.

### Tables v0

Exact-match only; fixed key width per table; small and few (sized concretely in the ISA doc). LPM parked (trigger: an IP-routing demo). Ternary/ACL parked (trigger: an ACL demo). Data-plane learning — MAP writing its own tables — parked (trigger: a learning-switch demo).

## Staging

Mirrors the parser's arc, including its demo-first lesson (RTL before language worked last sprint, and the demo is where the realism motivation lives):

- **M1 — spec + golden model.** MAP ISA v0 design doc → Sail spec → generated emulator → assembler + rig. Golden-model composition = PP emulator output piped into the MAP emulator; the pcap rig grows a second stage and end-to-end cases (packet in → egress verdict out).
- **M2 — RTL + system demo (demo-first).** Amaranth MAP core, cosim vs. Sail, then SimBricks: the flood forwarder in the C++ glue is replaced by the PP→MAP composition; the three demo programs land as demo beats.
- **M3 — language.** eDSL + IR extension: tables, actions, MAP code in nanuk-lang; playground grows MAP support. Re-check "IR closed under optimization" against table/action ops. The IR interpreter's cost-model mirroring extends to MAP lowering.

## Testing

The existing four-rung ladder, extended not reinvented: (1) Sail cosim per-engine and composed; (2) pcap differential with the corpus growing MAP cases; (3) SimBricks e2e; (4) PPA on the composed core. Differential fuzzing extends to the composed pipeline. Encodings frozen in the M1 plan doc, as stage 1 did.

## Parked in this arc (with re-entry triggers)

| Item | Trigger |
|---|---|
| Async lookup (LFLAG-style) | RTL pipelining makes lookup latency hurt |
| LPM tables | IP-routing demo |
| Ternary/ACL tables | ACL demo |
| Data-plane learning | Learning-switch demo |
| Reparse loop (MAP → PP) | Decap-then-reparse a single MAP program can't express |
| Multiple MAP cores | Throughput story |
| Separate modifier engine | Pipelining for throughput |
| Mid-packet splice instructions | A workload that genuinely isn't head-push/pop (none known) |
| PP ISA changes | Never in this arc; PP v0 is frozen |

## References

- xISA white paper (Xsight Labs, `TD-402-00 Rev 1.00`, March 2025): PFE overview (Parser + MAP complex), MAP Programming Model (LFLAG, dependency checker, `SENDOUT`/`SENDDATA`/`DROP`, FrameDelta, `LBALLOC`/`FREBASE`), Packet Reparse. Local copy: `.agent_scratch/XISA_Public.pdf`.
- EZchip NP lineage (TOPparse / TOPsearch / TOPresolve / TOPmodify) — task-optimized cores precedent.
- Intel IXP / Netronome NFP — the one-ISA pooled-cores counterexample.
- RMT/PISA (Bosshart et al., SIGCOMM '13) — the deparser-as-PHV-tax comparison.
