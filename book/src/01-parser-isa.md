# The Parser ISA

*In this chapter we design the parser's instruction set from scratch — eleven
instructions, four registers, one cursor — and prove it works by hand-writing the
program that walks Ethernet through VLAN and IPv4 to UDP. By the end you will
understand why a packet parser needs almost no arithmetic, why one instruction
word is deliberately illegal, and what "total semantics" costs at the level of a
single opcode.*

An instruction set is a contract. It says: here is the machine state you can
touch, here are the moves you can make, and here is exactly what each move does —
including the moves that go wrong. Before we can write a Sail specification, build
hardware, or compile a language, we have to decide what that contract *is*. So
this chapter is pure ISA design. No implementation yet, just the argument for
each instruction and the state it acts on.

We are designing the parser processor — **PP** for short — and its first
instruction set, which we version `v0`. The rule for `v0` is austerity: the
smallest ISA that can parse Ethernet, 802.1Q VLAN tags, IPv4 with options, and
UDP. Nothing gets in because it looks tidy. An instruction earns its place only
when a program we actually want to write cannot be written without it.

## The machine we are programming

Start with the state, because the state is where the design decisions live.

A parser reads a packet. We give it a **header buffer** — 256 bytes by default,
read-only to the program. That is not the whole packet; it is a window onto the
front of it, big enough for the headers we care about. The payload never enters
the processor at all, which is a decision we will lean on hard later: it keeps
every instruction O(1), independent of frame length.

Over that buffer sits a **cursor** — a byte index that advances monotonically.
The cursor only ever moves forward. There is no seek-backward instruction, no
rewind. You march through the packet from front to back, and the cursor is your
place in the march. This monotonicity is not an accident; it is the property that
makes a parser's memory access pattern trivial to build in hardware and trivial to
reason about in a symbolic executor.

For working storage we give the program four general-purpose registers, `r0`
through `r3`, 64 bits each. Sixty-four bits is a deliberate width: it holds a MAC
address in one register. There is also a zero register — reads return zero, writes
are discarded — which brings the count the program can name to five. Here is a
small tell about how ISAs grow: the register field in the encoding is *three* bits
wide, enough to name eight registers, but `v0` defines only five. The extra room
is left on purpose. When a future version needs more registers, the encoding is
already waiting.

The program itself lives in **instruction memory** — around a thousand 32-bit
words, word-addressed, with a 16-bit program counter. And the *output* — the
whole point of the exercise — is three things: a table of sixteen header slots,
each with a *present* bit and an *offset*; a 128-bit block of **standard
metadata** we call **SMD**; and, at the end, a verdict and a payload offset.

That output format is the zero-copy idea made concrete. When the parser
recognizes a header, it does not copy the header's bytes anywhere. It records
*where the header started* — a cursor snapshot — into the offset table, and sets
the present bit. Downstream, anyone who wants the IPv4 source address reads it
straight out of the original buffer at the recorded offset. Nothing is
disassembled, so nothing will ever need reassembly.

## Eleven instructions

Here is the entire `v0` instruction set. Every one of these is used by a real
program; none is here for symmetry.

The heart of the ISA is **extraction**:

- **`EXT rd, boff, bsize`** reads `bsize` bits (up to 64) starting at bit offset
  `boff` from the current cursor, zero-extends them, and drops the result into
  `rd`. Note *bit* offset. This is the parser's whole reason to exist, and it is
  bit-granular on purpose. The IPv4 version field is four bits; the IHL field is
  the next four. With bit-granular extraction they come out of the packet already
  isolated and right-aligned — pre-masked, for free. That single fact is why this
  ISA has almost no arithmetic, as we will see in a moment.

Moving the cursor is separate from reading, always:

- **`ADVI imm`** advances the cursor by an immediate number of bytes.
- **`ADVR rs`** advances it by the number of bytes in a register — the move you
  need when a header's length is data, like IPv4's variable options.

