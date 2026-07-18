# RTL and Cosimulation

*In this chapter we build both processors in real hardware — Amaranth, a Python HDL —
and then do the thing the whole project was structured to make possible: prove the
hardware agrees with the Sail spec, automatically, on every field of the output
contract. We will meet the "sequential-everything" lesson that keeps the generated
Verilog sane, the idea of cosimulation as conformance rather than testing, and a
single sign-bit bug that only a fuzzer could find.*

We have two fully specified ISAs and a golden-model emulator generated from each. Now
we make silicon — or the honest simulation of it. The rule from the introduction was
that the hardware is not *tested against expectations*, it is *conformance-checked
against the spec*. This chapter is where that rule becomes machinery.

## Amaranth: hardware as Python

We build the core in Amaranth, a hardware description language embedded in Python. A
processor is a `Component` with typed ports; its `elaborate` method returns a module
built out of combinational assignments (`m.d.comb`) and registered ones (`m.d.sync`),
with finite-state machines written as explicit switches over a state signal. Memories
are real memory primitives with explicit read and write ports. When we are done,
Amaranth emits Verilog — the module names are `nanuk_pp`, `nanuk_map`, and the composed
`nanuk_core` — and that Verilog is what gets verilated into the network simulation in
the next chapter. The whole stack stays in one language, from the eDSL at the top to the
gates at the bottom, which was a deliberate choice made all the way back in the project
design.

There are two levels of simulation in play, and it helps to keep them straight. Amaranth
has its own in-process simulator, and we drive the unit tests and the differential
cosimulation harness with it. Verilator, a separate and much heavier tool, compiles the
emitted Verilog to C++ and drives the full-system network simulation. The unit and
conformance work all happens in the fast in-process simulator; Verilator only enters for
the end-to-end demo.

One subtlety underpins the whole conformance story: the golden model counts *instructions
executed*, not clock cycles. So does the hardware's `steps` counter. That means the
cosimulation contract is *cycle-agnostic* — the hardware is free to take as many clock
cycles as it likes to execute an instruction, and it still has to agree with the emulator
on the instruction count and every architectural result. That freedom is exactly what lets
a sequential microarchitecture be judged against a semantics-only spec, and it is what makes
the next lesson affordable.

## The sequential-everything lesson

The most important hardware lesson of the project has a name — the **EXT lesson** — and it
was learned the hard way on the parser, in the first hardware arc. The parser's `EXT`
instruction extracts an arbitrary run of bits, and the natural first implementation is a wide
combinational datapath: one big expression that shifts and masks the whole window down to the
field in a single cycle. It works in simulation. It also makes Verilator emit *pathological*
output — the generated C++ blows up — because a very wide combinational path is exactly what a
cycle-accurate C++ backend struggles to compile efficiently.

So when we built the match-action processor, we designed it sequential *from birth*. The rule,
written into the module itself: every window or table access is a sequential loop over a memory
port, never a wide combinational datapath. A load streams its bytes one per cycle. A store streams
its bytes out. A `LOOKUP` scans the table one entry per cycle. A `CSUM` streams the header through a
small accumulator. Each memory-touching instruction is a little multi-cycle state machine, not one
fat expression. The parser has the same shape at heart: a two-state fetch/execute loop that respects
the synchronous memory read port.

The payoff is quantified and satisfying. The match-action core came out around 4,900 lines of Verilog,
the parser around 5,200 — the same ballpark — and both compiled without drama. The wide-combinational
version was the one that fought the tools. This is the sequential-everything lesson: at this scale, cheap
clock cycles are worth spending freely to keep the datapath narrow, because narrow datapaths are what
survive synthesis and verilation. And because the cosim contract is cycle-agnostic, spending those cycles
costs nothing in conformance — the emulator does not care how many cycles a load took.

## Cosimulation as conformance

Here is the heart of the chapter. There are three descriptions of each processor's behavior, and they all
have to agree: the Sail model, the golden-model emulator generated from it, and the Amaranth RTL. Cosimulation
is the machinery that checks the RTL against the other two, automatically, on the *entire* output contract.

Concretely, the RTL is run in the in-process simulator and, for each program-and-packet pair, we diff every
field the machine produces against the emulator: the verdict, the error code, the metadata window, the head-delta,
the instruction count, *and the transmitted frame bytes themselves*. Not a summary, not a spot-check — the whole
outbound contract, byte for byte. The coverage is a matrix: the demo programs (L2 forward, TTL rewrite, tunnel push)
crossed with the demo packet corpus crossed with both ingress ports; plus a tunnel round-trip, where the push program
must produce a frame the pop program exactly inverts; plus the *composed* pipeline, parser-RTL feeding match-action-RTL,
diffed against a pure-Python model of the whole thing.

The distinction between *testing* and *conformance* is the point. The RTL is not checked against hand-written expected
outputs that a human decided were correct. It is checked against an independently authored formal specification, through a
shared set of golden vectors. When a cosim assertion fails, it is not saying "this differs from what the test author
expected"; it is saying "this differs from what the architecture *is*." That is a categorically stronger claim, and it is
the reason writing the Sail model first keeps paying for itself. Fidelity goes all the way down: the hardware mirrors the
Sail step order — step budget checked first, then program-counter range, then decode, then execute — and every error code
maps one to one, because a conformance check that ignored those would not be a conformance check.

