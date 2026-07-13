# Nanuk — Benchmark Coverage Audit

**Date:** 2026-07-13
**Status:** Complete. Becomes `benchmarks/coverage.md` when that tree lands.
**Parent:** [Benchmark suite design](2026-07-13-benchmark-suite-design.md)

Every program in the four reference corpora, graded against the real ISA
(read from `spec/sail/model/{pp,map}` and `sw/python/nanuk/isa/*_iss.py`,
not from the design docs). Verdicts: **COVERED** today ·
**+ADD** (needs an accepted addition) · **REFUSED** (negative set) ·
**BLOCKED** (needs something on neither list — the discoveries).

## p4lang/tutorials — 13 exercises

| Exercise | Coords | Verdict |
|---|---|---|
| `multicast` | P1 · E0 · T1 | **COVERED** (via the flood-table idiom, see below) |
| `source_routing` | P4 · E2/E5 · **T0** | **COVERED** |
| `basic` | P2 · E2 · T4 | **+ADD** LPM |
| `basic_tunnel` | P2 · E2 · T1+T4 | **+ADD** LPM |
| `qos` | P2 · E1/E2 · T4 | **+ADD** LPM (no meters, no queues despite the name) |
| `calc` | P1 · **E3** · T1 | **+ADD** reg-reg ALU |
| `p4runtime` (`advanced_tunnel`) | P2 · E2/E4/E5 · T1+T3+T4 | **+ADD** LPM + counters |
| `ecn` | — | **REFUSED** — `standard_metadata.enq_qdepth` |
| `mri` | — | **REFUSED** — `standard_metadata.deq_qdepth` |
| `firewall` | — | **REFUSED** — `register<>` Bloom filters + `hash()` |
| `link_monitor` | — | **REFUSED** — `register<>(MAX_PORTS)` arrays |
| `load_balance` | — | **REFUSED** — `hash(…crc16…)` ECMP |
| `flowcache` | — | **BLOCKED** — see Discovery 5 |

**Match-kind census (18 tables): 9 exact, 8 LPM, 2 keyless, 0 ternary,
0 range.** Refusing ternary costs this corpus *nothing*. LPM is the single
match kind gating the most exercises.

Beware a grep trap: `HashAlgorithm.csum16` appears in 11 of 13 inside
`update_checksum(...)` — that is `CSUM`, not hashing. Only `firewall` and
`load_balance` call the `hash()` extern.

## xdp-project/xdp-tutorial — graded lessons

| Lesson | Coords | Verdict |
|---|---|---|
| `basic01`/`basic02` | E0 · T0 | **COVERED** |
| `packet01` (parsing; + `xdp_vlan01/02`) | P1+P2+P3+P4 · E0 · T0 | **COVERED** |
| `packet02` asg1 (port rewrite + incremental csum) | P3 · E2 · T0 | **COVERED** |
| `packet02` asg2 (`vlan_tag_pop`, −4B) | **E5** | **COVERED** |
| `packet02` asg3 (`vlan_tag_push`, +4B) | **E4** | **COVERED** |
| `packet03` asg1 (ICMP echo server, MAC/IP swap, TX) | E1/E2 · T0 | **COVERED** |
| `packet03` asg2 (redirect to fixed port) | E1 · T0 | **COVERED** |
| `packet03` asg3 (MAC→port map + devmap redirect) | **T1** · E1 | **COVERED** — both maps are control-plane written, data-plane read: table-like, not state |
| `packet03` asg4 (`bpf_fib_lookup`) | E2 · T1+T4 | **+ADD** LPM (the helper fuses an LPM route lookup with an exact neighbor lookup) |
| `basic03`/`basic04` (counters) | T3 | **+ADD** counters — but see Discovery 1 |
| `tracing01` (tracepoint + `bpf_map_update_elem`) | — | **REFUSED** — data-plane table write (learning) |
| `tracing02` (cpumap/devmap queue telemetry) | — | **REFUSED** — queue signals |
| `tracing04` (`bpf_perf_event_output`) | — | **REFUSED** — packet-to-host channel |
| `advanced03` (AF_XDP) | — | **REFUSED** — RMW state steering forwarding; XSKMAP host sockets |
| *(`experiment01-tailgrow`)* | — | *not graded* — tail edits, out of scope by design |

