# Deparser & packet-editor doctrine

**Date:** 2026-07-12
**Status:** decided (discussion with Bili; no code changes — this records
design doctrine, survey evidence, and parked options with triggers)

## Context

The question arose under a tentative design goal — nanuk.lang as a P4
subset — which Bili retracted the same day after seeing where it leads:
**nanuk.lang optimizes for education, not P4 surface compatibility.**
The lang stays first-principles-shaped around the zero-copy machine; the
P4 relationship is (a) a concept-mapping section in the docs/paper and
(b) the parked P4-frontend satellite, which would translate real P4 to
the IR without contorting nanuk.lang. The IR stays ONNX-style and the
ISAs xISA-subset-shaped as before. The deparser question and its answer
survive the retraction unchanged (they're about the machine, not the
syntax): should nanuk invent a third processor type for deparsing?

## Survey: how real targets handle the P4 deparser

No design anywhere makes the deparser a third programmable processor
with its own ISA. Three recurring forms:

1. **PHV architectures → configured fixed-function serializer.**
   Tofino: field dictionary + packet-occupancy-vector validity bits
   feeding a crossbar (Barefoot patent US10686735) — table-driven, no
   instruction fetch; the deparser also owns checksum-update, mirror,
   resubmit, digest as fixed externs. RMT paper: one sentence
   ("recombines data from the PHV back into each packet"), parser +
   deparser together = 1.3% of chip area. Menshen: deparse config table
   "identical to the parser table". BMv2: hardcoded loop — serialize
   valid headers in emit order, append payload. P4-the-language already
   knows this: the deparser control is restricted to emit sequences.
2. **Zero-copy architectures → compiled away.** p4c's eBPF backend
   compiles a real P4 deparser block into one bpf_xdp_adjust_head /
   bpf_skb_adjust_room call plus byte stores. VPP writes into rewrite
   headroom; Juniper Trio edits the packet head in thread-local memory;
   xISA has no deparser stage at all. No deparser survives as a runtime
   entity in this camp.
3. **The middle form: the packet editor.** Broadcom NPL has no
   "deparser" — its Editor executes add/delete/rewrite-header commands;
   Cisco UADP has a Rewrite Engine; Avago patented a parallel
   command-vector edit engine. Programmable, but via edit scripts
   selected by match results — no PC, no branches.

## Doctrine

- **No third processor type.** The P4 deparser is an artifact of the
  PHV abstract machine (headers detached from bytes, validity bits,
  emit-order reserialization). nanuk deliberately chose the zero-copy
  machine (offsets + SMD, in-place edits, headroom + signed head-delta
  at SEND — the xISA/eBPF/VPP camp), where deparsing degenerates to
  edit ops. This confirms the MAT-arc "no deparser by construction"
  decision with survey evidence.
- **nanuk.lang gets no deparser construct at all** (education-first: no
  construct whose only job is P4 resemblance). The lowering rule
  transfers to the parked P4-frontend satellite: if it ever lands, it
  compiles P4 deparser blocks the p4c-ebpf way — emit order matches
  parsed wire layout → no-op; insertions/removals → headroom stores +
  SEND delta (mechanically what tunnel push/pop do today); true header
  *reordering* → unsupported, a documented deviation. MAP's ST +
  SEND(delta) are exactly the two primitives the eBPF lowering needs.
- **IR**: lower early (lang → existing store/delta ops). Add a
  declarative emit/edit node only when a backend wants the intent
  preserved (see parked editor below).

## The editor question ("why is an edit engine not an ISA sibling?")

The boundary is a spectrum, not a kind. Shipped editors are branch-free
command vectors because (a) by edit time every data-dependent decision
has been made — control flow belongs in parse (data-dependent walk) and
match (lookup dispatch); "decide in match, act in edit"; (b) branch-free
scripts have fixed worst-case latency at line rate; (c) no fetch/branch
machinery. But a branch-free instruction sequence and an edit command
vector differ only in vocabulary — RMT's own VLIW action engine has no
PC either. Fusion is a design choice: xISA and nanuk fuse decide+act in
one MAP ISA.

**xISA precision** (white paper, .agent_scratch/xisa.txt): fusion is at
the *processor* level, not the instruction level — LKP (asynchronous,
LFLAG + SYNC) and store-to-header are separate instruction classes in
one ISA. (nanuk's LOOKUP is more fused in one respect: the miss-branch
is in the instruction.) Consequence, confirmed: **a MAP program's role
is program-defined** — only LKP + branches = pure lookup engine; only
stores + csum + send-delta = pure editor. nanuk's demo corpus already
spans the spectrum: map_l2fwd = pure lookup engine, tunnel_pop = pure
editor (no table access), ttl / tunnel_push = fused. "MAP acting as a
pure edit engine" is a demo (run tunnel_pop), not a hardware project.

## Parked: the unfused editor engine

A real third block — MAP reduced to match/compute, plus an EDIT engine
consuming a descriptor (WRITE(off,len,val) / CSUM_UPDATE / head-delta /
emit command vector) — has genuine industry precedent (NPL Editor, RWE,
Avago patent) and would be the honest hardware target for P4 deparser
blocks. It costs the full nanuk vertical ×1.5 plus a new frozen
interface contract (the descriptor format — the expensive part), and it
deviates from the xISA-subset goal, which fuses. Parked with triggers:

- (a) the P4 frontend satellite wants a literal deparser/editor stage
  to target;
- (b) RTL timing pressure makes separating match latency from edit
  latency worth it;
- (c) a demo program needs edits that don't fit in-place + head-delta
  (true header reordering).

## Public pattern donors (if a trigger fires)

- **Open RTL**: CESNET ndk-fpga `comp/axis_tools/edit/packet_editor/`
  (offset+mask byte-rewrite stage, cocotb tests — the cleanest small
  editor skeleton), `comp/mfb_tools/edit/` frame_extender/trimmer (the
  length-changing edits as a separate datamover), checksum_calculator;
  Menshen `lib_rmt/rmtv2/deparser_top.v` + `sub_deparser.v` (the
  PHV-reassembly school we'd deliberately not choose).
- **Open generators**: luinaudt/deparser (FPGA'21, BMv2 JSON → VHDL);
  p4fpga (Bluespec deparser, frozen 2016); Xilinx/nanotube (XDP → HLS).
- **Firmware**: Netronome/nic-firmware `actions.uc` — public microcode
  action-list interpreter with in-place push/pop-VLAN + csum macros
  (the fused form as shipped code).
- **Patents**: US10834241 (Xilinx streaming editor — pipeline of
  update/insert/remove shifters; closest to a small streaming editor),
  US10855816 (Avago parallel command-vector engine), US10686735
  (Barefoot deparser), US9961167 (Marvell canonical-layout trick).
- **Spec only**: NPL-Spec + NPL-Tutorials Editor constructs.
