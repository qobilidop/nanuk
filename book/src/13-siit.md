# SIIT: A Real Translator

**What you'll understand:** how a whole stack that had only ever run toy
protocols met its first real-world application — a stateless IPv4↔IPv6
translator — and what happened when the RFC, the reference oracle, and the
language each turned out, at least once, to be wrong. This is the chapter where
the methodology we built in Parts I–III stops being scaffolding and starts
earning its keep. By the end you'll have seen a verification catch that
overturned a reviewer-approved fix, a bug that 68 green vectors couldn't find,
and an independent oracle that tried to prove us wrong and came back empty.

Everything before this chapter was infrastructure: an ISA, a golden model, an
RTL core, an assembler, an IR, an eDSL, a symbolic executor, a playground — all
exercised on protocols we invented for the purpose (`l2l3l4`, `nanukproto`,
`map_l2fwd`). Good enough to prove the stack composes; not good enough to prove
it's *worth* composing. For that you need an application a networking person
already respects, with a test suite you didn't write, that either passes or
humiliates you.

## Choosing the demo: build the RFC, not the port

The brief was shaped like Katran — "run the core of a notable open-source
dataplane project, validated against that project's own tests." We surveyed
about twenty-five candidates across two families (L4 load balancers; general
dataplane apps), scoring each against Nanuk's real capability envelope:
exact-match tables only, an ALU with add/sub/and/or/xor and left shifts,
byte-granular header-relative load/store in a 256-byte window, 32 bytes of
headroom with a signed head-delta at SEND, ones-complement checksum, a 256-step
budget — and the standing refusals (no hash instruction, no per-flow state, no
replication).