`packet03` asg3's two maps are the important distinction: a map the data
plane only **reads** is a table (COVERED); a map the data plane
**read-modify-writes to steer forwarding** (`advanced03`'s
`if ((*pkt_count)++ & 1)`) is state (REFUSED). Same BPF construct,
opposite verdict — the line is *who writes it*.

## Gibb parse graphs (ANCS 2013, Fig. 3)

| Graph | Types | Verdict |
|---|---|---|
| `simple` | 5 | **COVERED** — P1/P2/P3 fixture |
| `enterprise` | 10 | **COVERED** — P4 fixture (QinQ; note `max_var` makes the VLAN bound *shared*: 2 tags total, not 2 each) |
| `service_provider` (fixed + prog) | 4–5 | **COVERED** — lookahead fixture (MPLS ≤5) |
| `edge` | 6 | **COVERED** — lookahead + nesting fixture; **folds in**, see below |
| `datacenter` | 12 | **COVERED** — nesting fixture (VXLAN/NVGRE → inner Ethernet) |
| `big_union` | 21 | **COVERED** — scale fixture |

**PP v0 parses every graph. It forces no new instructions.** The reason is
`EXT`: it is not a byte extract but an **11-bit bit-offset, non-consuming
read reaching 255 bytes past the cursor**. Sub-byte fields (IHL, `bos`,
`dataOffset`) come free with no mask or right-shift, and MPLS lookahead is
`ext rd, boff=32, sz=4` with the cursor still on the label — no
speculation, no rewind, and it *traps* rather than reading garbage past
the buffered window.

Only two variable-length headers exist in the whole corpus (IPv4 `ihl*4`,
TCP `dataOffset*4`) — both power-of-two multipliers, exactly what
`SHL`+`ADVR` computes. Every repetition is bounded. Nothing is unparseable.

## xISA — 5 walkthrough examples

| Example | Coords | Verdict |
|---|---|---|
| `cross-connect` | P1 · E0 · T1 | **COVERED** today (4 xISA instrs → 4 Nanuk instrs) |
| `simple-ipv4` | P2 · E0 · T4 | **+ADD** LPM |
| `ipv4-validation` | P2 · E2 · T4 | **+ADD** LPM |
| `network-calculator` | P2 · E1+**E3** · T1 | **+ADD** reg-reg ALU (mandatory — E3 is impossible without it) |
| `ipv4-counters` | P2 · E2 · T3+T4 | **+ADD** counters + LPM; **byte counter was BLOCKED** — see Discovery 1 |

The corpus is **MAP-heavy and parser-light**: every xISA parser advances a
*fixed* 20 bytes for IPv4 — even the one that validates IHL. Nothing in it
exercises P3–P6, E4, E5, or T2. And **all five set `FrameDelta = 0`**:
xISA's own examples never change frame length, so Nanuk's headroom and
head-delta doctrine gets *zero* validation from the donor. Our
`nanukproto` tunnel remains the only thing exercising that axis.

## Discoveries — capabilities on neither list

### 1. Byte counters need frame length — and the fix is *implicit*, not an instruction

All three program corpora demand byte counters (xdp `basic03` asg1 and the
`xdp_stats_kern.h` boilerplate compiled into every `packetXX` lesson; p4
`link_monitor`; xISA `ipv4-counters`). Nanuk cannot read frame length:
`plen` exists internally but no instruction exposes it, and there is no
probe workaround — a window violation is a *fatal error halt, not a
branch*.

The audits initially disagreed on whether this is a hard block. It is not,
and the resolution is a better design than any of the three alternatives:

