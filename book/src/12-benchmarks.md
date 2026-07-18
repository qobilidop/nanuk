# The Benchmark Suite

**What you'll understand:** why Nanuk's benchmark suite doesn't measure the ISA
— it *defines* it. You'll see how a suite becomes binding (a program it can't
express is a defect until fixed or explicitly refused), why we drew a hard fence
around exactly four corpora and no more, and why the list of things the suite
*refuses* is half the coverage proof rather than an appendix. And you'll meet the
audit that caught our own accepted feature list getting an instruction wrong.

Every earlier chapter grew the machine to run programs *we* wrote. That's a trap:
you unconsciously write programs the machine can already run, and the ISA
converges on "whatever we happened to try." The benchmark suite exists to break
that loop by importing requirements from outside — real teaching corpora we
didn't author — and treating each program not as a test but as a *demand*.

## The suite drives the ISA

The doctrine is stated plainly in the design: *"The suite drives the ISA. A
program in the suite is a requirement: anything it cannot express is a defect in
the ISA until either fixed or explicitly moved to the negative set with a stated
reason."* This inverts the usual relationship. Benchmarks don't grade a finished
design; the design gets *derived* from what the benchmarks demand.

The doctrine exists because the first attempt at deriving the ISA failed, and the
failure is instructive. The MAP ISA was originally shaped by a three-program
"expressiveness razor": L2 exact-match forward, TTL rewrite, tunnel push/pop.
*"It looked like a methodology. It was a coincidence."* Those three programs span
the *edit* axis — do nothing, rewrite in place, grow and shrink the head — but
they sit on the **same point of the table axis**: one exact-match lookup, no
state. So counters, LPM, and per-flow state stayed invisible, because nothing in
the razor asked for them. The fix was to recognize the two axes explicitly (what
a program does to the *bytes* versus how it *decides*) and populate a real ladder
along each.

The sharpest single instance of the suite driving the ISA is E3, the calculator.
It's the only program that appears in *two independent corpora* — p4's `calc` and
xISA's `network-calculator` — and the only one Nanuk failed outright: the entire
MAP ALU was immediate-only (`ADDI`/`ANDI`/`SHLI`), with no register-register
form at all, so there was nowhere to put a second operand that came off the wire.
The test file's header records the verdict: *"The benchmark that changed the
ISA."* v0.1 added `ADD`/`SUB`/`AND`/`OR`/`XOR` reg-reg — five opcodes demanded by
exactly one program that happened to appear twice. The instruction word still
carries the scar in a comment, as we saw in Chapter 7.

A suite that only ever *adds* things, though, isn't a razor — it's a wish list.
The same instrument cut a benchmark (more on that below) and exposed a
specification gap without adding any feature at all. Working in both directions is
what makes it a razor rather than a ratchet.

## The corpus boundary: four, and no more

