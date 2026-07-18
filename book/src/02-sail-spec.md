# The Sail Specification

*In this chapter we take the parser ISA from the last chapter and write it down in
a language precise enough that a computer can turn it into a running emulator — no
hand-written interpreter, no second source of truth. We use Sail, the same tool the
RISC-V and Arm architecture teams use, and we come out the far side with a golden
model: the oracle every later layer will be diffed against. We also collect the
three Sail gotchas that each cost us a debugging session.*

An ISA sketch on paper is a promise. "Illegal instructions halt with an error";
"`EXT` zero-extends"; "the cursor never moves backward." Prose can hold those
promises, but prose cannot *run*, and we said in the introduction that the whole
project hangs on being able to diff the hardware against an oracle. So the promise
has to become executable. The question is how, without hand-writing an emulator —
because a hand-written emulator is just a second implementation, with its own bugs,
and now we have two things to trust instead of one.

The answer is to write the semantics *once*, formally, in a language built for
exactly this, and have the emulator generated from it. That language is Sail.

## Why Sail

Sail is a domain-specific language for describing instruction-set architectures.
You write, in Sail, what the machine state is and what each instruction does to it,
and Sail's C backend emits a C emulator that executes those semantics. The same
tool underpins the official Arm and RISC-V specifications, which is exactly the
company we want to keep: an ISA described in Sail is an ISA described in the idiom
the computer-architecture and formal-methods worlds already speak.

The single most important consequence is our governing rule: **the Sail model is
the single source of truth for semantics and encodings.** Not one source among
several — *the* source. The assembler will encode instructions the way Sail decodes
them. The hardware will implement what Sail specifies. When the hardware and the
generated emulator disagree, the emulator is right by definition, and the hardware
has a bug. That is what "golden model" means, and it is only meaningful because
there is exactly one place the truth lives.

We did not invent this workflow from nothing. A sibling repository —
a Sail model of the xISA parser — served as a *pattern donor*: we borrowed its Sail
idioms, its CMake-and-ctest build shape, its devcontainer, and its
differential-testing rig. But Nanuk's spec is written fresh and Apache-licensed;
the donor gave us idioms, not code.

## Driving generated code from a small C main

Here is the practical problem. Sail's C backend gives you a pile of generated C
implementing the semantics, but it does not give you a friendly program you can pipe
a packet into. You have to drive it. And you do not want your driver reaching into
Sail's runtime internals — that couples your harness to Sail's implementation
details and breaks every time Sail changes.

The design that avoids this is a **thin C-facing API**: a small set of functions,
all taking and returning machine-word-sized values, that a plain C `main` can call
without knowing anything about Sail's runtime. We wrote them in Sail itself, in an
`api.sail` file, and they are exactly the verbs an emulator driver needs:

- `emu_reset` initializes all machine state.
- `emu_poke_imem` loads one 32-bit program word at an address.
- `emu_poke_pkt` loads one packet byte.
- `emu_set_plen` sets the packet length.
- `emu_run` runs until the machine halts.
- `emu_get_verdict`, `emu_get_error`, `emu_get_cursor`, `emu_get_steps`,
  `emu_get_hdr_present`, `emu_get_hdr_offset`, `emu_get_smd` read out every field of
  the output contract.

Every one of those takes and returns a `bits(N)` type that maps cleanly to a C
`uint64_t`. The C `main` reads a binary program file and a binary packet file, pokes
them in word by word and byte by byte, calls `emu_run`, and prints a single line of
JSON to standard out: the verdict, the error code, the payload offset, the step
count, and the header-present, header-offset, and SMD arrays. That one JSON line
*is* the output contract, made concrete. The smoke test is charmingly small: a
two-instruction program that moves `7` into `r0` and halts-accept, encoded as two
words, run against a zero-length packet, expecting `"verdict": 0`. If that prints,
the whole toolchain from Sail source to running emulator is alive.

One naming detail worth carrying, because it shows up in every stack trace: Sail
prefixes the generated C names with `z`. The Sail function `emu_run` becomes
`zemu_run` in the emitted C, `emu_poke_imem` becomes `zemu_poke_imem`. When you are
staring at a linker error about an undefined `zemu_get_smd`, that `z` is Sail's
fingerprint, not a typo.

## The Python mirror, and the tripwire that guards it

There is a subtlety in "Sail owns the encodings." The assembler is written in
Python — it has to be, because the whole language stack above it is Python — and an
assembler has to know how instructions are encoded. So the Python side necessarily
holds a *copy* of the encoding rules. Two copies of an encoding is exactly the kind
of thing that drifts: someone tweaks a field width in Sail, forgets the Python
mirror, and now the assembler emits bytes the emulator misreads, silently.

We refuse to let that drift go unnoticed. The Python encoder is explicitly a
*mirror*, and it is guarded by a differential tripwire: a test assembles our
two-instruction smoke program through the Python assembler and byte-compares the
result against the hand-computed words that Sail's decoder expects. If Sail and
Python ever disagree about how a single instruction is laid out, that test goes red.
The rule we wrote for ourselves was blunt: never let them drift silently.