Notice that `EXT` does not move the cursor. Reading and advancing are two
distinct acts. You extract the fields you want from a header, *then* you advance
past it. Keeping them separate means an extraction never has a surprising side
effect on your position.

A little register manipulation, and no more than a little:

- **`MOVI rd, imm16`** loads a 16-bit immediate. This is how constants enter the
  machine.
- **`SHL rd, rs, shamt`** shifts left by an immediate amount. It exists for
  exactly one computation: IPv4's IHL field counts 32-bit words, so the header
  length in bytes is `IHL × 4`, which is `IHL << 2`. That is the whole
  justification for having a shifter.

Control flow, fused and flag-free:

- **`BEQ rs, rt, target`** branches if two registers are equal.
- **`BNE rs, rt, target`** branches if they differ.
- **`JMP target`** is the unconditional jump.

The branches compare register against register, never register against immediate.
That is not a stylistic choice; it is an encoding constraint. A compare-immediate
branch would need to fit an opcode, two register-or-immediate fields, and a branch
target into 32 bits, and it does not fit. So a constant you want to compare against
gets materialized into a register with `MOVI` first, and then you branch on two
registers. The constraint shaped the ISA.

And there are no condition flags. Many machines compare, set a hidden Z/N flag,
and branch on the flag later. We rejected that outright in favor of fused
compare-and-branch: the comparison and the decision are one instruction, and there
is no hidden state carried between instructions. A parser with no invisible flags
is a parser whose every path is fully described by its visible registers and
cursor — which, again, is what the verification track downstream will thank us for.

Producing output:

- **`SETHDR hdr_id`** marks a header present: it sets the present bit for that
  slot and snapshots the current cursor into the slot's offset. This is the
  zero-copy record-keeping in one instruction.
- **`STMD field, rs`** writes a register into a standard-metadata field — the
  sideband you hand to the next stage.

And termination:

- **`HALT accept|drop`** stops the machine and delivers the verdict, along with
  the payload offset (the final cursor position), the header table, and the SMD.

That is eleven. Count what is *missing* and you learn the design. There is no
`ADD`. There is no `AND`, `OR`, or `SHR`. There is no less-than branch. The reason
is the one we flagged at `EXT`: bit-granular extraction pulls sub-byte fields out
pre-masked, so the masking an ALU would normally do is already done by the time a
value lands in a register. An ALU is *almost unnecessary*. The instructions that
are absent are not forgotten — each one has a named trigger that will bring it
back. `ADD` returns the day we parse IPv6 extension headers, whose length is
`len × 8 + 8`. The bitwise ops return when packing SMD fields outgrows a handful
of `STMD`s. Less-than branches return with range validation. Until a program we
want to write demands one, it stays out.

## Totality: the all-zeros trap

Here is the constraint that costs the most and matters the most. Every behavior of
this machine is defined. There is no undefined behavior anywhere — not for a read
that runs off the end of the buffer, not for a parse that loops too long, not for
an instruction word that means nothing.

Concretely, every abnormal path is a defined, observable *error halt* that
delivers the same output contract as a normal halt, just with a verdict of error
and an error code recorded in SMD. Read past the end of the buffer? Header
violation, error code 1. Loop forever? The step budget — a hardware watchdog that
counts instructions per packet — trips at its bound, error code 2, and this is
precisely why we can *allow* backward branches for VLAN and QinQ loops without
fear: the watchdog guarantees termination even when the program would not.

And then the sharp one. **The all-zeros instruction word is illegal, not a NOP.**
It would have been easy to make the zero encoding a no-op — many ISAs do. We did
the opposite on purpose. Imagine the program counter runs off into a region of
instruction memory that was never written and is therefore all zeros. If zero were
NOP, the machine would quietly slide through it — a "NOP sled" — until it fell off
the end or did something arbitrary, and you would have no idea where things went
wrong. Because zero is illegal, a runaway PC in zeroed memory halts *immediately*
with a diagnosable illegal-instruction error. The degenerate case became a clear
signal instead of a silent slide.