- xISA needs `SIZEQUERY` because **its `COUNTER` takes an explicit delta
  register**. On the good-IPv4 path it computes `bytes = ipv4.total_length
  + L2 offset`; on all five malformed/non-IPv4 arms no header field can
  supply a length, so it must query the hardware.
- **If Nanuk's count instruction supplies the byte delta implicitly —
  `COUNT tbl, idx` ⇒ `packets += 1; bytes += plen` — then no program in
  any corpus needs to read the length at all.** The counter *hardware*
  consumes `plen` (which the core already knows, streaming); the
  *instruction set* never exposes it.

**Decision: implicit-`plen` counters. No `SIZEQUERY`, no length
instruction, no second system md slot.** This preserves the core-redesign
ruling that slot 0 is the only system slot, and it is strictly less
machinery than either alternative. Side benefit: Nanuk counts *wire*
bytes, where xISA counts the IP header's self-declared length — an
attacker-supplied field it must checksum-validate first.

### 2. Reg-reg ALU must include AND

The accepted addition was written `ADD/SUB/OR/XOR`. Both calculators
disagree: p4's `calc` implements `+ − & | ^`, and xISA's implements
`ADD/SUB/AND/OR/XOR` (plus a shift-and-add `MUL` *routine*, not an
instruction). **The set is `ADD`, `SUB`, `AND`, `OR`, `XOR`.** The one
program motivating the addition needed an op the list omitted — the suite
catching an error on its first pass.

`MUL` is *not* needed: shift-and-add is expressible, and Nanuk's 64-bit
GPRs delete xISA's entire carry-propagation dance (it hand-builds a 64-bit
accumulator from 32-bit words). Nanuk's flaglessness costs nothing here
because its register width pays for it.

### 3. `LOOKUP`'s hit path is under-specified — the biggest real gap

The ISA defines the *miss* branch and says "hit continues." It does not
define **what a hit returns**, and the corpora pin this down hard:

- **Action data width.** `ipv4_forward(macAddr_t dstAddr, egressSpec_t
  port)` returns 48 + 9 = **57 bits** — fits one 64-bit register, but only
  just. `load_balance`'s `set_nhop` returns 89 bits (refused anyway).
- **Action *selection*, not just data.** Four exercises need per-entry
  action dispatch. `calc`'s single table dispatches to five different
  action bodies; xISA does it with a table whose values are **jump
  addresses** plus a register-indirect `BR`.

Nanuk's answer is a `BEQ` chain on a returned action-id (12 instructions
vs. xISA's 3). **The behavior is reachable; the lesson is not** — xISA's
point is that the control plane can redefine the data plane's *control
flow*, adding an opcode without recompiling. That is the one place in the
whole audit where Nanuk lands on the *impossible* side rather than the
*verbose* side, and it is a deliberate deviation worth naming rather than
discovering.

**Action required:** specify LOOKUP's hit result (destination register(s),
action-id convention) before any of this is built. This is a gap in what
already exists, not a new feature.

### 4. MAP cannot test header presence — and `ipv4-counters` breaks the idiom

`map_ttl/fwd.asm` turns this into a virtue: "totality is the guard, no
`hdr_present` test needed" — a `LD` from an absent header is a defined
fatal error-halt, which *is* the drop we wanted. That works exactly as
long as **the absent-header action is drop.**

`ipv4-counters` breaks it: it must **count** the non-IPv4 packet and *then*
drop. It needs a real branch on presence, and MAP has none (`LDMD` reads
only md slots; `hdr_present` lives in the PP struct, invisible to MAP).

Fix needs no ISA change — a **PP→md header-present bitmap convention**
(`STMD` a bitmap in the PP; `LDMD` + `ANDI` + `BEQ` in MAP). But it must
become a *documented md convention*, because today the only sanctioned
answer is "die."

### 5. `flowcache` — replication-as-bitmap cannot express a divergent copy

