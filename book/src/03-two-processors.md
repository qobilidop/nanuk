# Two Processors, Not One

*In this chapter we answer a question the project has been dodging: why are there
exactly two processors? Not one unified machine that both parses and edits, and not
three with a separate deparser — two. The answer turns out to be the sharpest
argument in the whole design, and it is worth understanding before we build any more
hardware, because it is the reason the hardware has the shape it does.*

By now we have a parser processor — PP — fully specified. The natural next step is to
make packets *do* something: forward them, rewrite a TTL, push a tunnel header. That
is match-action, and it needs its own capabilities: table lookups, arithmetic on
fields, writing bytes back into the packet. The question is where those capabilities
should live. Three answers present themselves, and the project committed hard to the
middle one: **two sibling ISAs — a parser processor and a match-action processor —
and it is genuinely worse to merge them.**

This chapter is the argument for that commitment. It matters because "two processors"
is not an implementation convenience we backed into; it is a claim about where the
real boundaries in packet processing are, and getting it right shaped every layer
above and below.

## The naive objection is not the argument

Start by throwing out the weak version of the case, because it is the one that first
comes to mind and it is wrong. You might think two ISAs means "too many instructions
to put in one machine." It does not. The union of the parser's eleven instructions and
the match-action processor's dozen, minus the moves they share — `MOVI`, the branches,
`JMP` — is only about nineteen instructions. That is still small, still RISC-sized, and
the encodings coexist comfortably in a single six-bit opcode plane. Opcode count is not
the problem. If the only cost of merging were a slightly bigger instruction list, we
would merge in a heartbeat.

So we have to name the *real* reason, and there are five of them.

## Five reasons the merged machine is worse

**The ISAs are machine-state disciplines, and the disciplines conflict.** An ISA is not
really a list of instructions; it is a discipline over machine state. Look at what each
discipline actually is. The parser: a monotonic cursor that only moves forward,
bit-granular reads, a 256-byte window that is *read-only*, and its job is to *produce*
offsets and metadata and then HALT with a verdict. The match-action processor:
header-relative addressing with signed byte offsets, byte-granular access, a read-*write*
window with headroom in front of it, and its job is to *consume* those offsets and
metadata and then SEND or DROP. Those are not two lists of instructions bolted together.
A union machine carries the *cross product* of both state models — read-only and
read-write, forward-only cursor and header-relative offset, bit and byte — not the sum
of two opcode lists. This is the **state-model conflict**, and it is the root of
everything else.

**Header bases do not exist during parsing — creating them is the parser's whole job.**
The match-action processor addresses memory as "the IPv4 header, plus four bytes." That
addressing mode is only meaningful *after* the header offsets have been filled in — and
filling them in is precisely what the parser does. So the handoff from parser to
match-action is not an interface you could inline away by merging the two into one
program. It is the *data structure* that makes the second addressing model mean anything.
The parse result is the match-action processor's memory map. You cannot merge away a memory
map.

**A merged machine has to grow a mode bit, and the mode bit is the boundary in disguise.**
Suppose you tried to merge anyway and keep the semantics total, as Chapter 2 demands. You
would have to gate the conflicting behaviors behind a *phase bit*: in the parse phase the
window is read-only and the cursor is live and table lookups trap; in the edit phase writes
are allowed and the cursor is frozen. But now totality has to be defined over *instructions
times phases* — every instruction's meaning has to consult the phase — and the merged model
is strictly *bigger* than the two separate models combined. The union does not eliminate the
boundary between parsing and editing. It smears it across every instruction's semantics. This
is **mode-bit decay**, and it is the decisive technical point: merging does not remove the
seam, it hides it everywhere.

**The split is not an ISA artifact — it recurs at every level we build.** This is the one we
came to call the diamond problem. The parse-versus-act distinction is not something the ISA
imposes; it shows up independently at every layer. In the language, there are parse states and
there are tables and actions. In the intermediate representation, there is a parser program and
a match-action program, and the validators reject each other's terminators. The demos come in
two genres. The playground debugger has two phases. The symbolic executor has two tractable
analyses whose composition is sequential. Unifying *only* the ISA makes the stack two-shaped
above and one-shaped below — you throw away the shape information at the ISA and every tool
above has to reconstruct it. Keeping the same shape at every level is exactly what makes the
four-rung ladder and level-by-level diffing work.

**Verification stays tractable only if the state stays small.** Symbolic execution of the parser
works because parser state is small and the buffer is read-only — the path enumeration does not
have to reason about writes. A merged machine drags the write window and the tables into every
single path the symbolic executor explores. Two small total-semantics models are each analyzable;
one big one is where analyzability goes to die.

## The honest case for merging, and why it loses

A good book steelmans the option it rejects, so here is the strongest case for *one* ISA — and it
is genuinely strong.