Why go to all this trouble? Because of the ladder from the introduction. We intend
to check the hardware against an emulator generated from this spec by feeding both
the same inputs and diffing the outputs. You cannot diff against an oracle that
shrugs. If the spec said "reading off the end is undefined," the emulator would be
free to return anything, and agreement between it and the hardware would prove
nothing. Total semantics is what makes the oracle worth consulting.

## Proof by program

The only real test of an ISA sketch is to write the program it was designed for.
Here, in prose, is the parse `v0` was built to handle: Ethernet, then 802.1Q VLAN
(including stacked QinQ tags), then IPv4 with options, then UDP.

It opens on the Ethernet header. `EXT r0, 0, 48` pulls the 48-bit destination MAC
out of the first six bytes. `EXT r0, 96, 16` reads the EtherType at bit offset 96
— twelve bytes in. Then the dispatch: `MOVI` a comparison constant into a
register, `BEQ` against `0x8100` to jump to the VLAN path, `BEQ` against `0x0800`
to jump to IPv4. Advance fourteen bytes past the Ethernet header and go.

The VLAN path advances four bytes past the tag and jumps *back* to the dispatch.
That backward jump is the QinQ loop — a second stacked tag routes right back
through the same dispatch logic — and it is safe only because the step budget is
standing behind it.

The IPv4 path shows off bit-granular extraction. `EXT r1, 0, 4` reads the version
nibble, already isolated, and a `BNE` against 4 drops anything that is not IPv4.
`EXT r1, 4, 4` reads IHL; `EXT r2, 72, 8` reads the protocol byte at bit offset
72. Then `SHL r1, r1, 2` turns IHL into a byte count, and `ADVR r1` advances the
cursor past the whole header, options and all, in one move. Compare the protocol
against 17 and branch to the UDP path.

UDP: `EXT r1, 16, 16` reads the destination port at bit offset 16, `STMD` stores
it to a metadata field, `ADVI 8` steps past the UDP header, and `HALT accept`
delivers the verdict with the payload offset sitting at the start of the UDP
payload. Along the way, `SETHDR` marked each header present as we recognized it,
and the forwarding-relevant fields — destination MAC, VLAN, L4 port — went into
named SMD slots for whatever consumes our output.

Two things fall out of writing this. First, register pressure peaks at three of
the four GPRs — the fourth is genuine headroom, not waste. Second, the whole
program uses only these eleven instructions, which means the invented-protocol
demo from the introduction needs nothing new: a header we design ourselves is
still just fields at bit offsets and a length to advance past, and `ADVR` already
handles arbitrary length fields in byte units. The ISA that parses UDP parses a
protocol that does not exist yet, by construction.

## Where this bit us

The honest friction in `v0` was not any single instruction — it was learning to
trust austerity. The instinct, every time, was to add the general thing: a full
ALU, compare-immediate branches, a seek. Each time, the discipline of "grow only
when a real program can't be written" held, and each time the program *could* be
written without it. Bit-granular `EXT` kept eating the jobs we assumed needed
arithmetic. The version-and-IHL nibbles came out pre-masked; the one genuinely
arithmetic need, `IHL × 4`, was a shift, not an add. We shipped `v0` with no adder
and did not miss it until IPv6 extension headers — which is exactly the trigger we
had written down in advance.

The all-zeros decision, by contrast, was one we made *because* an earlier instinct
was wrong. Making zero a NOP is the comfortable default, and it is a trap: it turns
the worst bug — a program counter loose in uninitialized memory — into the quietest
one. Choosing to make zero *illegal* meant the first time our hardware's PC ever
ran somewhere it should not, the machine told us exactly that, instead of sledding
silently to a wrong answer. We paid one opcode's worth of "wasted" encoding space
for a debugging signal we would come to depend on. Totality is not free, but this
is the kind of thing it buys.

With the contract settled, we can write it down in a language precise enough to
generate an emulator from. That is the next chapter: Sail.