The temptation with an import-requirements-from-outside strategy is that "outside"
is infinite. So the suite draws a hard fence: **four reference corpora, and no
more.** They are p4lang/tutorials (the canonical P4 curriculum, Apache-2.0),
xdp-project/xdp-tutorial (already graded along Nanuk's parse→rewrite→redirect
axes, GPL per-file), the Gibb et al. ANCS 2013 parse graphs (the parser benchmark
the field never standardized), and the xISA walkthrough examples (the donor
architecture's own ladder). *"Everything a corpus program demands is in scope;
nothing else is."*

Two things forced this fence, and both are worth knowing. The first is a
licensing surprise. The obvious academic candidate — Domino/Banzai — turned out
unusable twice over: all eleven of its programs are stateful by construction, so
*zero* of them run on Nanuk, and it ships **no license file at all** — nor do
Chipmunk, Marple, Sonata, or Gibb's own `parser-gen`, whose `LICENSE` is literally
zero bytes. The conclusion is a nice observation about the field: *"The cleanly
licensed material in this field is the pedagogical material. A teaching corpus
assembled from teaching corpora."* And critically, the programs are **re-derived,
never vendored** — the tasks are borrowed, not the listings. Nanuk owns every
line of program text it ships; it borrows only the *idea* of what each program
must do.

The second forcing function is the cliff. Applied literally, the boundary excludes
5 of p4lang's 13 exercises — the ones behind hashing, per-flow register arrays,
data-plane learning, or a queue-depth signal. Taking them would grow Nanuk a hash
unit, indexed state arrays, table writes, and a traffic manager: a two-to-three-times
larger arc than the entire match-action extension. So the decision is *"corpora
minus the cliff,"* which lands the covered set *"very close to what xISA actually
is — which is what a shrunk-xISA lineage claim should predict."* The fence isn't
arbitrary; it's the boundary that keeps Nanuk a coherent small machine instead of
a bad Tofino.

The boundary also works as a *cut* mechanism. A stateless-ACL benchmark was
removed precisely because its only justification was an out-of-corpus deployed
shape (Cloudflare's L4Drop) — *"exactly the argument the boundary rule exists to
reject."* The behavior stays expressible; the benchmark went, because no corpus
program demanded it standalone.

## The refusal ledger: half the coverage proof

Here is the idea that makes a binding suite safe. If the suite only ever says
"you must express this," it will eventually demand hashing, per-flow registers,
learning, and a queue model, and *"Nanuk will have grown into a bad Tofino."* So
the suite must also state, on the record, what it **refuses** — and that negative
set is *"half the coverage proof, not an appendix."* Each refusal names the
programs that would demand the feature and the reason the feature stays out.

- **Hash and per-flow register arrays** — the load balancers, Bloom-filter
  firewalls, flowlets, heavy hitters, all of Domino/Marple/Sonata. Refused as
  *"the cliff"*: neither half is useful without the other, and together they are
  a different machine. This is the same refusal that steered us away from Katran
  in Chapter 13 and toward SIIT, which needs neither.
- **Data-plane learning / per-flow state** — the learning switch, and a subtle
  pair of XDP programs that draw the line perfectly: *"a map the data plane only
  reads is a table (covered); a map the data plane read-modify-writes to steer
  forwarding is state (refused). Same BPF construct, opposite verdict — the line
  is who writes it."*
- **Ternary / TCAM** — switch.p4-class wildcard ACLs. The striking part: it's
  *"demanded by zero programs across all four corpora"* (a census of p4lang's 18
  tables found 9 exact, 8 LPM, 0 ternary, 0 range). *"The refusal is free, and
  we'd been carrying it as a cost."*
- **Queue-dependent programs** — ECN marking, INT-style per-hop telemetry. Nanuk
  has no traffic manager, so *"a queue-depth signal would be fictional."* The
  telemetry demo survives only in a hop-ID-only variant that reads no queue.
- **Per-copy processing (flowcache)** — this one earned its own doctrine.
  `flowcache` clones a packet to a CPU port and prepends a header *to the copy but
  not the original*. Replication in Nanuk is an egress port *bitmap* that fans out
  identical frames. *"This is not a gap to fill but a boundary to name: no egress
  pipeline, no per-copy processing"* — a first-class architectural refusal
  alongside single-ISA and no-deparser.

Refusing is a *feature* because a refusal is a decision with a rationale, and a
decision with a rationale is knowledge. "Nanuk can't hash" is a limitation;
"Nanuk refuses hashing because hash-plus-per-flow-state is a different machine,
demanded only by workloads with respectable stateless members" is a design. The
ledger converts every boundary from an embarrassment into a documented
architectural stance.

## What the audit caught

Once the suite was populated, four agents graded every program against the *real*
ISA — the Sail models and the ISS, not the design docs. Grading against the
implementation rather than the intention is what turned up the gaps, because the
docs describe what we *meant* and the code is what we *built*. Three findings
matter most.

**Reg-reg ALU, and the AND we forgot.** The accepted addition had been written
"reg-reg ALU: ADD/SUB/OR/XOR." Both calculators disagreed: p4's `calc` implements
`+ − & | ^`, and xISA's implements `ADD/SUB/AND/OR/XOR`. The one program that
motivated the whole addition needed an op the accepted list *omitted* — the suite
catching an error on its first pass. The set is five: `ADD`, `SUB`, `AND`, `OR`,
`XOR`. (Notably, `MUL` is *not* needed — shift-and-add is expressible, and
Nanuk's 64-bit registers delete xISA's carry-propagation dance.)

**The LOOKUP hit-path under-specification** — called the biggest real gap. The
ISA defined the *miss* branch and said "hit continues." It did not define *what a
hit returns*. Two things were genuinely unspecified: the action-*data* width (an
`ipv4_forward` action returns 48+9 = 57 bits, which fits one 64-bit register "but
only just"), and action *selection* — four exercises need per-entry action
dispatch, where a table's values are effectively jump targets. Nanuk's answer is a
`BEQ` chain on a returned action-id (12 instructions where xISA does it in 3), and
the audit's honest note is that *"the behavior is reachable; the lesson is not"* —
the one place the whole audit landed on "impossible to teach cleanly" rather than
merely "verbose." The action item is spec debt, not a feature: *specify LOOKUP's
hit result before building anything on top of it.*

**The headroom-as-scratch idiom.** The audit noticed that three separate program
audits leaned on the same quiet trick: the 32 bytes of headroom are legal
read/write scratch. Store to `h_frame, -8` and load it back; because the drain
starts at index 32 on `send 0`, the scratch is never transmitted. This one idiom
synthesizes a *right shift* (which the ALU lacks), a *concat*, and a sign-bit
extract. It was doing so much load-bearing work invisibly that the finding was
simply: *"Document it as an idiom."* A small ISA makes you notice the tricks that
a big one would let you paper over.

## Canaries

The suite guards its hard-won properties with two kinds of canary. The first are
capacity pins: the worst parser path costs **155 of 256 steps** and the union
program is **157 of 1024 instruction words**, and both numbers are asserted
exactly — *"regression canary: the worst path costs 155 steps."* If an
optimization or a new feature blows the budget, a test names the number that
moved.

The second, more interesting kind are *behavioral* canaries — programs written
specifically to catch a failure mode, checking not just the verdict but *how* it
was reached. A termination canary asserts that a sixth MPLS label is refused *in
the program text*, not merely by running out of step budget. A total-refusal
canary asserts that a GRE parser declining a packet is making a *decision*, not
suffering a window overrun — because *"a parser that errors where it should have
declined is a parser that will error on an attacker's packet."* An
overlapping-copy canary asserts the *whole frame* after a relocation, because
"payload intact" and "checksum valid" both passed while the source MAC came out
mangled — the same `memmove` aliasing hazard that would later bite SIIT's IHL
handling in Chapter 13. And an end-around-carry canary sweeps payloads until the
checksum's carry path actually fires, so the recovery code is *exercised*, not
merely present.

The pattern in every one of these is the same: assert the mechanism, not just the
outcome. A test that only checks the verdict passes for the right reason and the
wrong reason alike. A canary checks that the machine reached the right answer the
way the design says it should.

## Where this bit us

The suite's own numbers carry small, honest inconsistencies that are worth
seeing, because they show the difference between a projection and a measurement.
The worst-path step count is pinned at 155 and annotated "61%" — but 155/256 is
60.5%, and a pre-build estimate in the coverage doc had projected "~64%." The
integer 155 is the truth; the percentages are descriptive rounding around it. It's
a tiny thing, but it's the right tiny thing: the canary pins the *measured
integer*, and the prose around it stays approximate, so drift shows up as a
changed number rather than a re-argued estimate.

The deeper bite is philosophical. A binding suite is a commitment you can regret:
every program is a promise the ISA must keep, and the moment a corpus adds an
exercise you can't express, you owe either an implementation or a refusal with a
rationale. That's expensive on purpose. The alternative — a suite that measures
whatever the machine already does — is free and worthless, because it can never
tell you that you're wrong. The whole value of the benchmark suite is that it is
allowed to fail you, and the refusal ledger is what keeps that failure from
turning into unbounded scope. The suite drives the ISA; the ledger is the brake.
