# The MAP ISA

*In this chapter we design the second processor's instruction set — the match-action
processor, MAP — the machine that actually forwards, rewrites, and tunnels packets. We
will see why it is a byte machine where the parser was a bit machine, how table lookup
becomes a single fused instruction, how a checksum accelerator earns exactly one opcode,
and how a whole class of "hardcoded system semantics" we accidentally baked into the ISA
had to be evicted in a later redesign. This is the design exercise from Chapter 1, done a
second time for a genuinely different job — which, per Chapter 3, is the whole point.*

The parser produces a description of a packet: which headers are present, where they sit,
and a block of metadata. That description is inert. Something has to *act* on it — look up
the destination MAC and decide a port, decrement a TTL and fix the checksum, wrap the
frame in a tunnel header. That something is the match-action processor, MAP, and this
chapter designs its instruction set from the same austere starting point we used for the
parser: the smallest ISA that does the jobs a real demo program demands, grown only when a
program we want to write cannot be written.

## A byte machine, not a bit machine

The first design decision is a deliberate mirror image of the parser, and it is worth
stating as a slogan because it organizes everything: **the parser is the bit machine; MAP
is the byte machine.** The parser's job was sub-byte field extraction, so it read at bit
granularity. MAP's job is editing header fields, and every field it edits — a MAC address,
a TTL, a port number, a checksum — is byte-aligned. So MAP is byte-granular throughout.
There is no bit-offset addressing anywhere in it. That single contrast is one of the
"contrast pairs" that Chapter 3 argued are the curriculum, and it falls straight out of the
different jobs.

MAP shares the parser's skeleton — four 64-bit general registers plus a zero register, a
16-bit word-addressed program counter, a thousand-ish words of instruction memory, a step
budget watchdog, total semantics with defined error halts, all-zeros illegal. What differs
is the memory it addresses and the moves it can make.

## The window, the headroom, and no splice

MAP does not read a fresh packet with a cursor. It works over a *window* it inherits from
the parser: the 256-byte frame as the parser saw it, but now **read-write**, and — this is
the load-bearing part — with **headroom** in front of it. Headroom is a parameter, 32 bytes
by default, prepended to the window. The frame proper starts at window index 32; the header
offsets the parser produced are biased forward by that amount in hardware.

Why headroom? Because of a decision that runs all the way back to Chapter 3's "no deparser."
Length-changing edits — pushing a tunnel header, stripping one — are done as a **signed
head-delta applied at send time, never as a mid-packet splice.** To encapsulate, you write
the outer header *backwards* into the headroom at negative offsets, and then send with a
positive delta that tells the machine the frame now starts earlier. To decapsulate, you send
with a negative delta. There is deliberately *no* "insert N bytes at offset X" instruction.
The reason is first-principles: the payload never enters the processor — only the 256-byte
header window does — so every instruction stays O(1). A mid-packet splice would make
per-packet work proportional to frame length, which is the one thing a line-rate datapath
cannot afford. This is the same discipline as Linux `skb_push`/`skb_pull` and DPDK mbuf
headroom, promoted into ISA semantics. And it has a lovely consequence in hardware, which we
will reach in Chapter 5: applying the head-delta at send is literally one subtractor. The
"deparser," such as it is, is a piece of arithmetic.

## The instructions

Here is MAP's core, in the same "every instruction earns its place" spirit. Memory access is
header-relative:

- **`LD rd, hdr+off, n`** loads `n` bytes (up to 8) from a signed byte offset off a header
  base into a register, zero-extended.
- **`ST rs, hdr+off, n`** stores the low `n` bytes of a register into the window. A negative
  offset reaches back into the headroom — that is how a tunnel push writes its outer header.

Reading metadata, and — after a redesign we will come to — writing it:

- **`LDMD rd, field`** reads a metadata-window slot into a register. It is the exact mirror of
  the parser's metadata store.
- **`STMD field, rs`** writes a register into a metadata slot — the same instruction, same
  encoding, the parser already had.

A little arithmetic, earned one op at a time:

