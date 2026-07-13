# Nanuk — Benchmark Suite Design

**Date:** 2026-07-13
**Status:** Approved design. Implementation deferred (suite is designed
up front; programs, vectors, and the ISA worklist land later).
**Siblings:** [Core interface design](2026-07-12-core-interface-design.md) ·
[MAP extension design](2026-07-11-map-extension-design.md) ·
[Naming doctrine](2026-07-12-naming-doctrine.md) ·
[Single-ISA doctrine](2026-07-12-single-isa-doctrine.md)

## Why this exists

Nanuk's MAP ISA was derived from a three-program "expressiveness razor":
L2 exact-match forward, TTL/field rewrite, tunnel push/pop. That razor
under-determined the ISA, and we did not notice.

The evidence: the MAP extension design's parked list names nine items
(async lookup, LPM, ternary, data-plane learning, reparse, multi-MAP,
modifier engine, mid-packet splice, PP changes). **Counters, registers,
and meters appear nowhere on it.** They were never weighed and never
refused — they simply never came up, because none of the three razor
programs needed them. Nanuk's statelessness is an omission wearing the
costume of a decision.

The structural reason is visible once the capability space is drawn
properly: **MAP has two independent capability axes** — what a program
does to the *bytes* (edit power) and how it *decides* (table power). The
three razor programs are three different points on the edit axis and the
*same* point on the table axis (one exact-match lookup). The table axis
was never explored, so everything on it — counters, LPM, state — stayed
invisible.

A benchmark suite is the instrument that prevents this. It is a design
tool first and an evaluation artifact second.

## Mandate

**The suite drives the ISA.** A program in the suite is a requirement:
anything it cannot express is a defect in the ISA until either fixed or
explicitly moved to the negative set with a stated reason. The ISA's shape
gets *derived* rather than tasted.

The counterpart obligation — and what makes this mandate safe — is that
the suite must also say what it *refuses*. A binding suite with no stated
exclusions will eventually demand hashing, per-flow registers, learning,
and a queue model, and Nanuk will have grown into a bad Tofino. The
negative set is the razor's other edge.

## Vocabulary

A **benchmark** is one program, the single capability it forces, and its
acceptance vectors. The ordered sequence of benchmarks within a track is a
**ladder**. Each benchmark must force something the one below it does not;
if two force the same capability, one is redundant and gets cut.

"Rung" is **not** used here — it already means a step on the four-rung
evaluation ladder (Sail cosim → pcap differential → SimBricks e2e → PPA).
Two ladders, two vocabularies.

