# The eDSL

**What you'll understand:** why Nanuk's embedded language optimizes for education
rather than P4 surface compatibility — and why that goal was reached by
*retracting* a P4-subset decision the same day it was made. You'll see the two
program forms (parser states and match-action programs), the type-level rule that
makes the parser/MAP split a *compile error* rather than a convention, the
twins-of-hand-asm methodology that keeps the language honest against the metal,
and the register-allocation difference that reveals which machine each engine
really is.

The eDSL is the top of the stack — the layer a learner reads and writes. It's a
Python-embedded language: you decorate functions, call methods, and out the other
end comes IR that lowers to the assembly of the previous chapters. Because it's
what people *read*, its design choices are pedagogical choices, and the most
important one is a thing it decided *not* to be.

## Education-first: the P4 retraction

Early in the design there was a tentative goal — make `nanuk.lang` a P4 subset, so
P4 programmers would find it familiar. It was retracted the same day, and the
retraction is documented rather than quietly forgotten: *"nanuk.lang optimizes for
education, not P4 surface compatibility."* The reasoning is that chasing P4
resemblance drags the language away from the machine it actually compiles to. Nanuk
is a zero-copy, offsets-and-metadata machine; P4's surface is shaped around a
different abstraction (the PHV, detached headers, a deparser). A language that
mimics P4's syntax while lowering to Nanuk's machine would teach the wrong mental
model at every turn.

