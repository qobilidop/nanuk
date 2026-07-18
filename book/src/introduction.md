# Introduction

This is a book about building a packet processor — the whole thing, from the
instruction set down to the silicon and back up to the programming language you
write for it. Not a model of one. Not a diagram of one. A real, running vertical
stack small enough that a single person can hold every layer in their head, and
real enough that the final demo is two unmodified Linux hosts exchanging traffic
through RTL we wrote ourselves.

That is the whole pitch, and it is worth being honest about how unusual it is.
Most systems books pick a layer and go deep: a book about compilers assumes a
processor, a book about processors assumes an ISA, a book about networking
assumes a switch you buy. Nanuk refuses to assume any of them. We are going to
design an instruction set, write its formal specification, generate an emulator
from that specification, build a hardware implementation, prove the hardware
agrees with the specification, wrap the hardware in a switch, and then — because
by that point we will badly want to stop writing machine code by hand — build an
assembler, an intermediate representation, an embedded programming language, and
a symbolic executor that can invent test packets out of thin air. Every one of
those layers is a chapter, and every one of them exists in the repository as
code you can run.

## What Nanuk is

Nanuk is a **programmable packet processor**: a device whose behavior —
*packets in, packets out, plus some metadata and a verdict* — is decided by a
program loaded after the chip is fabricated, not baked into the gates. That is
the same trick a CPU plays. A CPU is silicon that does arithmetic whose *meaning*
is decided by the program in memory. A programmable packet processor is silicon
that does packet processing whose *policy* is decided by the program in
instruction memory. The bet of this whole project is that packet processing is
worth making programmable in exactly that ISA-based sense, and that the cleanest
way to learn how is to build the smallest honest example end to end.

We start with the parser, because parsing is the most self-contained piece of a
packet processor: the input is crisp (bytes), the output is crisp (which headers
were present, where they sat, and a verdict of accept or drop), and it is
genuinely interesting to make programmable. A packet arrives as an opaque run of
bytes. Somewhere in there is an Ethernet header, maybe a VLAN tag or two, maybe
IPv4 with options, maybe UDP. A parser's job is to walk that structure and
report what it found. Doing that with a *program* rather than fixed logic is the
thing commercial parser engines actually do, and it is where we begin.

Then we build the parser's sibling: a **match-action processor** whose lookup
tables *are* the forwarding policy. Together — one parser feeding one
match-action engine — they form the Nanuk core, and around that core we build a
switch. By the end, "load a different program" is the same as "the network goes
dark," because the program is the policy, all the way down to the wire.

## The ISA bet, and the ladder that keeps us honest

There is an older, more established way to make packet processing programmable:
the P4/PISA model, where you describe a parse graph and a pipeline of
match-action tables and a compiler maps that onto reconfigurable stages. We are
deliberately *not* doing that. Nanuk is built around a tiny processor with its
own instruction set — an ISA — in the spirit of Xsight Labs' open xISA rather
than a P4 parse-graph abstraction.

Why? Four reasons, and they run through everything that follows. Real silicon
already works this way; the ISA route unlocks decades of mature tooling that the
formal-verification and computer-architecture worlds built for instruction sets;
an ISA is the *truthful* layer, because real chips implement parse graphs on
programmable parser engines under the hood anyway; and if we ever want the P4
abstraction, it can sit on top as a frontend later — turning the reference P4
software switch into a differential-testing oracle rather than a competitor.

That last idea — *diffing against an oracle* — is not a nice-to-have. It is the
spine of the project. From the very first stage we hold ourselves to a four-rung
evaluation ladder, and "done" means climbing it:

1. **Instruction-level conformance.** The hardware is cosimulated against an
   emulator generated from the formal spec — the RISC-V world's discipline,
   applied to a packet parser.
2. **Program-level differential testing.** Feed the same program and the same
   corpus of captured packets into the golden model and into the
   implementation; diff the extracted headers and the verdicts. If they
   disagree, someone is wrong, and now we know.
3. **System-level end-to-end.** Put the whole thing inside a full-system
   network simulation — real Linux, real TCP — and watch traffic flow through
   our RTL.
4. **Physical characterization.** Synthesize it and read the area and timing
   reports.

You cannot run rungs one and two against an oracle that shrugs. If the spec says
some input is "undefined behavior," there is nothing to diff — the oracle is
allowed to do anything, so agreement is meaningless. That single observation
forces one of the strongest constraints in the whole design.

## Two principles worth stating up front

**Total, deterministic semantics — no "undefined" anywhere.** Every behavior of
every ISA and every IR is defined: out-of-bounds reads, over-depth parses, illegal
instructions, all of it. There is no corner where the spec throws up its hands.
This is not formal-methods pedantry for its own sake; it is load-bearing for the
main track, because it is what makes the oracle diffable. It also happens to make
the symbolic-execution work later almost free. When we say a runaway program
counter in zeroed instruction memory *halts with a diagnosable error* rather than
NOP-sledding into oblivion, that is this principle biting down: even the
degenerate cases have a defined, observable outcome.

**Zero-copy, by construction.** A classic packet pipeline parses a packet into a
header vector, lets you edit it, and then *deparses* — reserializes the edited
headers back into bytes. Nanuk has no deparser, on purpose. The parser never
copies header bytes into a reconstructed vector; it emits *offsets* — "the IPv4
header started at byte 14" — plus a small block of standard metadata. Downstream
stages read fields straight out of the original buffer through those offsets.
No copy, no deparse, no reserialization step to get wrong. This one decision
shapes the ISA, the hardware, and the reason there are exactly two processors and
not three. We will earn it slowly.

## How the book is built

Nanuk was not built in the order this book presents it, and it was certainly not
built without mistakes. We designed an intermediate representation the honest way
— by *extracting* it from a working compiler rather than speculating about it up
front, because IRs designed before their consumers exist tend to be wrong. We
walked back an entire planned MLIR track after deciding its weight did not belong
in the repository. We deferred the tape-out to future work once we realized the
full-system simulation runs *the same RTL* cosimulated against the same spec, so
silicon would add narrative value but not new evidence.

The book inherits that candor. Every chapter opens with a short note on what you
are about to build or understand, and every chapter closes with a section called
*Where this bit us* — a real moment from the lab notes where the design fought
back and taught us something. Those sections are not garnish. They are the reason
the book exists: it is distilled from the decision records and lab notes we kept
while building, on the theory that the interesting part of engineering is not the
clean result but the argument that produced it.

## The map

**Part I — The Spec** designs the parser ISA, writes it in Sail, generates an
emulator from it, and then confronts the question the naming forces: why exactly
*two* processors?

**Part II — The Machine** designs the match-action ISA, builds both processors in
real hardware with Amaranth, cosimulates them against the spec, and wraps the
result in a switch that pushes real traffic.

**Part III — The Languages** climbs back up: an assembler and an instruction-set
simulator, the protobuf IR, the embedded language you actually write programs in,
a symbolic executor, and a browser playground that runs all of it live.

**Part IV — Applications** puts the thing to work: a graded benchmark suite that
the ISA had to answer to, and a real stateless IPv4-to-IPv6 translator — the same
quiet translator running in every phone on a v6-only mobile network — built on the
exact core we designed for parsing Ethernet.

One more thing before we start. The name is Inuktitut for polar bear, with a
"nano-" prefix hiding inside it, and it should always travel with its tagline
because "Nanuk" alone collides with a Czech word for popsicle. We will write it
**Nanuk** in prose and `nanuk` in code, and the two processors are **PP** — the
parser processor — and **MAP** — the match-action processor. Hold onto those
three names; the rest of the book hangs off them.

Let's build a packet processor.