The tracks map onto that evaluation ladder: the `pp` and `map` tracks are
**rung-2 instruments** (pcap differential against the golden model — "can
the ISA express this, and do all implementations agree?"). The `e2e` track
is a **rung-3 instrument** (SimBricks — "does the composed core behave
correctly inside a real system?"). An expressiveness failure indicts the
*ISA*; an e2e failure indicts the *core, the periphery, or the contract*.

## Reference corpora and the coverage boundary

Four corpora define the boundary. They were chosen because each is
cleanly licensed or re-derivable, actively maintained, and *pedagogically
graded* — they are teaching corpora, which is what Nanuk is.

| Corpus | License | Role |
|---|---|---|
| [p4lang/tutorials](https://github.com/p4lang/tutorials) (13 exercises) | Apache-2.0 | The canonical curriculum |
| [xdp-project/xdp-tutorial](https://github.com/xdp-project/xdp-tutorial) | GPL-2.0 (per-file) | The only corpus already graded along Nanuk's axes: parse → rewrite → redirect |
| Gibb et al., ANCS 2013, Fig. 3 parse graphs | re-derived (upstream `LICENSE` is 0 bytes) | The parser benchmark that the field never standardized |
| xISA walkthrough examples (5, via `~/i/sail-xisa`) | Xsight's work, re-derived as tasks | The donor architecture's own ladder |

Programs are **re-derived, never vendored** — the tasks are borrowed, not
the listings.

**Boundary rule (decided 2026-07-13): cover these corpora, and no more.**
Everything a corpus program demands is in scope; nothing else is. Revisit
only after the in-scope set is fully supported.

Applied literally this rule has a sharp edge, so the accounting is stated
explicitly. Gibb, the xdp-tutorial, and all five xISA examples are fully
coverable. **Five of p4lang's thirteen exercises are not**: they sit
behind hashing, per-flow register arrays, data-plane learning, or a
queue-depth signal. Taking them would grow Nanuk a hash unit, indexed
state arrays, table writes, and a traffic manager — a 2–3× larger arc than
the MAT extension, and the queue-depth signal would be *fictional* since
Nanuk has no traffic manager.

**Decision: corpora minus the cliff.** Nanuk gains reg-reg ALU, counter
tables, and LPM. It stays hashless and free of per-flow state. Note the
sanity check: this lands very close to what xISA actually is — which is
what a shrunk-xISA lineage claim should predict.

Consequence to state plainly: **"Nanuk is stateless" ends here.** It ends
in the cheap direction — indexed counters, control-plane readable, no
hash, no per-flow anything — but it ends.

## Track `pp` — parser

Ladder axis: **the structural complexity of the parse graph.** Linear →
branching → computed length → bounded repetition → nesting → scale.

| # | Benchmark | Forces | Source |
|---|---|---|---|
| P1 | Fixed stack — Eth + IPv4 + TCP, no branches | `EXT`, `ADVI`, `SETHDR`, `HALT`. The floor | original |
| P2 | Demux — ethertype → IPv4/IPv6/ARP; proto → TCP/UDP/ICMP | multi-way compare-and-branch dispatch; the dispatch cost model | p4 `basic` |
| P3 | Variable-length header — IPv4 IHL, TCP data offset | **computed advance**: `SHL` + `ADVR`. Exists to justify `SHL` — or delete it | p4 / Gibb |
| P4 | Bounded repetition — QinQ, then MPLS label stack (≤5, bottom-of-stack bit) | a loop with a *proved* bound; per-iteration state; termination | Gibb (service provider) |
| P5 | Nesting — VXLAN/GRE → inner Ethernet → inner IPv4 | the **same header type twice**: header-ID space, metadata-window pressure | Gibb (datacenter) |
| P6 | Scale — big-union graph (28 header types, 677 paths) | imem size, header slots, step budget. **Capacity, not capability** | Gibb (union) |

**Result: the PP ladder forces no new instructions.** PP v0 stays frozen.
P1–P6 are all expressible with the existing twelve. This is a real finding
— the parser design survives its own benchmark suite — and it is why P6 is
kept despite forcing no *capability*: it is the only place the suite
probes sizing, and sizing is where PP can still fail.

Note what the ladder never asks for: validation, checksums, arithmetic.
The PP has no ALU by design, and nothing in the parse corpus wants one.

## Track `map` — match-action

Two ladders, because MAP has two independent capability axes. Programs sit
at coordinates. This structure *is* the fix for the razor failure.

### `map`-edit — what the program does to the bytes

| # | Benchmark | Forces | Source |
|---|---|---|---|
| E0 | Forward / drop-all — no edits | terminators only. The floor; also the negative gate (drop-all → 100% loss) | existing |
| E1 | Fixed rewrite — MAC swap, DSCP mark | `ST` into the window | xdp `packet02` |
| E2 | Arithmetic rewrite — TTL decrement + checksum | signed `ADDI`, `CSUM` | p4 `basic`, xISA `ipv4-validation` |
| E3 | **Compute on packet-carried operands — the calculator** | **reg-reg ALU** (`ADD`/`SUB`/`OR`/`XOR`); multi-op dispatch | p4 `calc` **and** xISA `network-calculator` |
| E4 | Grow the head — VLAN push (4B), tunnel encap | headroom + head-delta | xdp `packet02`, p4 `basic_tunnel` |
| E5 | Shrink the head — VLAN pop, tunnel decap | negative head-delta | existing `nanukproto` |

**E3 is the highest-information benchmark in the suite.** It is the only
program that appears in **two independent corpora** — one of them the
donor architecture — and the only one Nanuk fails outright today: the
entire MAP ALU is `ADDI`/`ANDI`/`SHLI`, immediate-only, with no
register-register form at all (`ADD reg,reg` was deferred as YAGNI during
the core redesign) and no subtract, OR, or XOR. One program, indicting
three separate decisions.

xISA's calculator dispatches its opcode through a lookup table whose
values are jump addresses, executed by a register-indirect branch, and
closes with a shift-and-add multiply loop. Nanuk needs **neither** an
indirect branch nor a `MUL`: a `BEQ` chain covers the dispatch, and
shift-and-add is expressible with `SHLI` + reg-reg `ADD` + `BEQ`/`BNE`.
The indirect branch is an *optimization*, not a requirement — recorded as
a design question, not a corpus demand. This is a deliberate deviation
from the donor.

### `map`-table — how the program decides

| # | Benchmark | Forces | Source |
|---|---|---|---|
| T0 | Table-free forward — source routing (port from a header stack) | proves the machine is not table-dependent | p4 `source_routing` |
| T1 | Exact match — L2 forward | `LOOKUP` + hit/miss control flow | existing |
| T2 | Exact match → deny — stateless 5-tuple ACL | lookup → `DROP`. A deployed shape (Cloudflare L4Drop) | p4 `firewall`, minus its Bloom filter |
| T3 | **Count — per-port / per-type packet and byte counters** | **counter tables**: indexed increment, control-plane readable. Hashless state | xISA `ipv4-counters` |
| T4 | **Longest-prefix — IPv4 routing with aggregation** | **LPM tables.** /32 host routes preserve program shape but not prefix aggregation | p4 `basic`, xISA `simple-ipv4` |

T3 and T4 are the two benchmarks that cost real work, and both sit on the
axis the old razor never explored. That is not a coincidence.

## Track `e2e` — scenarios

A scenario is *topology + programs + table config + traffic + expected
observation*. The existing demo beats are already this shape; the track
promotes them to first-class artifacts with stated acceptance criteria.

| # | Scenario | Asserts |
|---|---|---|
| S1 | Flood forwarding — ping between two hosts | the composed core carries real traffic |
| S2 | Unicast by table; wrong table → 100% loss; live reprogram | **the table is the policy** |
| S3 | Two-switch tunnel — push at one, pop at the other | the contract composes across devices |

The track also carries the **rig** (SimBricks glue, `nanuk_switch.cc`,
Verilator wiring, build scripts). The rig is not Amaranth-specific: it
Verilates whatever Verilog the HW side exports, so a future `hw/sv`
implementation plugs into the same rig and runs the same scenarios. The
rig is a shared conformance fixture across hardware implementations,
exactly as `benchmarks/{pp,map}` are shared conformance vectors across
software implementations. That is why it belongs here.

Growth beyond S1–S3 is deliberately deferred until the expressiveness
tracks land — new scenarios should be *earned* by new capabilities.

## The negative set

Named refusals with reasons. This is half the coverage proof, not an
appendix.

| Refused | Programs | Reason |
|---|---|---|
| Hash + per-flow register arrays | p4 `load_balance`, `firewall` (Bloom), `link_monitor`; Katran/Maglev; Cilium; heavy hitters, flowlets, CONGA; all of Domino/Marple/Sonata | **The cliff.** Neither half is useful without the other, and together they are a different machine |
| Data-plane learning | p4 `flowcache`; learning switch | Already parked with a trigger (MAP writing its own tables). Keep it parked |
| Queue-dependent | p4 `ecn`, `mri`/INT | Nanuk has no traffic manager — a queue-depth signal would be **fictional** |
| Ternary / TCAM | switch.p4-class wildcard ACLs | Cost. T2 covers the deployed exact-match case. (Note: p4lang's 13 exercises use 20 exact + 18 LPM matches and **zero** ternary) |

Consequence worth recording: the per-hop-telemetry demo idea dies in its
INT form (it needs queue depth). A hop-ID-only variant survives and stays
available.

## Checkable properties

The suite claims two things, and both are mechanically auditable:

- **Coverage.** Every program in the four reference corpora is either
  (a) expressible by some benchmark, or (b) named in the negative set with
  a reason. No reference program may be unaccounted for.
- **Minimality.** Removing any benchmark drops some reference program out
  of (a) without moving it into (b).

`benchmarks/coverage.md` is the generated audit: every p4 exercise, every
xdp-tutorial lesson, every Gibb parse graph, and all five xISA examples,
each mapped to a benchmark or a refusal.

## Acceptance criteria

A benchmark is mechanically defined as:

- a **program** — living in `examples/` as standalone content (a benchmark
  member *is* an example; it does not stop being one), with an eDSL twin;
- the **one capability** it forces, named;
- **acceptance vectors** — packets in, expected result out — run against
  the golden model in CI;
- a **back-link** from the example's README to its benchmark.

The capability matrix is a **generated artifact, not a hand-maintained
document** — emitted from what actually passes. Same mirror-with-tripwire
discipline as the rest of the stack: the table can never quietly lie about
what Nanuk can do.

`benchmarks/` holds declarative content and (for `e2e`) its fixture;
runners live with their language (the pp/map harness stays in
`nanuk.testkit`).

## Layout

```
benchmarks/
  README.md       # ladder logic, coverage + minimality claims, negative set
  coverage.md     # every reference program -> benchmark or refusal (generated)
  pp/             # P1..P6
  map/            # E0..E5 (edit axis), T0..T4 (table axis)
  e2e/            # S1..S3 scenarios + the SimBricks rig (from demo/)
```

`demo/` **dissolves** into `benchmarks/e2e/`. "Demo" reverts to pure
vocabulary — a *staged run*, which is what performing an e2e scenario for
an audience is. The artifact is a scenario; the demo is a performance of
it. Naming a directory after the performance was always the odd one out.

`examples/` is unchanged in role: the programs. `benchmarks/` references
them by name and never copies them. One program can simultaneously be a
playground seed, a benchmark member, and an e2e stage — that is what a
good example is for.

Generated e2e artifacts stay under the producing subsystem
(`benchmarks/e2e/{build,out}`, gitignored), per the build-output doctrine.

## Derived ISA worklist

Forced by the suite under the stated boundary. **PP: nothing.** MAP:

| Demanded by | Addition | Cost |
|---|---|---|
| E3 (`calc`, ×2 corpora) | reg-reg ALU: `ADD`, `SUB`, `OR`, `XOR` | cheap, uncontroversial |
| T3 (xISA `ipv4-counters`) | **counter tables** — new table kind + increment instruction | reopens the table subsystem |
| T4 (p4 `basic`, xISA `simple-ipv4`) | **LPM tables** — new match kind | reopens the table subsystem |

Explicitly *not* forced, and therefore not taken: register-indirect branch
(a `BEQ` chain covers E3's dispatch), `MUL` (shift-and-add is
expressible), queue-depth signal (refused), hash (refused).

## Open items to verify before implementation

Discipline demands these be checked against the corpora rather than
assumed — the boundary rule is "cover the corpus, no more," so each of
these is in scope only if a corpus program actually demands it.

1. ~~**Tail delta at `SEND`.**~~ **RESOLVED 2026-07-13: out of scope.**
   See "Tail delta" below.
2. **Headroom > 32B.** IPv6-in-IPv4 encap needs 40B. Verify whether any
   corpus program requires it; headroom is already a parameter, so this
   may be a sizing note rather than a change.
3. **Frame length in the metadata window.** T3's byte counters need it
   (xISA uses `SIZEQUERY`). Check whether `md` already carries it; if not,
   this is a contract question, not an ISA question.
4. **Gibb's fourth parse graph (edge).** Four graphs exist, not three.
   Decide whether `edge` earns a benchmark or folds into P5/P6.

## Tail delta — investigated, out of scope

Recorded because the investigation is more useful than the verdict.

**What it is for.** In a zero-copy machine you cannot allocate a reply
packet — you only have the one that arrived. Generating an ICMP error
therefore means *reshaping the received frame into the reply*: shrink the
tail down to the quoted prefix of the original datagram (the kernel sample
cuts to 98B = 14B Ethernet + 20B IP + 64B payload), grow the head by 28B
for a fresh IP + ICMP header, swap addresses, recompute checksums, reflect
it out (`XDP_TX`). The tail shrink is what makes the quoted original the
right size. This is production code, not a toy: **Katran** (its
encapsulation can exceed path MTU, so it must answer PMTUD) and **Cilium**
(DSR ICMP replies) both do exactly this, and the 2018 kernel commit that
added `bpf_xdp_adjust_tail` names it as the intended use case.

**Why it is nonetheless out of scope.** No reference corpus demands it:

- **xdp-tutorial** — appears only in `experiment01-tailgrow`, which is
  outside the graded lesson tracks and has no assignments. `packet02`
  mentions it in one README sentence and does not use it.
- **p4lang tutorials** — `truncate()` exists in v1model; **zero of the 13
  exercises use it**. No exercise appends at the tail either (v1model's
  deparser emits headers *before* the payload — a tail edit is not
  expressible).
- **xISA** — **no tail concept at all.** `FrameDelta` is a 9-bit signed
  *head* delta ("start of packet is calculated as FOF − FrameDelta").
  `SIZEQUERY` reads packet size; there is no `SIZESET`. All five example
  programs set `FrameDelta = 0`.

**The asymmetry to remember if this is ever revisited.** Tail *shrink* is
a length decision, not a data operation — in a streaming egress the drain
simply stops early (`out_len = in_len + head_delta + tail_delta`); nothing
moves. bmv2's `truncate()` is a one-line `min()`. It is arguably cheaper
than the head delta Nanuk already has. Tail *grow* is an allocation
decision: it needs tailroom ownership, a failure mode, and initialization
semantics for the appended bytes — precisely the three things the kernel
had to invent (`frame_sz`, `-EINVAL`, `memset`) to ship grow in v5.8, two
years after shrink. **If Nanuk ever takes this, take shrink only.**

Counterpoint worth holding: xISA declines to expose even the cheap half,
which suggests that in a real pipeline even a "free" tail shrink needs the
egress subsystem to agree on the length (flit accounting, CRC recompute).
Do not assume it is free in RTL without checking.

## Parked, with triggers

| Item | Re-entry trigger |
|---|---|
| **Tail delta at `SEND` (shrink only)** | A corpus program demands it — the natural one is ICMP-error generation in place (Katran/Cilium PMTUD pattern) |
| Hash unit + per-flow register arrays | The in-scope corpora are fully supported *and* a deliberate decision to cross the cliff |
| Queue-depth signal | A traffic manager exists |
| Ternary tables | An ACL demo that exact match genuinely cannot express |
| Register-indirect branch | Dispatch cost shows up in a real program |
| Corpus expansion | The current boundary is fully supported |