The P4 relationship survives, but relocated: a concept-mapping section in the prose
(here's how a P4 idea corresponds to a Nanuk one), and a *parked* P4-frontend
satellite that would translate real P4 to the IR without contorting the eDSL. The
sharpest consequence is that the eDSL gets *no deparser construct at all* —
*"education-first: no construct whose only job is P4 resemblance."* The language
stays first-principles-shaped around the zero-copy machine, and a P4 program that
wants to run on Nanuk goes through a translator, not through a costume the eDSL
wears. Retracting a decision the same day you made it, and writing down *why*, is
the working style this whole book is built from: the retraction is data.

## Two program forms

The eDSL has two sibling shapes, built the same way — decorate, build IR, compile.

A **parser program** is a `Parser` whose states are functions marked with
`@p.state()` (one flagged `start=True`). Inside a state you call the cursor-machine
vocabulary: `mark` a header, `extract` bits into a value, write `smd` metadata,
`load_md`, materialize a `const`, `advance` the cursor, `dispatch` on a value, or
`goto` another state — terminating with `accept` or `drop`. The value handles that
`extract` returns support only `<<` and multiplication by powers of two, because
*"v0 has SHL, no MUL"* — the language refuses to let you write an operation the
machine can't perform.

A **match-action program** is a `MatchActionProgram` with states, `table`
declarations, and `header` bindings. Its vocabulary is the byte machine's: `load`
and `store` header bytes, `load_md`/`store_md`, `and_imm`, `bin_op`, `shift`,
`const`, `add`, `lookup`, `csum` — terminating with `send`, `drop`, `goto`, or
`dispatch`. The two forms are deliberate siblings, `MatchActionProgram` beside
`Parser`, and their symmetry is the point: the same construction pattern, two
different machines, laid out so you can see the contrast. That contrast — cursor
versus header-relative, bit versus byte, read-only versus read-write — is the
curriculum the two-processor doctrine (Chapter 3) promised, and the eDSL is where a
learner first meets it as *two kinds of program you write differently*.

## The byte-machine rule is a type error

The cleanest expression of the parser/MAP split lives in the eDSL as a rule you
cannot violate. The MAP is the byte machine, so a match-action program that tries
to touch a sub-byte field doesn't produce wrong output — it *fails to compile*.
When you bind a header field in a MAP program, the binding checks alignment:

```python
if field.bit_offset % 8 or field.width % 8:
    raise CompileError(
        f"{field.qualname} is not byte-aligned ...; the MAP "
        "is byte-granular — sub-byte fields are the parser's job")
```

The worked example is exact: `ipv4.version` (a 4-bit field) is a `CompileError`,
while `ipv4.ttl` (a byte-aligned 8-bit field) compiles. As the lab notes put it,
*"the engines' split is now a type error."* This is a small piece of code doing a
large amount of pedagogical work. The reason sub-byte fields belong to the parser
and byte-aligned edits belong to the MAP isn't stated in a comment you might skip —
it's enforced at the moment you write `mp.header(ipv4)`, so the machine's structure
is something you *bump into* rather than something you're told. A learner who tries
to decrement a 4-bit field in a MAP program learns, from the error, which engine
owns bit-granular work and why.

## Twins of hand asm: parity as methodology

Every example ships twice: a hand-written `.asm` teaching copy and an eDSL *twin*,
with tests asserting the twin's compiled output is behaviorally identical to the
hand asm through the golden emulator. This is the language's honesty check. The
hand asm is what a human writes when they understand the machine; the twin is what
the eDSL produces; if they diverge in behavior, either the language generates wrong
code or the human misunderstood the machine — and either way you want to know.

The parity tests are layered, and the layering is careful about *what* it compares.
The eDSL-versus-hand-asm test compares verdict, error, offsets, and metadata but
*deliberately not step count* — because the two programs schedule instructions
differently. The hand asm juggles registers cleverly; the eDSL's lowering allocates
mechanically, so *"the eDSL ttl program loads TTL twice where the hand asm juggles
registers."* Behavior must match; instruction counts need not. But a *second* leg —
interpreter against the twin's own lowering — *does* require step equality, because
(as Chapter 8 showed) the interpreter mirrors the cost model of the very lowering it
runs against. The SIIT parity test carries the fullest version of this triangle:
the twin pipeline behaviorally matches the hand pipeline, the interpreters agree
with the emulator on the twins' lowering *including* steps, and the simulator agrees
with the emulator on the same words. Different legs assert different equalities, and
knowing which is which is the whole art: comparing steps where schedules legitimately
differ would be a false alarm; *not* comparing them where the cost model binds them
would miss a real bug.

## Two allocators, two machines

The register allocator is where the eDSL quietly reveals which machine each engine
is. Both lowerings have the same four registers and reserve one (`r3`) as scratch
for compare constants. What differs is when a register is *freed*.

The parser lowering **never frees**: *"a value stays live from its defining op to
the end of its state — no freeing — registers do not survive into the next
state. Needing a fourth concurrent value is a LowerError."* This is fine because
parsing is naturally register-light: extract a field, branch, advance, move to the
next state. The MAP lowering, by contrast, does **last-use liveness** — it frees a
register the moment its value's last use is behind it — because *"straight-line MAP
programs materialize many short-lived constants — tunnel push writes an 11-constant
header — so no-free allocation would starve."* The ordering is deliberate: an op
captures its source registers, *then* frees the dead ones, *then* allocates its
result, so a dying source's register is immediately reusable by the same
instruction (`rd == rs` is well-defined because the key is read before the result is
written).

The lab note frames the divergence as a teaching choice, not an oversight: *"The
parser lowering stays as-is — its programs never needed liveness, and its simplicity
is a teaching feature."* Two allocators, two complexities, matched to two workloads:
the parser is register-light and gets the simple allocator; the MAP is
constant-hungry and earns the sophisticated one. A learner reading both lowerings
sees, in the *shape of the allocator itself*, that a parser and a match-action
engine stress a register file differently — which is a more durable lesson than any
sentence claiming it.

## Where this bit us

Two parked bugs in the eDSL are worth surfacing, because both survived precisely by
being *behaviorally* invisible — the failure mode this chapter's whole methodology
is built to catch, catching itself in the two places the twins didn't reach. The MAP
`default=s.drop` dispatch shorthand is a dead branch: it tests `if default is
self.drop`, but `self.drop` is a bound method Python rebinds on every access, so the
identity check is *always* false and the shorthand silently routes to a drop *state*
instead of emitting a bare drop terminator. Behaviorally identical — verdict DROP,
one extra jump only a step count would notice — which is exactly why every MAP
example uses a dedicated drop state rather than the advertised shorthand, and why
nothing failed. And the parser `Movi` immediate is range-checked only at lower, not
at validate (Chapter 8's three-way disagreement), a trap that bites only someone
hand-writing IR.

Both bugs are the same lesson wearing two hats: a difference no verdict can see is a
difference your tests won't catch unless you look at the mechanism. The twins
methodology exists to make the language provably match the metal, and these two
finds are the boundary of it — the places where "behaviorally identical" and
"actually correct" quietly came apart, and only reading the generated code, not the
result, told them apart.