Katran lost on a single instruction: it needs a hash, and Nanuk's ALU cannot
synthesize one (no right shift, no multiply; Toeplitz-by-iteration busts the
step budget). That refusal is a feature, and rather than overturn it we let it
steer us. The winner was [Jool](https://github.com/NICMx/Jool), the
standards-reference SIIT/NAT64 implementation for Linux. It won on every
criterion, but the decisive one was tests: Jool ships **262 raw `.pkt`
input/expected pairs**, compared byte-for-byte, organized by RFC section — the
only candidate we found with true frame-level golden fixtures that are
implementation-agnostic raw bytes, replayable through any frame-in/frame-out
harness.

The framing decision mattered more than the pick. **We build SIIT, not "a port
of Jool."** The RFC is the spec; a clause-by-clause requirements audit plus an
executable-spec-generated vector suite define "done"; our semantics stay
sovereign where the RFC leaves choices. Jool is an *independent-interpretation
oracle*, not an identity. And the honest name is SIIT — stateless translation.
Stateful NAT64 (RFC 6146) needs per-flow session state, a standing
architectural refusal, so the docs never say "NAT64" for the thing Nanuk does.
The deployment hook is real: stateless SIIT is the CLAT half of 464XLAT — the
translator running in every phone on a v6-only carrier network — and SIIT-DC in
datacenters. This isn't a toy; it's the packet path underneath a chunk of the
mobile internet.

Why SIIT *fit* is worth stating precisely, because it's the first application to
exercise doctrines we'd built speculatively. It's stateless by design — zero
refusals triggered. The v4→v6 header swap is a net +20-byte prepend, which lands
inside the 32-byte headroom: the first real-world workload to justify the
head-delta doctrine beyond `nanukproto`. Address translation is bit operations
(RFC 6052 prefix embed/extract) plus exact-match EAMT entries. Checksums are
CSUM plus the RFC 1624 incremental-patch idiom. About thirty to sixty
packet-path ops, everything in the first eighty bytes. High teaching density:
v4/v6 header duality, pseudo-header checksums, address-family transition, and
"a conformance test is an RFC section" as a structuring principle.

## The four legs, strongest claim last

The test architecture has four legs, and the ordering is deliberate — each leg
can catch a class of error the earlier ones structurally cannot.

1. **The RFC requirements audit** (`benchmarks/siit/audit.md`): RFC 7915 walked
   clause by clause — **95 dispositioned clauses**, each marked
   `tested(group)`, `deferred(trigger)`, `refused(rationale)`, or
   `not-a-requirement`. Every deferral names the trigger that would pull it
   back; every refusal names its rationale. This is the scope ledger and, not
   incidentally, the seed for this chapter.
2. **Executable spec + generated vectors**: a reference translator
   (`sw/python/nanuk/testkit/siit_ref.py`, stdlib-only) is the oracle; a
   combinatorial generator drives every input through it and emits **70
   committed, scapy-free vectors** — byte-exact, no exception masks, because we
   control every source of nondeterminism (the fragment-ID policy is fixed by
   decision).
3. **In-house differential replay**: every vector through the golden emulator,
   the ISS, and the interpreter; all levels agree with each other *and* with the
   reference, byte-for-byte on the output frame. RTL cosim joins over the SIIT
   corpus, and symex enumerates the parser's paths and contributes witness
   packets.
4. **Jool graybox replay**: the only leg that can catch a *shared misreading* of
   the RFC, because legs 1–3 are all authored from one reading. An independent
   implementation, its own fixtures, classified pass / divergence / out-of-scope.

The audit is the genre worth dwelling on. It reads like a code review of a
document. Each clause is quoted or tightly paraphrased and given exactly one
disposition with a stable ID (`7915-4.1-tos`, `7757-hairpin`) that the vectors
cite in their `rfc` field — rename-proof, unlike GitHub heading anchors. The
ledger of drop reasons — `runt`, `fragment`, `zero_udp_checksum`, `icmp_error`,
`unsupported_l4`, `ttl_expired`, `untranslatable_address` — lives
authoritatively in the reference translator's docstrings, and where any other
document disagrees, the reference wins. This is the "single source of truth per
layer" doctrine applied to a *specification*: the RFC leaves the drop *order*
undefined, Nanuk must not, so the reference freezes it — and it took three
review rounds to settle on **outer-in** ordering, with fragment checked *before*
L4 truncation on purpose (a non-initial fragment's bytes were never an L4
header, so "fragment" must outrank whatever garbage sits where the L4 header
would be).

Then the three times the suite corrected the things that were supposed to be
authoritative.

## The trailer that shouldn't have been stripped: verification can be wrong

The first landing's reference stripped Ethernet minimum-frame padding: bytes
past the IPv4 Total Length were dropped from the output. A reviewer had endorsed
it. It was wrong, and *how* we learned it is the note worth keeping.

The hand-written program pair came in one vector short. `edge_min_frame_46` — a
42-byte v4 frame plus 18 bytes of min-frame padding — was **provably
inexpressible** on the core. The emitted frame always ends at `HEADROOM + plen`;
the padded case needs a different SEND delta than the unpadded one, and that
choice depends on the physical frame length, which no Nanuk ISA exposes. Every
read past a frame's end is a *terminal* halt, so no program can even locate the
end to strip past it. The proof was airtight — and it was a proof *against the
program*.

The inversion is the lesson: the proof wasn't an indictment of the program, it
was an indictment of the *reference*. Three reasons, ascending in force. L2
padding is below the IP abstraction, and Jool — the L3 kernel module this demo
tracks — never sees frame padding either; stripping it invents a behavior with
no spec. Real MACs pad on transmit and strip on receive, so a conformant L3
translator should neither expect padding nor manufacture its removal. And on the
zero-copy datapath, stripping is *inexpressible* while passthrough is *free* —
the very property that made the vector impossible is the one that makes the
correct behavior trivial.

So the trailer now passes through verbatim, both directions. The program passed
on the first regeneration with **zero code changes**: it had been right all
along, and the oracle was teaching it the wrong answer. The vector flipped from
a documented strict-xfail to a plain pass. What caught the mistake was a
*hardware expressibility* argument — the RTL couldn't express the reference's
behavior, and the RTL was right. When the spec and the machine disagree, the
machine is not automatically wrong. The audit's `7915-framing-trailer` row
records all three reasons so the next reader doesn't re-litigate it.

## The IHL overlap: differential probing beats corpus replay

The v4→v6 Ethernet relocation interleaved loads and stores:

```
ld r1,h_frame,0,8; st r1,H_L4,-54,8; ld r1,h_frame,8,4; st r1,H_L4,-46,4
```

The new frame start is `h_frame + IHL − 40`. For IHL 11 (a 44-byte header) or 12
(48 bytes), that start lands strictly inside `[8,12)` of the *source* frame, so
the first store clobbers bytes the second load still needs — the source MAC
comes out mangled. It's `memmove`'s aliasing hazard, reproduced in four
instructions. The header comment even claimed "source and destination never
overlap" — true for v6→v4 (a fixed +20 offset), false for v4→v6, whose offset
rides IHL.

**The committed corpus never caught it.** All 70 vectors passed, because the only
options vector exercised IHL=6. Sixty-eight-for-sixty-eight green, and a live
bug. What surfaced it was a throwaway *differential probe* — sweep every IHL from
5 to 15, reference versus program, byte-for-byte — not replaying the corpus
harder. Corpus replay confirms what you thought to test; differential probing
across a parameter you *didn't* vary is what finds the hole. Two pinning vectors
(`edge_ipv4_options_ihl11_46`, `_ihl12_46`) with distinct non-repeating MAC bytes
now sit in the corpus, and the fix is loads-before-stores — safe for every IHL
by construction rather than by geometry. Both pre-fix failures landed exactly at
byte offset 8, the start of the clobbered region, which is how you know the
vectors bite the reported hazard and not a look-alike.

## Movi: the suite made the language grow

The third correction landed on the language itself, and it's the
suite-drives-the-language doctrine in miniature. Writing the eDSL twins hit a
wall the hand asm didn't: the parser writes a header-present bitmap as a
*literal* to `md[1]`, and the parser eDSL had **no way to materialize a
constant**. Its only `Value` sources were `extract` (packet bits) and
`load_md` — every shipping parser twin had only ever stored *extracted* values.
The eight bitmap literals mix L3 and L4 bits and can't be built from any
dispatch-known field by shifts alone.

The tell: the ISA already had `MOVI`. The parser lowering emits it constantly —
but only as an *internal* detail, to stage dispatch and compare constants into a
reserved scratch register. The capability existed one layer down and had simply
never been surfaced as a value the IR could name. So this wasn't "the machine
can't"; it was "the language never asked." The fix was a full mini-vertical,
mirroring the MAP side's `MapConst` exactly: a `Movi` op added to the parser IR
proto, regenerated pb2, threaded through validate → lower → interp → symex, and
surfaced as `s.const(imm)` in the eDSL — the same spelling the MAP eDSL already
used, for vocabulary parity. Then the twins closed cleanly. A benchmark that
only ever passes isn't exercising the language's edges; this one demanded a
piece of the language before it would go green.

## The Jool oracle: 22 / 2 / 100, and no shared misreadings

Legs 1–3 are all authored from one reading of RFC 7915. Leg 4 exists to catch a
misreading none of them would catch on its own: an independent implementation
(Jool), its own pinned graybox fixtures, replayed through our reference and
classified against the audit. Zero GPL bytes in our tree — the Jool clone lives
in a gitignored `third_party/`, and the replay reproduces only fixture *names*
and byte *offsets*, never fixture bytes.

The scorecard: **124 fixtures — 22 pass, 2 divergence, 100 out-of-scope, 0
unclassified.** The headline is the last clause. Every one of the 100
out-of-scope fixtures exercises a capability Nanuk already, explicitly, defers or
refuses — fragmentation, ICMP inner-packet translation, ICMP origination,
extension-header traversal, unknown-transport forward-all — each cited to a
*pre-existing* audit row, none invented after the fact to explain away a
surprise. And the only place both translators actually *send* and disagree
reduces to one policy, not a bug.

That divergence is one we already own. Two sub-MTU v6→v4 fixtures (`7915/abt1`,
`7915/cit1`) differ at exactly IPv4 header byte 6 — `ours[6] ^ expected[6] ==
0x40`, the DF bit, checksum accounted for. Jool clears DF for sub-MTU output per
§5.1's size-conditional rule; Nanuk always sets DF=1, a frozen decision on the
books (`7915-5.1-df`) for RFC 8021 atomic-fragment safety. The oracle didn't
*surface* this — it *confirmed* it, byte-for-byte, against a comparison mask
*derived from the named policy* rather than fitted to the two fixtures. No
fixture exists where Jool matches the RFC and Nanuk doesn't. The independent
suite landed on precisely the one clause the program deliberately departs from,
and nowhere else.

Two smaller things the oracle taught us. B1's first pass flagged 14 same-family
fixtures as "almost certainly hairpinning" without reading what they expected;
B2 read the packets and found 13 were Jool *originating* an ICMP error — already
covered by the "Nanuk never originates" rows — and only one, `7915/gat1`, was a
genuine RFC 7757 hairpin, needing a new audit row `7757-hairpin` dispositioned
`deferred(hairpin/dual-translation)`. One real hairpin, not fourteen; the fix
was reading the fixtures instead of re-guessing. And to even ask Jool's question
fairly, we made the *reference* bigger — Jool's real config is a /40 pool6 and a
/24↔/120 EAMT, neither expressible by the program's /96 + exact-host scope — so
the reference translator grew to implement all six RFC 6052 prefix lengths and
general-prefix EAMT, KAT'd against the RFCs' own worked examples. The **program**
stays exactly /96 + EAMT-exact; only the reference — the oracle's own yardstick —
grew, and regenerating all 70 vectors after the change produced an empty `git
diff`. No reference bugs found. The oracle did the one job it was built for and
came back empty.

## The demo tiers: making a proven-correct core visible

Two more legs proved nothing about correctness — Plan A/B already did that. They
proved SIIT is *reachable*, and what's interesting is how much of the periphery
had to bend to make a proven core visible while nothing touched the datapath.

In the browser, `siit` joined the playground as a program-selector entry
([`?program=siit`](https://qobilidop.github.io/nanuk/play/?program=siit)),
composed exactly like `map_l2fwd`. Three findings on the way. The bridge execs
arbitrary source, so it can't know a program's *name*, only its *shape*: it
fingerprints the compiled MAP by its table signature,
`[(0,32,64),(1,32,64),(2,64,32)]`, and picks the SIIT rig on a match — a
tradeoff pinned by a guard test that compiles every bridge MAP and asserts no
signature collision, so the day a future demo collides, a test fails at that
commit rather than the bridge silently mis-routing years later. The IR renderer
had never had a `bin_op` case, because SIIT is the *first* MAP program to use the
ISA v0.1 reg-reg ALU — so the IR pane, the asm pane, and the two-level trace
would have quietly desynced the moment anyone ran SIIT, a renderer failing by
showing a plausible-but-wrong picture rather than crashing. And all five presets
(`udp46_len25_ttl64`, `udp64_len25_ttl64`, `edge_eamt_dst_46`,
`icmp46_len25_ttl64`, `neg_v4_ttl_expired`) come from the committed vectors on
disk, not from scapy — the GPL boundary the wheel has held since the first
playground landing holds again. The wheel rebuild is the tell that this was a
real language change, not a frontend one: it grew by picking up the reg-reg ALU
and `s.const`, because the browser runs exactly the IR the core does.

In SimBricks, a v4-only QEMU guest and a v6-only guest sit either side of a
Verilator'd `nanuk_switch` running the SIIT program. Three beats, all
*switch-verified* — a number counts only if the switch's own counters back it,
not if a client tool claims it. Ping: 10/10, 0% loss, with `ttl=63` on the reply
proving it really crossed both translations. iperf UDP: the client reports 49
datagrams, the switch counts 169 translated — clearing the ≥0.9× reconciliation
gate, though the surplus lands *above* what the client claims, most likely from
unpaced retries against a responder that never acks; unconfirmed against a
capture, so recorded as a loose end rather than settled. TTL=1 negative gate:
12/12 loss, `dropped=12` at the switch — a silent black hole, because Nanuk never
originates the Time Exceeded, confirmed at the switch rather than inferred from
silence.

## Where this bit us

Three finds we shipped anyway, parked with the honesty they deserve. The MAP
eDSL's `default=s.drop` shorthand is a **dead branch**: it tests `if default is
self.drop`, but `self.drop` is a bound method Python rebinds on every access, so
the identity check is always false and the shorthand silently routes to a drop
*state* instead. Behaviorally identical — verdict DROP, one extra jump only
`steps` would see — which is exactly why it survived; every MAP example uses a
dedicated drop state rather than the advertised shorthand. Second, the new
`Movi` op's immediate is range-checked only at *lower*, not at *validate*,
following the parser IR's own doctrine but not the MAP IR's — so a malformed IR
with an out-of-range immediate passes validate, passes interp, passes symex, and
fails only at lower: three-way disagreement on what "valid IR" means, harmless
for well-formed programs, a trap for anyone hand-writing parser IR. Third, the
i40e NIC model in SimBricks delivered v6→v4 (shrinking) frames as 98 bytes of
all-zeros while the switch's own dump showed correct bytes on the wire — a
SimBricks `i40e_bm` bug, not a Nanuk one, worked around by switching to the E1000
model (same datapath, same core, only the NIC model changed) and reportable
upstream.

The through-line of this chapter is a single uncomfortable idea: **verification
can be wrong.** The reviewer-approved trailer strip was wrong and the machine
taught the spec; the 68 green vectors hid a live bug that only differential
probing found; the language was missing a primitive that only the twins
demanded; and the one thing we might have gotten wrong across the whole RFC, an
independent oracle went looking for and instead confirmed we'd departed on
purpose. The methodology compounds because each leg is built to catch what the
others can't — and the crown of the book is not that SIIT runs, but that we can
say *why* we believe it's right, and show our work every place we were wrong.