The best practical argument is that merging **halves the artifact count**. Each processor gets four
semantics implementations — the Sail model, the reference interpreter, the instruction-set simulator,
and the RTL — so two processors mean eight artifacts to keep in sync; one processor would mean four.
That duplication is real, and we should not pretend the mirror-with-a-tripwire discipline makes it
free. There is more. A single run-to-completion machine would make *decapsulate-then-reparse* trivial
— it is just "parse again after the edit" — and the whole question of passing metadata across the
parser-to-match-action boundary would simply cease to exist. And there is precedent: Intel's IXP,
Netronome's NFP, and eBPF/XDP in software all run one ISA where a single program parses, decides, and
edits.

So why does the merged design still lose? Because every one of those wins is the right answer to a
*different* architecture question. Pooling identical general-purpose cores wins when you do not know
the workload mix in advance. A single ISA wins when a huge software ecosystem — eBPF's verifier, JIT,
and tooling — amortizes the cost of generality. Run-to-completion wins when you have already given up on
the pipelined-throughput story. None of those hold for Nanuk. Nanuk's architecture story *is* the
pipeline — the xISA and EZchip lineage of task-specialized stages — and its "ecosystem" is a few tiny
per-stage firmware programs, not a world of software worth building a general machine for. The single ISA
is a beautiful answer to a question we are not asking.

We even considered the compromise — a RISC-V-style shared base ISA (the moves, the branches, the jump)
with a parser extension and a match-action extension. We rejected that too, and for a sharp reason: the
sharing that is genuinely cheap *already exists* — shared assembler core, shared encoding idioms, shared
Sail machinery, shared "school rules" — without the coupling. A formal shared base would let a
match-action-driven change to the base ripple into the parser ISA, which we have *frozen*. That is
aesthetics purchasing a versioning problem, and we did not buy it.

## Why not a third processor, either

If two is right, why not three — a separate deparser, the way the P4/PISA world has one? Because a
deparser is the tax you pay for a representation Nanuk deliberately does not use. PISA needs a deparser
because its parser rips headers apart into a header vector; something has to reassemble them, and that
something is the deparser. Nanuk chose zero-copy offsets and metadata instead. Nothing is ever
disassembled, so nothing needs reassembly. **There is no deparser, by construction.** Editing — TTL
decrements, tunnel pushes — folds into the match-action processor as in-place writes plus a signed
head-delta applied at send time, which is exactly what xISA does, and exactly the discipline that Linux's
`skb_push`/`skb_pull` and DPDK's headroom use, promoted into the ISA. The separate modifier engine that
EZchip ships is a throughput and pipelining decision, not a semantic one, and at Nanuk's scale it buys
nothing.

There is a subtle objection lurking here that is worth surfacing, because it is the sharpest form of the
"state-model conflict" and it nearly argued us back into a third engine. The header structure the parser
produces — the offsets, the metadata — describes the *unmodified* packet. After the match-action processor
edits the frame, especially with a length-changing edit, that structure is *stale*. Does that argue for a
separate editor engine to keep it fresh? No — and the reasons are clarifying. The governing invariant is that
**bytes are authoritative; the parsed structure is a cached view with a validity window**. In our version-zero
design the staleness has *no observer* by construction: edits are staged, not committed — the header bases stay
pinned, the re-basing happens atomically at send, and once send fires the structure dies. The contract's scope
(parser to match-action) exactly coincides with the validity window (until send). And crucially, a separate
editor engine would not fix the staleness anyway — command-vector editors apply their edits even later, and the
outgoing packet's structure is exactly as stale. The editor split is a throughput decision, orthogonal to
staleness. When someone downstream genuinely needs fresh structure, the answer everyone in this camp reaches for
is *reparse* — xISA has an explicit packet-reparse, eBPF's verifier forcibly invalidates all packet pointers
after a length-changing edit and makes you parse again. Nobody adds an engine to keep metadata fresh. The rule we
wrote down: never ship silently-stale metadata past send.

## Where this bit us

The place this whole argument earns its keep is pedagogy, and that is the honest reason the project drew the
line at two. Two sibling ISAs teach Nanuk's actual thesis — that **an ISA is the interface contract for a job,
and different jobs get different contracts**. The contrast pairs *are* the curriculum: bit machine versus byte
machine, cursor versus header-relative addressing, read-only versus read-write, halt-with-a-verdict versus
send-with-a-delta. Those contrasts are only visible because there are two ISAs to set side by side. Doing the
shape-an-ISA-to-a-job exercise *twice*, for two genuinely different jobs, is the course. Merging deletes the
second half of it — and a unified general-purpose ISA teaches general-purpose ISA design, which RISC-V already
teaches better than we ever could.

Here is the bit that stung in the good way. This decision was not made once and left alone. After the whole
match-action arc was built, we deliberately attacked the call from the opposite direction — *what if we had
merged?* — and the counterattack produced sharper arguments than the original four: the state-model conflict,
the mode-bit decay, the diamond problem, and the framing that finally settled it. Three engines is too many —
there is nothing to reassemble. One is too few — the boundary is load-bearing, semantically and verificationally
and pedagogically. **Two is the minimum number of engines that makes the stage contract architectural rather
than a software convention.** We only got that clean formulation by trying, in earnest, to prove ourselves wrong.

With the boundary settled, we can design the second processor for real. That is Part II, and it opens with the
match-action ISA.