`clone_preserving_field_list(CloneType.I2E, CPU_PORT_CLONE_SESSION_ID, …)`
clones to a CPU port, and **the copy gets a 4-byte `packet_in` header
prepended while the original does not**. A port bitmap fans out *identical*
frames. This is the one place in the corpus where "no traffic manager, no
egress pass, replication = bitmap" structurally fails.

It also wants an indexed counter array keyed on a *packet-field slice*
(`hdr.ipv4.dstAddr[5:0]`) — not a counter attached to a match table — plus
table idle-timeout.

**This is a new call, not an old one to cite.** Recommendation: **REFUSE**,
with the reason stated as *no egress pipeline / no per-copy processing* —
a first-class architectural boundary, and the honest counterpart to the
single-ISA and no-deparser doctrines. `flowcache` is also the newest
exercise in the repo (2025) and the only one of the 13 that needs it.

### 6. `CSUM` is narrower than we thought

UDP/TCP/ICMPv6 checksums cover a **pseudo-header** (src/dst IP, protocol,
L4 length) that is *not contiguous* with the L4 byte range — and TCP's
length is not in the packet at all (`ip.tot_len − ihl*4`). A
ones-complement *range* checksum structurally cannot compute them.

The corpora are still satisfiable, but **not because of `CSUM`** — every
L4 checksum in xdp-tutorial is patched *incrementally* with immediate adds
(`check += htons(1)`). `CSUM`'s clean use is the IPv4 header checksum
(contiguous, no pseudo-header), where it also does double duty as a
*verify*: a valid header sums to `0xFFFF`, so `CSUM` returns 0.

E2's justification narrows accordingly. Not a defect — a correction to what
we claimed the instruction was for.

### 7. Idioms and parameters (no ISA change, but write them down)

- **The headroom is legal read/write scratch.** `st r, h_frame, -8, 8`
  then `ld` it back; at `send 0` the drain starts at index 32, so the
  scratch is never transmitted. This synthesizes a **right shift**
  (`SHLI` + store + narrow load), a **`CONCAT`** (the VRF++DIP key), and a
  sign-bit extract. It is doing a lot of quiet work — three audits leaned
  on it. Document it as an idiom.
- **No ordered compare** (`BLT`/`BGE`). Demanded by `IHL >= 5`,
  `TTL >= 2`, `ttl <= 1`. All workaroundable here (small domains: enumerate
  the bad set — or, elegantly, an exact table `{ihl → ihl*4}` giving
  validation *and* the header byte-length in one `LOOKUP` with fused
  branch-on-miss). A general `A < B` needs reg-reg `SUB` + sign-bit
  extract. **Out of scope by the boundary rule** — but decide it knowingly.
- **No right shift in MAP.** Walking mask replaces `B >>= 1` in the
  multiply loop at equal cost. Not a blocker.
- **Step budget and GPR count.** A 32-bit shift-and-add multiply runs
  ~224 steps against a 256 budget and wants 6 live values against 4 GPRs.
  Both are *parameters*. Failure mode would be `ERR_STEP_BUDGET`, which
  reads like a bug rather than a limit. **Decide before writing the
  calculator: raise the budget, or spec the calculator with 16-bit
  operands.**
- **Metadata window pressure.** 8 slots, 7 usable after slot 0. A
  VLAN-depth-10 collection (xdp `xdp_vlan02_kern.c`) wants 10; the
  header-present bitmap (Discovery 4) wants one more. Watch it.
- **Mid-frame splice, revisited.** Only `flowcache` does a *true* head push
  at offset 0. Every other p4 push/pop is a shallow splice
  (`basic_tunnel` inserts 4B *after* Ethernet). Emulable exactly as VLAN
  push already is — head delta, then relocate the prefix backwards into
  headroom with LD/ST. **E4/E5 must be defined to include the
  prefix-relocation idiom.** Worst prefix is MRI's 38 bytes.

## Consequences for the ladder

