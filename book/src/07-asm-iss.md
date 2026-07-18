# Assembler and ISS

**What you'll understand:** how Nanuk turns assembly text into the exact 32-bit
words the hardware fetches, why the encoding — not the Sail prose, not the RTL —
is the real contract between every layer, and why we wrote a *fourth*
implementation of semantics we already had three of. You'll see a two-pass
assembler small enough to read in one sitting, learn why every reserved bit is a
tripwire rather than a "don't care," and meet the decoder fuzz test that treats
random garbage as a conformance obligation.

Part I gave us an ISA defined in Sail and a golden emulator generated from it.
Part II built the RTL and cosimulated it against that emulator. That's two
implementations of the same semantics kept in agreement. This chapter is where
the *toolchain* enters — the assembler that produces machine code, and a Python
instruction-set simulator (the ISS) that executes it. The ISS is the layer the
playground runs, the layer the IR's lowering is checked against, and the layer
that turned out, more than once, to be the cheapest place to catch a bug.

## Two passes, and why labels need both

The assembler lives in three files per engine plus one shared core:
`pp_asm.py` and `map_asm.py` own their mnemonic tables and CLIs; `_asm_core.py`
owns everything ISA-independent. Its docstring draws the line and then adds a
warning worth internalizing: *"Error message formats are pinned by both test
suites — change them deliberately."* In a stack where four implementations must
agree, even the text of an error is part of the contract.

The first pass, `parse_lines`, does exactly three jobs: strip comments at the
first `;`, collect `.equ` constants and labels, and assign a word address to
every instruction. The crucial detail is what a label *resolves to*:

```
symbols[label] = len(program)
```

A label is the **word index** — the count of instructions emitted so far — not a
byte address. This is the whole reason two passes exist. When the assembler
meets `jmp done` it may not have seen `done:` yet; forward branches are the
common case, not the exception. So pass one walks the entire program collecting
every label's word index before pass two encodes a single instruction. By the
time pass two needs to turn `done` into a number, the symbol table is complete.

Pass two is a `match` on the mnemonic that calls into the encoding layer. Symbol
resolution happens here, in `resolve_int`: try `int(token, 0)` first (so decimal
and `0x` hex both work), then fall back to the symbol table, and if neither
hits, raise `unknown symbol`. Registers resolve through a tiny fixed map —
`r0..r3` plus `rz`, the zero register that reads as 0 and discards writes,
encoded as 4 in *both* ISAs. Words are emitted big-endian, four bytes each.

