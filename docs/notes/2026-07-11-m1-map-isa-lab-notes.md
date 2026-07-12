# Lab notes: M1 — the MAP ISA, from design doc to green rig in one run

*2026-07-11. Covers the match-action extension decision, the MAP ISA v0
design, and the M1 implementation (Sail spec → emulator → assembler →
composed PP→MAP rig → three demo programs).*

## The decision: two sibling ISAs

The second arc extends Nanuk from a parser to a parser + match-action
pipeline. The architectural fork was one-ISA-two-stages vs. two sibling
ISAs; Bili's instinct (clean boundary, composable PP ∘ MAP) matched what
the xISA white paper actually does — a specialized Parser ISA beside a
richer MAP ISA in the PFE — and won on first principles: an ISA is the
interface contract for a stage's *job*, per-instance hardware pays for
unused union capability, verification scales with per-model surface, and
doing the shape-an-ISA-to-a-job exercise twice *is* the curriculum. The
one-ISA counterexample (IXP/NFP pooled microengines) trades stage fit for
pooling and is notorious to program.

No third engine: PISA needs a deparser because the PHV rips headers apart;
our zero-copy offsets+SMD representation has nothing to reassemble. EZchip's
separate TOPmodify is a throughput play, not semantics. xISA folds editing
into the MAP + a fixed-function send-time MAC editor — we do the same
(in-place ST + signed head-delta on SEND, headroom for prepends, no splice
instruction, every instruction O(1)).

Checksum went the same way after checking both prior arts: P4 puts it in
externs at parser/deparser; xISA has CHKSUMTST/UPD/CALC as async coprocessor
ops. Ours is a synchronous `CSUMUPD` — black-box accelerator, one
instruction, silicon-proven shape.

## What the demo programs bought (the razor at work)

Writing the three demo programs *during* ISA design caught real issues:

- **The 5-instruction L2 switch.** LD → LOOKUP (fused branch-on-miss) →
  SEND, with the flood mask hardware-computed into inbound-SMD field 9.
  Computing all-but-ingress in the program would have needed variable shift
  + negation — ALU creep killed by moving one formula into hardware.
- **TTL wraparound.** 64-bit registers make decrement-then-test-zero wrap on
  TTL 0; the router rule (drop TTL ≤ 1) needs the test *before* the ADDI.
  Caught at design time by writing the program in the doc.
- **Totality as guard.** The planned hdr_present-bitmap guard for non-IPv4
  was unwritable (no AND) — and unnecessary: LD from an absent header base
  is a *defined* error halt (err_hdr_absent → drop). The totality doctrine
  replaced an instruction.
- **Tunnel framing is a design constraint, not a choice.** Head-only edits
  force full L2-in-L2 encap (outer Eth | nk | original frame). The original
  nanukproto framing (nk between Eth and L3) would need a mid-packet splice
  to decap. parse_tunnel.asm is the new PP side; inner_ethertype 0x6558
  borrows GRE's transparent-bridging convention.
- **SMD pass-through earned its place.** tunnel_pop distinguishes tunnel
  from plain traffic by an SMD flag the PP writes — cheaper and clearer than
  any bitmap test in the MAP.

## Sail lessons (new ones; stage-1 notes still apply)

- No `-` or `^` overloads on plain bitvectors: build masks with
  `sail_mask(64, sail_ones(n))`, complement/flip with `xor_vec`.
- Flow typing cannot see through `let` globals **or mutable loop
  variables**: `sail_ones(n_ports)` needs a local
  `let np = n_ports; assert(1 <= np & np <= 8)`, and vector accesses inside
  `while` loops need the index rebound immutably (`let ei = i`) before the
  assert helps.
- Two Sail models coexist happily in one CMake tree: second
  `--c-no-main`/`--c-preserve` codegen, second test function
  (`add_map_sail_test`), same runtime libs; the generated C's `z`-prefixed
  symbols never collide because each lands in its own executable.

## The composed rig

`run_pipeline()` chains the golden models exactly as the SimBricks glue
gates its forwarder today: PP verdict ≠ accept short-circuits. The MAP
emulator takes the inbound contract as a line-oriented ctx.txt (ingress,
smd, hdr, table, entry records) and emits JSON with the transmitted frame in
hex — frames beyond the 256B window pass their tail through untouched in the
Python harness, since bytes the engine never saw can't be edited by it.

The drift tripwire doubled: the same 15 golden words are pinned in
spec/map-test/test_map_decode.sail and spec/python/tests/test_map_encoding.py.

Scoreboard for the run: 12 ctest (7 MAP) + 74 spec/python pytest (31 new) +
untouched hw/lang/compiler/web suites all green; demos = L2 unicast/flood,
TTL+checksum against a scapy oracle, tunnel push/pop byte-identical
round-trip over two hops.

## Parked en route (with triggers, recorded in the ISA doc)

Async lookup/csum (RTL pipelining pain) · LPM/ternary (routing/ACL demos) ·
data-plane learning (learning-switch demo) · reparse loop · multi-reg keys
(5-tuple demo) · CSUMTST/CALC (verify or non-IPv4 demos) · SEND-without-halt.

## Next

M2 per the extension design doc: Amaranth MAP core, cosim vs. this Sail
model, then the SimBricks demo replaces the C++ flood forwarder with the
composed PP→MAP pipeline (the "parser program is the forwarding policy" beat
becomes "the *table* is the forwarding policy — reprogram it mid-ping").