## The bug only a fuzzer could find

Corpus-driven cosimulation is deterministic and thorough, but it only exercises the packets you thought to include. Alongside
it we run *differential fuzzing*: random programs crossed with random packets and tables, asserting the golden model and the RTL
agree on the full contract. And here totality pays off exactly as promised in Chapter 2 — because every 32-bit word sequence is a
valid program (worst case, a defined error halt) and the step budget bounds every run, there is no validity precondition to
generate around. You can generate pure noise and it is all meaningful. That is a rare luxury in fuzzing, and it is a gift from the
total-semantics discipline.

The fuzzer immediately earned its place. The match-action fuzz leg failed seven of twenty-five cases on its first run: every `SEND`
on a packet of 256 bytes or more error-halted in the RTL but sent cleanly in the golden model. The root cause is a beautiful little
hardware trap. The send-range check compares the head-delta against the negated packet length. The packet-length signal is nine bits
wide — it has to hold values up to 256 — and the code negated it with Amaranth's `.as_signed()`. But `.as_signed()` on a value that
uses its *full* width is a *reinterpretation*, not a conversion: a nine-bit value of 256 has its top bit set, so `.as_signed()` reads
that top bit as a sign and yields −256. The comparison went haywire, but *only* for packets of 256-plus bytes — and the demo corpus
never contained one. The corpus-driven cosim was green while the bug sat there in plain sight.

The lesson, written into the code as a scar next to the fix: **Amaranth `.as_signed()` on a value that uses its full width is a
reinterpretation, not a conversion — negate in a wider signed signal instead.** The correction computes the negation into a wider
signed signal, so the arithmetic is real arithmetic and not a bit-pattern reinterpretation. The fuzzer found a second, smaller thing
too — a tail-passthrough rule the RTL test driver was missing — which was a harness bug rather than a core bug, and it is worth telling
as a pair: the fuzzer found one real hardware bug and one test-harness bug in the same run.

There is an honest coda about fuzzing's limits. The fuzzer's program generator does not emit the register-register ALU opcodes that a
later benchmark added, so drift in those instructions would sail right past it. We know that, so those opcodes get a *dedicated*
cosim test with hand-picked edge cases — subtracting past zero to watch the borrow wrap, adding to overflow out of the top bit. Fuzz
coverage has holes, and the discipline is to know where they are and plug them deliberately.

## How the core is put together

The two processors live as separate modules — the parser and the match-action processor — and a third module composes them behind a
single streaming interface. The composition is the zero-copy idea made structural. The core owns *one* frame-window memory, and it is
*shared*: the parser reads it through one set of ports, the match-action processor takes read and write ports on the very same memory —
and, because Amaranth memories want their ports declared when the memory is built, those ports are all handed out at construction time,
before anything elaborates. Nothing is ever copied between the two processors. The frame is loaded once at ingress, edited in place, and
drained at egress, and the head-delta from a tunnel push or pop is applied by *one subtractor* in the drain address path. The
deparser/editor doctrine from Chapter 3 turns out to be, in hardware, a single arithmetic unit.

Around that shared window sits a metadata register file — the eight-slot window both processors edit in their turn — and a strict,
turn-based phase machine: fill the window, run the parser, hand off, run the match-action processor, drain the result. The handoff is
where the parser's output structure wires into the match-action processor's address inputs; it is a one-cycle latch of the header map,
and the metadata needs no handoff at all because both processors address the same register file. The external face is a streaming
interface with the recognizable ready/valid/last handshake, one metadata block sampled at the start of packet, and a single result
strobe per packet carrying the verdict, the error, and the final metadata — for *every* verdict, including drop and error, so a consumer
always gets a well-defined answer. A separate write-only control port loads the two programs and the table contents between packets.
Bytes of the frame beyond the 256-byte window pass through a separate tail buffer untouched; the processors only ever work on the head.

## Where this bit us

The `.as_signed()` bug is the one we tell, because it is the whole verification philosophy in a single sign bit. The hardware was
"correct" against every packet a human had thought to write down. It was wrong for a class of inputs — large packets — that the corpus
simply did not contain, and no amount of staring at the corpus would have revealed it. Totality is what let the fuzzer explore that class
for free, because there was no notion of an "invalid" program to filter out; and cosimulation against an independent spec is what made the
fuzzer's verdict trustworthy, because the emulator was authored separately and had no reason to share the hardware's mistake. The bug
lived in the gap between "passes the tests I wrote" and "conforms to the specification," and closing that gap is the entire reason the
project is shaped the way it is.

The quieter lesson is the sequential-everything one, because it is the kind of thing you only believe after the tools punish you. The
instinct in hardware is to do the work in one cycle — it *feels* faster, it *feels* like good hardware. At this scale it is the wrong
instinct: the wide combinational path is what makes the toolchain choke, and the narrow sequential loop is what compiles and synthesizes
cleanly. We paid for that lesson once, on the parser's `EXT`, and applied it everywhere after.

We now have two processors in real hardware, composed into a core, provably faithful to the spec. The last thing to do in Part II is put
that core to work — wrap it in a switch, push real traffic through it, and watch a table become a forwarding policy. That is the next
chapter.
