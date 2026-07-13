# Benchmarks

The graded suite the Nanuk ISA answers to.

This is a **design instrument first and an evaluation artifact second**. The
MAP ISA was originally derived from a three-program razor — L2 forward, TTL
rewrite, tunnel push/pop — and that razor under-determined it: all three
programs sit at the same point on the *table* axis, so counters, LPM, and
state were never even weighed. The suite exists so that cannot happen again.

**The suite is binding.** A program in it is a requirement: anything it cannot
express is a defect in the ISA until either fixed or explicitly moved to the
negative set with a stated reason. The counterpart obligation — and what makes
that safe — is that the suite must also say what it **refuses**.

Design: [`docs/superpowers/specs/2026-07-13-benchmark-suite-design.md`](../docs/superpowers/specs/2026-07-13-benchmark-suite-design.md).
Audit: [`coverage.md`](coverage.md).

## Vocabulary

A **benchmark** is one program, the single capability it forces, and its
acceptance vectors. The ordered sequence within a track is a **ladder**. Each
benchmark must force something the one below it does not; if two force the
same capability, one is redundant and gets cut. (One already was — see the
negative set.)

"Rung" is *not* used here. It means a step on the four-rung evaluation ladder
(Sail cosim → pcap differential → SimBricks e2e → PPA). Two ladders, two
vocabularies.

## Boundary

The suite covers four reference corpora **and no more**:

| Corpus | License | Role |
|---|---|---|
| [p4lang/tutorials](https://github.com/p4lang/tutorials) (13 exercises) | Apache-2.0 | the canonical curriculum |
| [xdp-project/xdp-tutorial](https://github.com/xdp-project/xdp-tutorial) | GPL-2.0 | already graded along Nanuk's axes: parse → rewrite → redirect |
| Gibb et al., ANCS 2013, Fig. 3 parse graphs | re-derived | the parser benchmark the field never standardized |
| xISA walkthrough examples (5) | re-derived | the donor architecture's own ladder |

Programs are **re-derived, never vendored** — the tasks are borrowed, not the
listings.

## Track `pp` — parser

Ladder axis: **the structural complexity of the parse graph.**

| # | Forces | Program | Status |
|---|---|---|---|
| P1 | `EXT`/`ADVI`/`SETHDR`/`HALT` — the floor | `examples/l2l3l4` | ✅ |
| P2 | multi-way compare-and-branch dispatch | `examples/l2l3l4` | ✅ |
| P3 | computed advance (`SHL` + `ADVR`) — IPv4 IHL, TCP data offset | `examples/l2l3l4` | ✅ |
| P4 | bounded repetition with a *proved* bound (QinQ) | `examples/l2l3l4` | ✅ |
| P5 | **incomplete information** — the successor type is not in the header you are on | `examples/mpls_sp` | ✅ |
| P6 | **nesting** — the same header type twice | `examples/overlay_dc` | ✅ |
| P7 | **scale** — capacity, not capability | `examples/union` | ✅ |

**Result: PP ISA v0 parses every graph in the Gibb corpus and forces no new
instruction.** P5–P7 are the evidence (`sw/python/tests/test_benchmarks_pp.py`,
30 vectors against the golden model). The union's worst path — 14 header
instances, QinQ → 5×MPLS → IPv4 → AH → NVGRE → inner Ethernet/IPv4/TCP — costs
**155 of 256 steps** and the program is **157 of 1024 imem words**.

Two things the parser ladder taught us:

- **Lookahead is free, and it is why PP survives.** `EXT` is not a byte
  extract: it is an 11-bit *bit-offset*, non-consuming read reaching 255 bytes
  past the cursor. MPLS names no successor — `bos` says the label stack ended,
  not what follows — so P5 reads the four bits *past* the label while the
  cursor still sits on it. Sub-byte fields (IHL, `bos`, `dataOffset`) come free
  with no mask and no right shift, and a peek past the buffered window *traps*
  rather than reading garbage.
- **The header-id space is a real resource.** `SETHDR` takes a 4-bit id — 16
  slots — and the union graph has 21 header *types*. One slot per type is
  arithmetically impossible, so mutually-exclusive types must be aliased. That
  trade-off is the content of P7.

## Track `map` — match-action

Two ladders, because MAP has two independent capability axes. Programs sit at
coordinates. This structure *is* the fix for the razor failure.

**Edit axis** — what the program does to the bytes:

| # | Forces | Program | Status |
|---|---|---|---|
| E0 | terminators only — the floor, and the negative gate | `examples/drop_all` | ✅ |
| E1 | **fixed rewrite** — `ST` into the window | `examples/icmp_echo` | ✅ |
| E2 | arithmetic rewrite — signed `ADDI`, `CSUM` | `examples/map_ttl` | ✅ |
| E3 | **compute on packet-carried operands** | — | ❌ needs reg-reg ALU |
| E4 | grow the head — headroom + head delta | `examples/nanukproto` | ✅ |
| E5 | shrink the head — negative head delta | `examples/srcroute`, `nanukproto` | ✅ |

**Table axis** — how the program decides:

| # | Forces | Program | Status |
|---|---|---|---|
| T0 | **table-free forward** — the route is in the packet | `examples/srcroute` | ✅ |
| T1 | exact match — `LOOKUP` + hit/miss control flow | `examples/map_l2fwd` | ✅ |
| T2 | **counters** — indexed increment, hashless state | — | ❌ needs counter tables |
| T3 | **longest-prefix** — routing with aggregation | — | ❌ needs LPM tables |

**E3 is the highest-information benchmark in the suite.** It is the only
program that appears in **two independent corpora** (p4's `calc` and xISA's
`network-calculator` — one of them the donor architecture), and the only one
Nanuk fails outright: the entire MAP ALU is `ADDI`/`ANDI`/`SHLI`,
immediate-only, with no register-register form at all.

## Track `e2e` — scenarios

A scenario is *topology + programs + table config + traffic + expected
observation*, run under SimBricks with the RTL in the loop. See
[`e2e/`](e2e/), which carries the scenarios **and the rig** — the rig
Verilates whatever Verilog the HW side exports, so a future `hw/sv`
implementation plugs into the same fixture and runs the same scenarios.

## The negative set

Named refusals. This is half the coverage proof, not an appendix.

| Refused | Programs | Reason |
|---|---|---|
| Hash + per-flow register arrays | p4 `load_balance`, `firewall`, `link_monitor`; Katran; Cilium; all of Domino/Marple/Sonata | **The cliff.** Neither half is useful without the other, and together they are a different machine |
| Data-plane learning | learning switch | Parked with a trigger (MAP writing its own tables) |
| Queue-dependent | p4 `ecn`, `mri`/INT | Nanuk has no traffic manager — a queue-depth signal would be **fictional** |
| Per-copy processing | p4 `flowcache` | A **divergently-edited clone**. Replication is an egress port *bitmap*, which fans out identical frames. Not a gap to fill but a boundary to name: no egress pipeline |
| Ternary / TCAM | switch.p4-class wildcard ACLs | **Demanded by zero programs across all four corpora** (p4's 18 tables: 9 exact, 8 LPM, 0 ternary). The refusal is free |
| Tail edits | `xdp_adjust_tail` | No corpus demands it. Parked, not refused on principle |
| *(cut, not refused)* stateless ACL | — | No corpus program demanded it standalone. The boundary rule cuts it; the behavior stays expressible |

## Claims

- **Coverage.** Every program in the four corpora is either expressible by some
  benchmark, or named in the negative set with a reason. No program is
  unaccounted for.
- **Minimality.** Removing any benchmark drops some corpus program out of
  coverage without moving it into the negative set.

Both are audited in [`coverage.md`](coverage.md).

## The worklist the suite produced

**PP: nothing.** It survives its own suite; v0 stands frozen.

**MAP**, in the order the ladder demands it:

1. **reg-reg ALU** — `ADD`, `SUB`, `AND`, `OR`, `XOR` (E3). Note `AND`: our
   first draft said ADD/SUB/OR/XOR, and both calculators use `&`. The suite
   caught that on its first pass.
2. **counter tables** (T2) — double counters, with the **byte delta implicit**
   (`packets += 1; bytes += plen`). xISA needs a `SIZEQUERY` only because its
   `COUNTER` takes an explicit delta register; making ours implicit means no
   program in any corpus ever needs to read the frame length, so no length
   instruction and no second system metadata slot.
3. **LPM tables** (T3) — keys fit 64 bits; xISA's widest is a 44-bit VRF++DIP.

Explicitly *not* taken, because no corpus program forces them: register-indirect
branch, `MUL`, a frame-length read, ordered compares (`BLT`/`BGE`),
shift-by-register.