## Totality, in Sail this time

The last chapter argued for total semantics — no undefined behavior anywhere — from
the ISA-design side. Sail is where that argument gets *enforced*, because Sail's
type system will not let you write a partial function without noticing. Every
abnormal path becomes a defined error halt with a verdict of error and a numbered
code: 0 for none, 1 for a header violation, 2 for the step budget running out, 3 for
an illegal instruction, 4 for the program counter leaving its valid range, 5 for an
out-of-range SMD access. The all-zeros word from the last chapter is illegal here,
not a NOP, and so are unlisted encodings and reserved bits set to nonzero.

This is worth more than tidiness, and it pays off spectacularly later. Because every
32-bit word sequence is a *valid program* — the worst it can do is halt with a
defined error — and because the step budget bounds every run, you can throw
completely random bytes at the emulator as a program and it will always terminate
with a well-defined answer. That means differential fuzzing needs no validity filter
at all: no "is this a legal program" precondition to generate around. When we get to
the hardware, we will diff ninety random program-and-packet pairs against this
emulator and they will all agree, and the reason we *can* is that totality made every
random input meaningful.

## Three ways Sail bit us

Writing a formal spec is not a smooth ride, and the book promised honesty. Three Sail
behaviors each cost us a debugging session, and all three are the kind of thing you
only learn by hitting them.

**Flow typing cannot see through `let` globals.** Sail uses *flow typing*: after you
assert or check a bound, the type-checker refines what it knows about a value along
that path, so a subsequent bitvector or array operation type-checks. This is lovely
until the bound you assert against is a named top-level constant — a `let` global,
like a parameter named for the buffer size. The checker treats the global as an
opaque term, not as the literal it was defined from, and the refinement does not go
through. The fix is deflating: asserts need *literal* bounds. You want to write your
bounds checks in terms of the nicely-named parameter; the type-checker makes you
write the raw number. It is a small, permanent friction between clean parameterization
and a spec that type-checks.

**Hex literals are bitvectors, not integers.** In Sail, `0x42` is a bitvector
literal, not an integer. So an expression that mixes an integer-valued term with a hex
literal is a *type error*, not a silently-coerced comparison. The concrete shape that
bit us was `unsigned(pc) == 0x42`: `unsigned(pc)` produces an integer, `0x42` is a
`bits(n)`, and the `==` simply will not check. This one stings precisely because an
ISA spec is saturated with hex — opcodes, encodings, jump targets — and reaching for a
hex literal to compare against a program-counter value is the natural thing to do, and
the natural thing does not compile.

**Dead-code elimination eats your API, and `--c-no-main` still wants a `main`.** This
one is two coupled traps, both flowing straight from the "thin C-facing API" design.
First: the API functions — `emu_reset`, `emu_poke_imem`, `emu_run`, and the rest —
are never called from *inside* the Sail model. They exist only to be called from
external C. So Sail's C backend, doing its job, dead-code-eliminates them, and your C
`main` cannot link against functions that no longer exist. You have to explicitly mark
them preserved with `--c-preserve`, or they vanish. Second: even when you build with
`--c-no-main` to supply your own C `main`, the backend *still* generates a `model_main`
that references a `zmain` symbol — so a model that legitimately has no `main` fails to
link against a dangling `zmain`, and you have to provide a stub. The very functions
that make the model drivable are the ones the compiler throws away, and the very flag
meant to let you write your own entry point still emits a reference to the one you
deleted. Both are version-sensitive enough that we pinned the exact Sail version and
the exact flag in the commit that introduced them.

## Where this bit us

The deepest lesson from the Sail stage was not any single gotcha — it was learning
what "matches the spec" has to mean. When we later rebuilt the hardware's extract logic
(a story for Chapter 5), the redesign was judged not against a table of expected outputs
but against *the Sail algorithm itself*, laid out in time. The hardware that survived
was the one that read like the Sail `read_pkt_bits` routine, step by step. The lesson we
wrote down was that a design which "matches the spec's *algorithm*, not just its results"
tends to be the buildable one — because when it diverges, you can point at the exact
line, in both places, that disagrees.

And the golden model quietly earned its keep the first time we restructured hardware
underneath it. Twenty-nine unit tests and a full cosimulation run re-verified a
from-scratch rewrite of the extract path with *zero* test edits. That is the real
dividend of writing the spec first: the oracle does not care how you implement the
machine, only that you implement *this* machine, so restructuring the implementation
costs nothing in test churn. The golden model is what makes hardware cheap to change.

We now have an executable, total, single-source-of-truth specification of the parser
processor. Before we build hardware for it, though, there is a question the design has
been quietly forcing on us — why is there a *second* processor at all, and why exactly
one more and not two? That is the next chapter.