**Parser track gains a rung and splits another.** No rung as originally
written forced a peek *past* the current header — but MPLS carries no
next-protocol field at all (`bos` says the stack ended, not what follows;
you must read the 4 bits beyond it). That is Gibb's Challenge #3, and the
reason his DSL has `pseudo-fields`. QinQ and MPLS are therefore *not the
same capability*: VLAN repeats with **complete** information, MPLS repeats
with **incomplete** information.

| # | Benchmark | Forces | Fixture |
|---|---|---|---|
| P1 | Fixed stack | EXT/ADVI/SETHDR/HALT | `simple` |
| P2 | Demux | multi-way compare-and-branch | `simple` |
| P3 | Variable-length header | computed advance (`SHL`+`ADVR`) | `simple` (IHL, doff) |
| P4 | Bounded repetition | loop with proved bound; shared counter | `enterprise` (QinQ) |
| **P5** | **Incomplete information (NEW)** | **non-consuming lookahead past the current header** | `service_provider` + `edge` |
| P6 | Nesting | same header type twice | `edge` (EoMPLS) + `datacenter` (VXLAN) |
| P7 | Scale | imem, header slots, step budget | `big_union` |

**Gibb's `edge` graph folds in as a use case but earns a rung as a
capability** — and P5 + P6 together *are* the edge graph, which is the
strongest possible form of "fold it in."

Scale sizing (`big_union`, 21 header types, worst path 15 header
instances): imem is comfortable (~150–250 of 1024 words), but the **step
budget runs to ~64%** — dominated by `MOVI`+`BEQ` dispatch chains, since PP
has no compare-immediate — and **header slots hit 15 of 16**. With 21
header *types* you cannot statically assign one slot per type; you must
alias mutually-exclusive types or collapse repeats. That trade-off is what
P7 measures.

Note also the claim this licenses: Gibb's central result is that
**field-extraction buffers dominate parser area** (big-union extracts 617
bits on its worst path). Nanuk's metadata window is 128 bits because the PP
produces *offsets*, not a PHV. We deliberately opt out of the structure the
paper says dominates cost — a claim worth making loudly rather than
accidentally.

## Coverage and minimality

**Coverage holds.** Every program in all four corpora is accounted for:
covered, covered-with-an-accepted-addition, refused with a named reason, or
(one case, `flowcache`) escalated to a new decision.

**Minimality holds, with one cut and one addition.** Every benchmark is
demanded by at least one corpus program. Two observations:

- **T2 (exact → drop / stateless ACL)** is demanded by *no* corpus program
  as a standalone — p4's `firewall` is refused for its Bloom filter, and no
  xdp lesson or xISA example does a pure ACL. It survives only on the
  strength of the deployed shape (Cloudflare L4Drop). **Under a strict
  reading of the boundary rule, T2 should be cut.** I recommend keeping it
  and marking it explicitly as *not corpus-demanded* — but the rule says
  cut, and that is your call.
- **Ternary matching is demanded by zero programs across all four
  corpora** (0 of 18 p4 tables). The refusal is free. Strong result.

## Verdict on the three accepted additions

All three survive, all three are demanded, and the set is now precise:

1. **LPM tables** — 8 p4 tables, xdp `bpf_fib_lookup`, 3 of 5 xISA
   examples. The single highest-value addition. Keys fit 64 bits (xISA's
   widest is a 44-bit VRF++DIP), so no multi-word key requirement follows.
2. **Counter tables** — xISA `ipv4-counters`, p4 `p4runtime`, xdp
   `basic03/04`. **Double counters (packets + bytes) with an implicit
   `plen` byte delta** (Discovery 1). This is Nanuk's first data-plane
   table *write* — name the boundary against learning (refused) explicitly.
3. **Reg-reg ALU** — `ADD`/`SUB`/`AND`/`OR`/`XOR` (Discovery 2). Demanded
   by exactly one program, which appears in two independent corpora.
