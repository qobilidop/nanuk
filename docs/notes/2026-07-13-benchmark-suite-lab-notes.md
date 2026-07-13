# Lab notes — the benchmark suite, and what it found

**2026-07-13.** Designing an instrument, then watching it indict the thing it
was built to measure.

## The bug that started it

Nanuk's MAP ISA was derived from a three-program "expressiveness razor": L2
exact-match forward, TTL rewrite, tunnel push/pop. It looked like a
methodology. It was a coincidence.

Those three programs are three different points on the *edit* axis — what a
program does to the packet's bytes — and the **same** point on the *table*
axis: one exact-match lookup, no state. The razor never moved along the second
axis, so everything on it stayed invisible: counters, longest-prefix matching,
per-flow state. None of them was rejected. None was even weighed.

The proof is in our own parked list. The MAP extension design names nine
deferred items — async lookup, LPM, ternary, data-plane learning, reparse,
multi-MAP, a modifier engine, mid-packet splice, PP changes. Counters and
registers appear nowhere. We had been treating an accident as an axiom, and
saying "Nanuk is stateless" as though it were a decision.

A benchmark suite is the instrument that catches this. It is a design tool
first, an evaluation artifact second.

## What a benchmark is, and what a rung isn't

A **benchmark** is one program, the single capability it forces, and its
acceptance vectors. The ordered sequence is a **ladder**. Each benchmark must
force something the one below does not — if two force the same thing, one gets
cut.

We nearly called each entry a "rung," and stopped: *rung* already means a step
on the four-rung evaluation ladder (Sail cosim → pcap differential → SimBricks
e2e → PPA). Two ladders, two vocabularies. Small thing; the kind of small thing
that rots a codebase.

## Corpora, and the licensing surprise

Four reference corpora: p4lang/tutorials, xdp-project/xdp-tutorial, Gibb's
ANCS 2013 parse graphs, and xISA's five published walkthroughs. Cover them, and
no more.

The obvious candidate for *the* suite — Domino/Banzai, whose atom-containment
table is precisely the "which programs need which instructions" instrument we
wanted — turned out to be unusable twice over. All eleven of its programs are
stateful by construction, so **zero of them run on Nanuk**; the suite exists to
argue about stateful atoms, which is the axis we set to zero. And it has **no
license file at all** — nor do Chipmunk, Marple, Sonata, or Gibb's own
`parser-gen` (its `LICENSE` is literally zero bytes).

That is not a coincidence, and it told us what our suite had to be. The cleanly
licensed material in this field is the *pedagogical* material. A teaching corpus
assembled from teaching corpora. We borrow the tasks and re-derive the
programs; we vendor nothing.

## The instrument works in both directions

On its first pass, before a line of it was implemented, the suite:

- **added** a capability we'd missed — our accepted addition read
  "reg-reg ALU: ADD/SUB/OR/XOR", and both calculators (p4's `calc`, xISA's
  `network-calculator`) use `&`. The one program motivating the addition needed
  an operator the list omitted;
- **exposed** a documentation gap in something already shipped — `LOOKUP`
  defines its miss branch and says "hit continues," but four corpus programs
  need per-entry *action selection*, not just action data;