- **`MOVI rd, imm16`** materializes a constant.
- **`ADDI rd, rs, imm`** adds a sign-extended immediate — its first customer is the TTL
  decrement, `ADDI r1, r1, -1`.
- **`ANDI`** and **`SHLI`** are the generic mask and shift by immediate. They exist because
  the canonical IPv4 checksum recompute needs them (more below).

The centerpiece — table lookup, fused with its miss branch:

- **`LOOKUP rd, table, rs, miss`** does an exact-match lookup: the key is a register masked to
  the table's key width. On a hit, the action data lands in `rd` and execution falls through.
  On a miss, `rd` is zeroed and control branches to the `miss` label. There are no condition
  flags — the branch-on-miss is fused into the instruction, the same flagless discipline the
  parser used for compare-and-branch. Lookup is synchronous in version zero: an asynchronous,
  latency-hiding form (the way real silicon overlaps a lookup with other work) is a deferred
  accelerator, noted for the day RTL pipelining makes the latency hurt.

Checksums, as an accelerator:

- **`CSUM rd, hdr, off, rl`** computes an RFC 1071 ones-complement checksum over a range of
  window bytes — the length coming from a register — and drops the folded, complemented result
  into a register. More on why it looks like *this* in a moment.

Control flow and termination:

- **`BEQ` / `BNE` / `JMP`** are the parser's, unchanged.
- **`SEND delta`** terminates the program: verdict "sent," record the head-delta, halt.
- **`DROP`** terminates with no output.

Note there is no `HALT`. `SEND` and `DROP` are the terminators; halting is not a MAP verb.

And note what is *absent*, each with a trigger. There is no register-register `ADD` or `SUB`,
no `OR`/`XOR` — they return the day a program needs to compute across two loaded fields.
Multi-register lookup keys wider than 64 bits return with a five-tuple demo. Checksum *verify*
(as opposed to compute) returns with a demo that needs it. Data-plane table writes — learning —
return with a learning-switch demo. The razor is the same as the parser's, and writing the demo
programs *during* the design is what kept it sharp.

## The checksum: keep the arithmetic, drop the protocol

The checksum instruction has a story, because its first version was too clever. The original
`CSUMUPD` recomputed an IPv4 header checksum at a location and wrote it back in place — a
black-box, one-instruction accelerator. It worked, and it was wrong in a specific way: it had
IPv4 baked into it. It parsed the IHL field to find the header length, it knew which bytes to
skip, it knew to write the result back to the checksum field. That is *protocol* knowledge
living inside the ISA.

The redesign de-protocolized it. The new `CSUM` computes a ones-complement sum over a byte range
and returns it in a register — and that is *all* it does. No IHL parsing, no skipped bytes, no
write-back. The program does the protocol-specific work itself: to recompute an IPv4 checksum, a
program loads the header-length nibble, masks it with `ANDI 0x0F`, shifts it with `SHLI 2` to get
a byte count, calls `CSUM` over that range, zeroes the old checksum field, and stores the result.
That recipe — `ld`, `andi`, `shli`, `csum`, `st` — is why `ANDI` and `SHLI` exist at all. What
stays hardcoded in the accelerator is the *arithmetic family* — ones-complement, which IPv4, TCP,
UDP, and ICMP all share — not any protocol's layout. A CRC-flavored protocol would need new
hardware, and that line is drawn on purpose. We considered an incremental checksum instruction
(the O(1) trick real routers use for TTL updates) and rejected it: the range checksum subsumes it
functionally, and the only thing incremental buys is performance, which is explicitly not a goal.

## The table is the policy

The most important idea in this whole ISA is not an instruction — it is what the tables *mean*.
MAP has a handful of exact-match tables (four by default), each with a key width, an action width,
and entries programmed by the control plane. An empty table always misses. `LOOKUP` on any table id
is a defined operation, even an unconfigured one, which keeps the semantics total.

The slogan is **the table is the policy**. An L2 forwarding program is five instructions: load the
destination MAC, look it up, and send to the port the table returned. Change the table and you change
the switch's behavior without touching a single instruction. This is Nanuk's thesis in miniature — the
program is the mechanism, the table is the policy — and, as we will see, we discovered exactly how load-
bearing it was under duress.

