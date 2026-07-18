# Symbolic Execution

**What you'll understand:** how the same parser IR that lowers to machine code can
be read *backwards* — turned into constraints and handed to a solver that invents
packets to drive every path — and why that only works because the ISA was designed
total and bounded from the start. You'll see witnesses validated three ways at
once, a solver conjuring a valid tunnel packet from nothing but a parser's
constraints, and an honest account of what "under-approximation" means and why it's
a feature rather than a hedge.

A parser program answers a question about a packet: given these bytes, what verdict?
Symbolic execution asks the inverse: given this program, what *packets* reach each
verdict? Instead of running the parser on concrete bytes, we run it on a *symbolic*
packet — bytes that are variables — and collect, along each path, the constraints
that path requires. A solver then finds concrete bytes satisfying those constraints:
a *witness*. The parser program, read this way, is a packet generator.

## Programs as constraint generators

The whole satellite is about 250 lines, and it's small for a designed reason. Total
semantics and bounded iteration were baked into the ISA *partly to make this cheap*
— you cannot symbolically execute a machine with undefined behavior or unbounded
loops, so the properties Chapter 3 introduced for the hardware's sake pay off again
here.

The packet becomes a Z3 byte array plus a symbolic length, and the interpreter's
semantics translate op-for-op into bitvector terms. The parser's windowed field read
— nine bytes concatenated, shifted to align, masked to width — becomes a `Concat` of
`Select`s over the symbolic array with a symbolic alignment shift: *"the same
algorithm as Sail's `read_pkt_bits`, in constraint form."* This is the mirror
doctrine again. The symbolic executor isn't a fresh model of the machine; it's the
interpreter's semantics re-expressed in constraints, so a divergence between symex
and interp is a bug in one of two things that are *supposed* to say the same thing.

Paths fork at exactly the points the machine branches. Every field read and every
cursor advance forks two paths: a feasible in-bounds path (continue with the
"read stayed in the window" constraint added) and a feasible header-violation path
(if running past the window is satisfiable, emit that verdict). Dispatch forks per
case, accumulating "not equal to the earlier cases" constraints so each case is
reachable only under all prior mismatches, and the default runs under all-mismatched.
And crucially, step accounting mirrors the interpreter *tick for tick* — the same
cost model from Chapter 8 — so each path carries not just an outcome *class* but an
*exact* predicted triple: verdict, error, and step count. The solver then evaluates
its model into concrete bytes, and out comes a witness packet that provably drives
that path to that verdict in that many steps.

## Witnesses proven three ways

A predicted verdict is a claim, and the symbolic executor could be wrong. So every
witness is validated, and validated *differentially*. The pure-IR test runs each
witness through the interpreter and asserts the interpreter reproduces symex's exact
predicted triple. The cross-check test (gated behind the cosimulation flag, because
it needs the built emulator) goes further: it assembles the program and runs each
witness on *both* the interpreter *and* the golden emulator, asserting all three
implementations agree on verdict, error, and steps.

The lab notes name the property this buys: *"each witness reproduces its exact
prediction on interp AND the golden emulator. A symex bug, an interp bug, or a Sail
bug would surface as a three-way disagreement."* This is the mirror-with-tripwire
doctrine at its most muscular. Four independent expressions of the parser's semantics
— the constraint model, the interpreter, the golden emulator, and the Sail spec
beneath it — are forced to agree on machine-generated inputs that no human chose.
When they agree, the agreement is strong evidence precisely because the inputs came
from the solver, not from a test author who might share a blind spot with the
implementer. The parity test also asserts full state reachability — the set of states
the paths actually visit equals the set of states declared — so a state no packet can
reach shows up as provably dead code.

## Inventing a valid tunnel packet

The most striking demonstration is a test literally named "symex invents a valid
tunnel packet." Its premise: hand the symbolic executor *nothing* but the nanukproto
parser IR — no example packet, no hand-crafted bytes — and ask it for a packet that
the parser accepts as a well-formed tunnel frame.