The split between shared and per-engine code is itself a lesson the naming
doctrine records: the parser is never the unmarked default. MAP predefines
`h_frame` as a symbol (the frame's base register, 15) and folds its case so
`h_frame` and `H_FRAME` are the same token; the parser does neither, because it
has no frame base to name. The shared core carries the machinery both need
(`.equ`, labels, operand-count checks, the `-o/--output` CLI); the differences
stay in the files that own them.

## Encodings are the contract

Here is the load-bearing idea of the chapter. Sail owns the *semantics*, but the
thing that actually crosses every boundary — assembler to emulator, emulator to
RTL, ISS to playground — is a 32-bit word with a fixed bit layout. The encoding
files (`pp_encoding.py`, `map_encoding.py`) are explicitly *mirrors* of the Sail
decode tables, and they say so: *"the Sail spec owns the encoding truth;
test_encoding.py pins both to the same golden words."*

Every Nanuk instruction is a fixed-width 32-bit word, big-endian, with the
opcode in the top six bits. Register fields are three bits. That's the entire
frame. The parser ISA fills it with twelve opcodes (`EXT`, `ADVI`, `ADVR`,
`MOVI`, `SHL`, `BEQ`, `BNE`, `JMP`, `SETHDR`, `STMD`, `HALT`, `LDMD`); MAP fills
it with twenty in v0.1 (`LD`, `ST`, `LDMD`, `MOVI`, `ADDI`, branches, `JMP`,
`LOOKUP`, `CSUM`, `SEND`, `DROP`, `STMD`, `ANDI`, `SHLI`, and the five reg-reg
ALU ops `ADD`/`SUB`/`AND`/`OR`/`XOR`). The MAP ALU comment is the benchmark
suite's fingerprint pressed into the source: *"The immediate-only ALU could not
compute on two operands that both came off the wire; the calculator benchmark
demands it."* We'll meet that benchmark properly in Chapter 12; here it's enough
to see that the encoding remembers *why* it grew.

The encoder validates every field before it packs it. A helper `_check` raises
`ValueError` the instant a value overflows its bit width — `boff 4096 does not
fit in 11 bits` — and signed fields (the LD/ST offset, the SEND delta, both
ten-bit two's-complement) raise on out-of-range magnitude. The assembler catches
these and re-wraps them as line-numbered `AsmError`s. This matters because the
alternative — silently truncating an over-wide immediate — would produce a word
that assembles cleanly and does the wrong thing, the single hardest class of bug
to find in a machine-code pipeline. Nanuk chooses to fail loudly at assembly
time.

## Reserved bits are a tripwire, not a don't-care

Between fields, most instruction words have gaps — bits that carry no operand.
The tempting choice is to treat them as "don't care": ignore them on decode. Nanuk
does the opposite. Every reserved bit must be zero, and a decoder that sees a
nonzero reserved bit refuses to decode the word at all.

The decoders live in the ISS files (`pp_iss.py`, `map_iss.py`), because decoding
*is* execution's first step. Each `_decode` case masks the bits that must be
zero and returns `None` — the signal for "illegal instruction" — if any of them
is set, or if a register field exceeds 4. So `MOVI` checks `w & 0x007F0000` (the
reserved band between the register and the 16-bit immediate); `HALT` checks
`0x03FFFFFE`, because only bit 0, the drop flag, is live.

This strictness is what let the ISA *change shape* safely. MAP's `SEND` used to
carry a port-bitmap register; v0.1 dropped it to a bare signed delta. Because
the new `SEND` decoder rejects a nonzero register field, the old
register-carrying word `0x2C82C000` no longer decodes — it fails loudly instead
of executing with a stale operand silently ignored. A reserved-bits-are-zero
discipline turns every future encoding change into a caught error rather than a
compatibility landmine.

One subtlety about what "illegal" means here. A nonzero reserved bit does *not*
raise a Python exception. It produces an **error-verdict halt** with error code
`ERR_ILLEGAL`. The machine stops, records that it stopped because of an illegal
instruction, and reports it as a verdict. This is the totality doctrine (Chapter
3) reaching down into the toolchain: there is no undefined behavior to decode
into, only defined verdicts. The only genuine Python exception the ISS raises is
for a program blob whose length isn't a multiple of four bytes — a malformed
*artifact*, not a malformed *instruction*.

## The ISS: a fourth implementation, on purpose

We had Sail, the generated C emulator, and the RTL. Why write a Python ISS that
executes the same words?

Because duplication with a tripwire is a verification strategy, not an accident.
The single-ISA doctrine names the cost precisely — *"Four semantics
implementations per engine (Sail, interp, ISS, RTL) = eight artifacts... the
duplication a real, deliberate cost."* Each implementation is written by a
different author against a different substrate, so a bug in one is unlikely to be
mirrored in another. When they disagree, one of them is wrong, and the
disagreement is the discovery. The ISS earns its place by being the fastest,
most inspectable of the four: pure Python, no C build, no Verilator, a trace
step emitted per instruction. It's the layer the playground can run in a browser
and the layer against which the IR's lowering is checked instruction-for-instruction.

The execution model is a small state machine with a carefully ordered `step`.
The order is documented because it's observable: *"budget, then pc range, then
decode/execute; the executed instruction is counted at fetch, so an
error-halting instruction has already ticked."* Concretely, each step first
checks whether we've hit the 256-instruction step budget (a budget error halt if
so), then whether the program counter has run past the 1024-word instruction
memory, then fetches the word — incrementing the step count and PC *before*
executing — and finally decodes and executes. Running off the end of the program
fetches a zero word, which decodes to illegal: falling off the edge is an
illegal-instruction halt, not undefined behavior.

The two engines' state models are the two ISAs' disciplines made concrete, and
putting them side by side is the curriculum the doctrine promised. The parser
machine has four 64-bit registers, a monotonic byte cursor, a 256-byte read-only
window, per-header presence and offset arrays, and eight metadata slots; it
terminates with an accept/drop verdict and reports the final cursor as the
payload offset. The MAP machine has a *writable* window — 288 bytes, 32 of
headroom plus the 256-byte buffer, with the packet copied in at offset 32 —
addressed relative to header bases that the parser produced; it terminates with
`SEND(delta)` or `DROP`, and on `SEND` it reconstructs the outgoing frame by
splicing the headroom according to the signed delta and passing through bytes
beyond the window untouched. Read-only cursor machine versus read-write header
machine: the contrast is only legible because there are two ISSs to lay next to
each other.

## Decoder fuzz: random garbage as a conformance test

The encoding is a contract, and a contract is only as good as its coverage of
the inputs nobody writes on purpose. The demo programs exercise the legal
instructions. What exercises the *illegal* ones — the reserved-bit rejections,
the bad register codes, the opcodes that don't exist yet?

Random words. Each engine has a decoder-fuzz test in the golden differential
suite. The parser's generates forty batches of eight random 32-bit words from a
fixed seed, assembles them into a raw blob, runs a random packet through both the
ISS and the C emulator, and asserts the full result tuple matches — verdict,
error, payload offset, step count, header presence, header offsets, metadata.
The comment states the intent: *"Decoder fuzz: random words exercise
illegal/reserved paths against the golden decode's totality."* MAP's fuzz test
does the same and additionally compares the transmitted frame.

Notice what this asserts. Not a round-trip (encode-then-decode gives back what
you put in) — something stronger. It asserts that two *independently written
decoders* agree on precisely which random words are illegal and precisely how the
machine halts when it meets one. Because the semantics are total, "what does this
garbage do?" always has a defined answer, and the fuzz test checks that two
implementations compute the same defined answer for garbage neither author
anticipated. A fixed seed keeps it deterministic and CI-friendly; the totality
doctrine is what makes it possible to write at all. You cannot fuzz-differentiate
against an oracle that shrugs.

Alongside the fuzz, the encoding files carry *golden word* tests: exact hex
values (`encode_ext("r0", 96, 16) == 0x040603C0`, `encode_alu("add","r0","r1","r2")
== 0x40140000`) pinned identically in the Sail decode test and the Python
encoding test. The M1 lab notes call this "the drift tripwire": the same golden
words live in both places, so any drift between the Sail encoding and the Python
mirror fails a test in both languages at once. The encoding isn't documented as
the contract; it's *pinned* as the contract, in two languages, byte for byte.

## Where this bit us

The honest divergences here are between the *design docs* and the *shipped
code*, and they're worth surfacing because they show the ISA growing away from
its first sketch. The MAP design doc named an instruction `CSUMUPD` — an
in-place IPv4 checksum accelerator that computes and writes back. The shipped
instruction is `CSUM rd, hdr, off, rl`: it computes a checksum into a register
over an explicit length, and the demo programs do the write-back themselves with
a separate `ST`. That's a real design change, not a rename — the accelerator
became a primitive, and the composition moved into software. Likewise `SEND` lost
its register operand between the doc and v0.1, and the old encoding is now
provably illegal rather than deprecated-but-tolerated. And the reg-reg ALU that
the design doc listed as "deliberately absent" is now five opcodes, because a
benchmark demanded it.

The lesson threading through all of these: the instruction counts and semantics
in a design doc are a *proposal*, and the encoding files are the *truth*. When
this book quotes "the strict core of eleven instructions," it's quoting a sketch
that the shipped machine has already outgrown — which is exactly what "extract,
don't speculate" predicts. The place to read what the machine really does is the
encoding, because that's the one artifact every other layer is pinned against.