## The redesign: evicting the system semantics

The MAP ISA you have just read is not the one we first shipped. The first version, taken end to end
through hardware and a working demo, had a quiet problem: **system-specific semantics had accreted inside
the ISA itself.** `SEND` carried a port bitmap and masked it with `& 0xF` — four ports, hardcoded into an
instruction. `LDMD` had special fields for the ingress port and for a hardware-computed "flood everywhere
but the ingress" mask. `CSUMUPD` parsed IPv4. None of that belongs in a *general* processor; it belongs in
the switch that wraps the processor. So we did a full-vertical ISA revision to evict it, and the eviction
taught us more than the original design did.

Three of the rework moments are worth telling, because each is a "we tried the clean-looking thing, it was
wrong, here is what was actually right."

**Flooding wanted an ALU op; the right answer was a table.** Computing "every port but the one it arrived on"
is `~(1 << ingress) & 0xF` — which needs a shift-by-a-register that MAP does not have. The tempting fix was to
add that shift. The correct fix was to realize flooding is not arithmetic at all: it is a *lookup*. The switch's
control plane installs a system table mapping each ingress port to its flood bitmap, and the program just does an
ordinary `LOOKUP`. Change the topology, reinstall the table, same program. We found the thesis — the table is the
policy — the hard way, by trying to solve a policy problem with an instruction. It is the single best illustration
in the project of what the ISA is *for*.

**Two metadata spaces collapsed into one.** The first design had a private parser-to-MAP metadata channel *and* a
separate system metadata in-and-out path. When we noticed the parser needed to *read* system metadata too —
port-based parsing wants the ingress id — the two-space design collapsed into a single metadata window: one vector
of eight 16-bit slots, loaded at ingress, read and written in place by *both* processors in their turn, presented at
egress. Untouched slots pass straight through, exactly the way the frame does. The elegant framing that fell out is
that a core running no-op programs is the *identity function* on (frame, metadata) — the dataflow reading is literal.
The two-space version *looked* better-isolated; it was strictly more moving parts.

**We reserved too many metadata slots for the system, and a demo caught it.** The first cut reserved slots one
through three for system use. Then the Ethernet/VLAN/IPv4/UDP parser demo, which records five values, clobbered slot
zero — and the conformance suite caught it immediately as flood lookups keying on the wrong bytes. The fix: slot zero
is the *only* system slot (ingress in, egress bitmap out); slots one through seven belong to the program. One system
slot is the honest minimum. There is a sharp edge left in the open, consciously: a program that forgets to write its
egress decision into slot zero will emerge holding the ingress id there — "forward toward where it came from." We chose
to leave that edge and guard it at the language layer, where the `send(egress=...)` sugar always emits the store,
rather than break the frame-and-metadata symmetry to paper over it.

## Where this bit us

The redesign's real lesson was where the rot had grown. Going in, we assumed evicting "hardcoded system semantics"
would be a hardware cleanup — surely the switch-specific stuff lived in the RTL glue. It did not. Almost all of it lived
in the *ISA*: the bitmap mask in `SEND`, the magic fields in `LDMD`, the protocol in `CSUMUPD`. The parser needed zero
eviction — parsing was already generic — but everything switch-shaped had quietly accreted on the MAP side, because MAP
is where packets meet policy and policy is where the temptation to hardcode lives. The eviction had to be a full-vertical
ISA v0 revision, not a cleanup, and it left MAP a genuinely general machine: per packet it is now nothing but the pure
function (frame, metadata) to (verdict, error, metadata, frame), with the ports, the bitmaps, and the protocol names all
banished to the periphery.

And the flooding episode is the one we keep coming back to. We reached for an instruction to solve a problem that was not
an instruction problem. The moment we saw that "flood everywhere but the ingress" is *table content the switch installs* —
not silicon behavior, not an opcode — the whole architecture clicked into focus. The table is the policy. We will watch that
sentence become literally true in Chapter 6, when reprogramming a table mid-ping changes where packets go. But first we have
to build these two processors in real hardware, and prove they do exactly what Sail says. That is the next chapter.