It works because the parser's accept path *is* a specification of a valid packet,
written as constraints. To reach the accepting state through the tunnel states, the
symbolic packet must have the right outer EtherType, the nanukproto magic `0x4E4B`
(ASCII "NK"), and the right version nibble — because those are exactly the
comparisons the parser makes on the way to `accept`. The solver, satisfying those
constraints, produces bytes that *are* a valid tunnel packet, and the test confirms
it by running the invented bytes through the golden emulator and checking that the
model accepts and marks the nanukproto header — and that the bytes at the marked
offset really are `4E 4B`. As the lab note puts it: *"The parser program's
constraints ARE a packet generator."*

This is the moment the inversion pays off conceptually. We didn't teach the symbolic
executor what a nanukproto packet looks like; we taught it to read a parser, and a
parser already contains the definition of the packets it accepts. Constructing a
valid packet for a protocol *from the acceptor alone* is the same trick a grammar
plays when it generates strings instead of recognizing them — and it means the
corpus for a new protocol can be *derived* from its parser rather than authored by
hand.

## Under-approximation, stated honestly

A symbolic executor over a machine with loops has to bound them, and how you disclose
that bound is a matter of intellectual honesty. Nanuk bounds loops with a per-state
visit cap (default 3) and caps total enumeration, and it is explicit that this makes
`symex` an **under-approximation**: *"every emitted path is feasible with an exact
witness, but deep QinQ stacks beyond the cap aren't enumerated. Nothing it says is
wrong; there are things it doesn't say."*

The distinction matters. An *over*-approximation might report a path that isn't
actually reachable — a false alarm. An under-approximation never does: every path it
emits is real, every witness genuinely drives its path. What it sacrifices is
*completeness* — a QinQ stack deeper than the unroll cap simply won't appear in the
enumeration. So the tool's guarantee is one-directional and clean: everything it
tells you is true; it just doesn't tell you everything. The step budget alone admits
256-deep paths, and exhaustive enumeration there is a stated non-goal for v1. A test
pins the termination behavior directly — a state that loops to itself, run with the
unroll cap, leaves only the header-violation exits feasible, so the enumeration
provably terminates.

Being loud about this is the point. A verification tool that quietly under-approximates
while implying completeness is worse than useless; one that says exactly where its
horizon is lets you trust everything inside it. The unroll cap isn't a limitation
we're apologizing for — it's the boundary we're publishing.

## Cross-coverage: two kinds of corpus, one suite

Symbolic execution earns its keep in the corpus. The generator (Chapter 13's
combinatorial vector generator) gives *spec* coverage — every field variation the RFC
cares about, driven through the reference. The symbolic executor gives *program-path*
coverage — every path the parser can actually take, with a witness for each. These are
different axes: the generator knows what the *specification* distinguishes; symex knows
what the *program* distinguishes. Feeding both into one corpus is the cross-coverage —
in the SIIT work, the parser's 26 feasible paths each contributed a witness that joined
the vector suite, so the committed corpus covers both what the spec says matters and
what the program's control flow does.

That's the honest scope of the relationship in v1: symex *produces* a corpus
(`gen_corpus` yields one deduplicated witness per feasible path, no scapy involved),
and those witnesses join the same suite the spec-driven generator populates. What's
*not* yet built is named plainly in the notes — MAP-side symbolic execution (concrete
tables make it tractable, but symbolic table *contents* are a design question), a
read-before-write property, and Alive2/Gauntlet-style translation validation with Isla
for the assembly side. Each is its own satellite row, parked with a trigger rather than
half-built.

## Where this bit us

The honest wrinkle is a packaging one, and it's the kind of drift a fast-moving repo
accrues. The lab notes claim the solver dependency "lives in the dev groups only —
never in wheels, so the playground bundle is untouched," and the *dependency* claim
holds: the shipped wheel requires only protobuf, never Z3. But the symex *module
itself* does ship inside the wheel — it's just inert, because importing it would fail
on the missing solver, and the browser bridge never imports it. So "Z3 never ships"
is true; "symex never ships" is stale. It's harmless — the module sits unused, adding
a couple of kilobytes — but it's the sort of thing that's true when written and
quietly false a few refactors later, which is exactly why this book is drawn from
lab notes with commit hashes rather than from memory. The claim was right the day it
was made; the code moved; and the honest record is to say both.