- **cut** a benchmark we wanted. A stateless-ACL benchmark was demanded by *no*
  corpus program standalone; it survived only on the strength of being a real
  deployed shape (Cloudflare's L4Drop), which is exactly the argument the
  boundary rule exists to reject.

A suite that only ever adds things isn't a razor.

## What the audit found

Four agents graded every program in all four corpora against the *real* ISA —
the Sail models and the ISS, not the design docs. Highlights:

**Ternary matching is demanded by zero programs, across all four corpora.**
p4lang's thirteen exercises use eighteen tables: nine exact, eight LPM, zero
ternary, zero range. That refusal is free, and we'd been carrying it as a cost.

**Byte counters need the frame length — and the fix is to not read it.** All
three program corpora want byte counters, and MAP cannot read `plen`. xISA
reaches for a `SIZEQUERY` instruction. But it only needs one because *its*
`COUNTER` takes an explicit delta register. Make ours implicit —
`packets += 1; bytes += plen` — and no program in any corpus ever needs the
length. The counter *hardware* consumes `plen`, which the core already knows
from streaming; the *instruction set* never exposes it. No new instruction, no
second system metadata slot, and slot 0 stays the only one.

**`CSUM` is narrower than we thought.** UDP/TCP/ICMPv6 checksums cover a
pseudo-header that is *not contiguous* with the L4 range, and TCP's length
isn't in the packet at all. A ones-complement *range* checksum structurally
cannot compute them. Every L4 checksum in the xdp corpus is patched
*incrementally* with immediate adds — so the corpus is satisfiable, but not
because of `CSUM`. Its clean use is the IPv4 header checksum, where it also
doubles as a verify (a valid header sums to `0xFFFF`, so `CSUM` returns 0).

**`flowcache` is refused, and it earned its own doctrine.** It clones to a CPU
port and prepends a header **to the copy but not the original**. Replication in
Nanuk is an egress port *bitmap*, which fans out identical frames. That is not
a gap to fill — it is a boundary to name: *no egress pipeline, no per-copy
processing*, alongside the single-ISA and no-deparser doctrines.

## Then we built it, and the parser held

The audit's headline claim — *PP v0 parses every Gibb graph and forces no new
instruction* — was an assertion derived from reading the ISA. So we wrote the
programs.

**P5, lookahead.** MPLS names no successor: `bos` says the label stack ended,
not what follows. The type is the four bits *past* the label you're standing on.
This is Gibb's Challenge #3, and it's what separates packet parsing from
instruction decoding — headers, unlike instructions, don't encode their own
type. No benchmark as first drafted forced it, which meant our ladder had a
hole and QinQ had been quietly standing in for MPLS. They are not the same
capability: **VLAN repeats with complete information, MPLS with incomplete.**

PP handles it, and the reason is a detail we'd forgotten about our own ISA:
`EXT` is not a byte extract. It is an 11-bit *bit-offset*, non-consuming read
reaching 255 bytes past the cursor. So the lookahead is `ext r2, 32, 4` with the
cursor still on the label — no speculation, no rewind, no peek instruction — and
it *traps* if it runs past the buffered window rather than reading garbage.
Sub-byte fields (IHL, `bos`, `dataOffset`) come free from the same property.

Bounded repetition without an increment is the other half. PP has no `ADD`, so
the label counter is **one-hot**, shifted left once per label. A sixth label is
refused *in the program text*, not by running out of step budget. Termination is
visible, not merely guaranteed.

**P7, scale.** All 21 header types of the union graph. `SETHDR` takes a 4-bit
id — sixteen slots — so one slot per type is arithmetically impossible, and
mutually-exclusive types must be **aliased**. That trade-off is the content of
the benchmark; it's why a capacity test earns a place on a capability ladder.
Worst path (QinQ → 5×MPLS → IPv4 → AH → NVGRE → inner Ethernet/IPv4/TCP, 14
header instances): **155 of 256 steps**, program **157 of 1024 imem words**.
Both pinned as regression canaries.

The claim survived contact. PP ISA v0 stands frozen.

## Two bugs the programs found, both instructive

**GRE without a key.** Our NVGRE path assumed the key field was present without
checking the K bit, so a keyless TEB header ran off the end of the window. The
fix matters less than the shape of the failure: both refusals — C bit set, K bit
clear — must be *decisions*, not window violations. A parser that errors where
it should have declined is a parser that will error on an attacker's packet.

**The overlapping copy.** Source routing pops a 2-byte hop that sits *after* the
Ethernet header — a mid-frame splice the ISA has no instruction for. The
zero-copy idiom is to relocate the 14-byte prefix forward and send with a head
delta of −2, so the drain simply starts later. Write that low-bytes-first and
the second `ld` reads bytes 8–9 *after* the first `st` has already overwritten
them, and the source MAC comes out mangled: `aabbaabbee02` instead of
`aabbccddee02`. It is `memmove`'s hazard, reproduced in four instructions. The
window is memory, not a register file, and it does not forgive an aliasing copy.

We only caught it because the test printed the frame. "Payload intact" and
"checksum valid" both passed. Assert the whole frame.

## Things the machine cannot do, discovered by trying

- **`1 << ingress` is not computable.** `shli` takes an immediate; there is no
  shift-by-register. So "send it back where it came from" needs a table — the
  same wall the flood table hit, met from the other side. Source routing dodges
  it by carrying the *bitmap* in the packet rather than a port number, which is
  what makes that benchmark genuinely table-free.
- **End-around carry without a carry flag.** MAP is flagless and has no right
  shift, so the carry out of bit 15 is recovered by masking to 16 bits and
  asking whether anything was lost (`andi`, then `beq`). It reads better than it
  sounds, and it's the only way to patch a checksum here.
- **Swapping IPv4 addresses doesn't disturb the header checksum.** It's a sum,
  and addition commutes. The echo responder simply never recomputes it — the
  kind of thing you only notice when the instruction set is small enough to make
  you think.

## The move, and the path bug it exposed

`demo/` dissolved into `benchmarks/e2e/`. "Demo" reverts to pure vocabulary — a
*staged run*, which is what performing an e2e scenario for an audience is. The
artifact is a scenario; the demo is a performance of it. Naming a directory
after the performance was always the odd one out.

The rig moved with the scenarios, and that's the load-bearing part: it Verilates
whatever Verilog the HW side exports, so a future `hw/sv` plugs into the same
fixture and runs the same scenarios. It is a shared conformance fixture across
hardware implementations, exactly as `benchmarks/{pp,map}` are shared
conformance vectors across software implementations.

The scripts derived the repo root with `dirname $0/..`, correct from `demo/` and
one level short from `benchmarks/e2e/`. Every path built from `REPO` was
silently wrong. This is the **second** time a textual path rewrite has broken
these scripts with no test noticing. They now assert they actually landed at the
repo root instead of trusting the arithmetic — and this time the move was
verified by running it, not by reading it: two QEMU hosts, real ICMP through the
Verilator'd core, `E2E DEMO PASSED`.

Twice is a pattern. The lesson isn't "be careful with paths," it's that anything
verified only by reading will eventually be wrong.

## Where it leaves the ISA

**PP: nothing.** It survives its own suite.

**MAP**, in the order the ladder demands:

1. **reg-reg ALU** — `ADD`, `SUB`, `AND`, `OR`, `XOR`. Demanded by exactly one
   program, which appears in two independent corpora, and which Nanuk fails
   outright today.
2. **counter tables** — double counters, byte delta implicit.
3. **LPM tables** — keys fit 64 bits.

Not taken, because no corpus program forces them: register-indirect branch
(a `BEQ` chain covers the calculator's dispatch — though xISA's *lesson*, that
the control plane can redefine the data plane's control flow, is genuinely lost,
and that is the one place we land on "impossible" rather than "verbose"), `MUL`
(shift-and-add is expressible, and 64-bit registers delete xISA's entire
carry-propagation dance), a frame-length read, ordered compares, and
shift-by-register.

"Nanuk is stateless" ends here. It ends in the cheap direction — indexed
counters, no hash, no per-flow anything — but it ends, and this time on purpose.
