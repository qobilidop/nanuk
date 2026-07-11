# M3: MAP in the Language Stack — Design

**Date:** 2026-07-11
**Status:** Approved (autonomous-run design record, per the M2→M3 mandate).
**Parent:** [Match-Action Extension](2026-07-11-map-extension-design.md)

## Principles

Sibling symmetry, again: the parser's language stack (eDSL states → IR →
lowering → asm, with validator + interpreter) is reused shape-for-shape for
the MAP. The parser `Program` message and all its consumers are untouched.

## IR (`nanuk_ir.proto` grows, nothing changes)

New top-level `MapProgram` message:

```proto
message MapProgram {
  uint32 ir_version = 1;              // = 1 (same version space)
  repeated TableDecl tables = 2;      // declarations only — entries are
                                      // control-plane state, never IR
  repeated MapState states = 3;       // states[0] is the start state
}
message TableDecl { uint32 table_id = 1; uint32 key_width = 2;
                    uint32 action_width = 3; string debug_name = 4; }
message MapState { string name = 1; repeated MapOp ops = 2;
                   Terminator terminator = 3; }
message MapOp {
  oneof op {
    MapLoad load = 1;        // value_id = window bytes (hdr-relative)
    MapLoadMd load_md = 2;   // value_id = inbound-SMD field
    MapConst const = 3;      // value_id = 16-bit immediate (MOVI)
    MapAdd add = 4;          // value_id = src + signed const (ADDI)
    MapStore store = 5;      // window bytes = value (hdr-relative)
    CsumUpdate csum = 6;     // in-place IPv4 checksum fix
    Lookup lookup = 7;       // THE op with control flow (see below)
  }
}
message MapLoad  { uint32 value_id = 1; uint32 hdr_id = 2;
                   sint32 byte_offset = 3; uint32 nbytes = 4;
                   string debug_name = 5; }
message MapLoadMd{ uint32 value_id = 1; uint32 field = 2;
                   string debug_name = 3; }
message MapConst { uint32 value_id = 1; uint32 imm = 2;
                   string debug_name = 3; }
message MapAdd   { uint32 value_id = 1; uint32 src_value_id = 2;
                   sint32 imm = 3; }
message MapStore { uint32 value_id = 1; uint32 hdr_id = 2;
                   sint32 byte_offset = 3; uint32 nbytes = 4;
                   string debug_name = 5; }
message CsumUpdate { uint32 hdr_id = 1; sint32 byte_offset = 2; }
message Lookup   { uint32 value_id = 1; uint32 table_id = 2;
                   uint32 key_value_id = 3; string miss_state = 4; }
message MapSend  { uint32 bitmap_value_id = 1; sint32 delta = 2; }
```

`Terminator` gains `MapSend send_ = 4` and `Drop drop = 5` (message `Drop {}`)
in its oneof; `Dispatch`/`Goto` are reused as-is for BEQ-chain branching.

**Lookup is deliberately an op, not a terminator**: it mirrors the fused
LOOKUP-branch-on-miss instruction — on hit the value is defined and
execution continues in the same state; on miss control transfers to
`miss_state` with the value dead. It is the one op with control flow, and
the validator knows it (values defined before a Lookup are not live in the
miss state — the no-cross-state-values rule already guarantees that).

## eDSL (`nanuk_lang`)

`MapProgram` mirrors `Parser` (same decorator/state/build_ir/compile shape):

```python
mp = MapProgram()
l2 = mp.table("l2", key_width=48, action_width=8)     # TableDecl handle
ethh = mp.header(eth, hdr_id=0)                        # bind Header to PP hdr id
ipv4h = mp.header(ipv4, hdr_id=2)

@mp.state(start=True)
def forward(s):
    dmac = s.load(ethh.dst)                # byte-aligned Header field
    act = s.lookup(l2, dmac, miss=flood)   # hit: continue; miss: goto flood
    s.send(act)

@mp.state()
def flood(s):
    s.send(s.load_md(MD_FLOOD))
```

- `mp.header(header, hdr_id=N)` binds a `Header` to the PP's hdr id; field
  access gives (hdr_id, byte_offset, nbytes). Non-byte-aligned fields are a
  CompileError (the MAP is the byte machine; sub-byte edits are parked).
- Raw window access for headroom writes: `s.store(v, hdr=H_FRAME,
  byte_offset=-22, nbytes=2)` (tunnel pushes write outside any bound header).
- `s.const(imm)`, `s.add(v, imm)` (negative ok), `s.csum_update(ipv4h)`,
  `s.send(bitmap_value, delta=0)`, `s.drop()`, `s.dispatch(...)`/goto reuse
  the parser eDSL's Terminator plumbing.
- `MD_INGRESS = 8, MD_FLOOD = 9, MD_HDRS = 10` named constants; SMD slots
  0-7 by number.

## Lowering + interpreter (`nanuk_ir`)

- `lower_map.to_map_asm(MapProgram) -> str` — same register discipline as
  the parser lowering (r0-r2 values allocated per state, r3 scratch for
  dispatch constants), targets `nanuk_spec.map_asm` syntax.
- `validate_map(MapProgram)` — value discipline, state refs, table refs,
  encoding ranges (offsets ±512, nbytes 1-8, imm 16-bit).
- `interp_map(program, packet, pp, tables, ingress) -> MapInterpResult`
  (fields = MapResult's) with **step accounting mirroring the lowering
  instruction-for-instruction** (dispatch = 2/case tried, Lookup = 1,
  Const = 1, Send/Drop = 1...), exactly as interp.py does for the parser —
  so differential tests compare steps and budget exhaustion too. If the
  lowering's cost model changes, interp_map follows.

## Demo parity (the M3 "done" gate)

The three demo MAP programs rewritten in the eDSL under
`lang/nanuk_lang/programs/` + `examples/*/fwd.py`, with tests asserting:

1. eDSL → asm is **behaviorally identical** to the hand-written .asm over
   the M1 corpus through the golden emulator (not necessarily text-identical
   — register allocation may differ; the contract diff is what matters).
2. `interp_map` vs `run_map(lowered)` — zero divergence on ALL fields
   including steps (the compiler's translation-validation-lite, as for the
   parser).

## Playground

Minimal honest scope: the bridge/wheels grow `compile_map_source` +
`run_map_packet` (interp_map with a preset table set); one MAP preset
program (l2fwd) joins the example chips; the result panel displays
egress/delta/frame for MAP runs. The three-pane provenance flow (eDSL | IR
| asm) works unchanged — MapProgram render + lowering emission counts reuse
the same ordered-walk machinery.

## Parked (with triggers)

eDSL `if/else` sugar over dispatch (a program that needs non-equality
branching) · cross-state values (a program that genuinely can't restructure)
· table-entry literals in the eDSL for tests (playground v2 packet-lab) ·
IR-level optimizer over MapOps (MLIR satellite question, still parked).
