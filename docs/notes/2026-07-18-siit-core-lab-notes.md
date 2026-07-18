# Lab notes — the SIIT core, and what the vectors taught the spec

**2026-07-18.** Nanuk's first application: a stateless IPv4↔IPv6 translator
(RFC 7915 + RFC 6052 + RFC 7757) on the core. The interesting part isn't that it
runs — it's the three times the test suite turned around and corrected the
things that were supposed to be authoritative: the reference oracle, the
program, and the language.

The shape of the work: an executable reference translator
(`siit_ref.py`, 9bdbb6b), a 94-clause RFC audit, 70 deterministic vectors
generated from the reference (546a7a2), a hand-written PP+MAP program pair
(6d34e79), eDSL twins of both (f8e5bd8), and every vector driven through all
four software levels plus the Amaranth RTL (d4077ff). All green. What follows is
what bit on the way.

## The trailer that shouldn't have been stripped — verification can be wrong

The first landing's reference stripped Ethernet minimum-frame padding: bytes
past the IPv4 Total Length were dropped from the output. It had a reviewer
behind it. It was wrong, and the way we learned it is the note worth keeping.

The program pair (6d34e79) came in one vector short: `edge_min_frame_46` — a
42-byte v4 frame plus 18 bytes of min-frame padding — was **provably
inexpressible** on the core. The emitted frame always ends at `HEADROOM + plen`;
the padded case needs a different SEND delta than the unpadded one, and the
choice depends on the physical frame length, which no Nanuk ISA exposes. Every
read past a frame's end is a *terminal* halt, so no program can even locate the
end to strip past it. The proof was airtight, and it was a proof against the
program.

The controller inverted it: the proof wasn't an indictment of the program, it
was an indictment of the *reference*. Three reasons the strip was wrong, in
ascending force:

1. L2 padding is below the IP abstraction. The real-world precedent this demo
   tracks — Jool — is an L3 kernel module that never sees frame padding either.
   Stripping it invents a behavior with no spec and no reference basis.
2. Real MACs pad on transmit and strip on receive. A conformant L3 translator
   sitting above that boundary should neither expect padding nor manufacture its
   removal.
3. On the zero-copy datapath, stripping is **inexpressible** and passthrough is
   **free**. The property that made the vector "impossible" is exactly the
   property that makes the correct behavior trivial.

So the trailer now passes through verbatim, appended after the translated
datagram, both directions (84a68c9). The program passed on the first regen with
**zero code changes** — it had been right all along; the oracle was teaching it
the wrong answer. The vector flipped from a documented strict-xfail to a plain
pass.

The lesson is the uncomfortable one: the reference translator is the oracle, and
the oracle was wrong. A reviewer had endorsed the wrong version. What caught it
was a *hardware expressibility* argument — the RTL couldn't express the
reference's behavior, and the RTL was right. When the spec and the machine
disagree, the machine is not automatically wrong. Audit ID `7915-framing-trailer`
records all three reasons so the next reader doesn't re-litigate it.

## The IHL overlap — differential probing beats corpus replay

The v4→v6 Ethernet relocation interleaved loads and stores:

```
ld r1,h_frame,0,8; st r1,H_L4,-54,8; ld r1,h_frame,8,4; st r1,H_L4,-46,4
```

New frame start is `h_frame + IHL - 40`. For IHL 11 (44 B header) or 12 (48 B),
that start lands strictly inside `[8,12)` of the *source* frame, so the first
store clobbers bytes the second load still needs — the source MAC comes out
mangled. It's `memmove`'s aliasing hazard, the same one the benchmark suite hit
in source-routing, reproduced in four instructions. The header comment even
claimed "source and destination never overlap" — true for v6→v4 (fixed +20
offset), false for v4→v6, whose offset rides IHL.

**The committed corpus never caught it.** All 70 vectors passed, because the
only options vector exercised IHL=6. 68-for-68 green, and a live bug. What
surfaced it was a throwaway *differential probe* — sweep every IHL 5..15,
reference vs. program, byte-for-byte — not replaying the corpus harder. Corpus
replay confirms what you thought to test; differential probing across a
parameter you *didn't* vary is what finds the hole. Two pinning vectors
(`edge_ipv4_options_ihl11_46`, `_ihl12_46`) with distinct non-repeating MAC
bytes now sit in the corpus, and the fix is loads-before-stores, safe for every
IHL by construction rather than by geometry (eef6675). Both pre-fix failures
landed exactly at byte offset 8 — the start of the clobbered region — which is
how you know the vectors bite the reported hazard and not a look-alike.

## The Movi mini-vertical — the suite made the language grow

Writing the eDSL twins (Task 5) hit a wall the hand asm didn't: the parser
writes a header-present bitmap as a *literal* to `md[1]`, and the parser eDSL
had **no way to materialize a constant**. Its only `Value` sources were
`extract` (packet bits) and `load_md` — every shipping parser twin only ever
stored *extracted* values. The eight bitmap literals mix L3 and L4 bits and
can't be built from any dispatch-known field by shifts alone.

The tell: the ISA already had `MOVI`. `pp_lower` emits it constantly — but only
as an *internal* detail to stage dispatch/compare constants into a reserved
scratch register. It was never surfaced as a value the IR could name. The
capability existed one layer down and had simply never been exposed. So this
wasn't "the machine can't"; it was "the language never asked."

The fix was a full mini-vertical, mirroring the MAP side's `MapConst` exactly:
a `Movi` op added to the parser IR proto, regenerated pb2, threaded through
validate → lower → interp → symex, and surfaced as `s.const(imm)` in the eDSL —
same spelling the MAP eDSL already used, for vocabulary parity (8a41358). Then
the twins closed cleanly (f8e5bd8). The suite didn't just measure the language;
it demanded a piece of it. A benchmark that only ever passes isn't exercising
the language's edges.

(One faithfulness fix the corpus forced along the way: `md[3]` — TTL/hop — must
be written *after* the TTL refuse, so a `ttl_expired` drop leaves `md[3]=0`,
matching the hand asm. The twin had it before. Parity is exact only because the
corpus pinned the write order.)

## The ledger order — settled outer-in, over three rounds

Which single drop reason wins when a packet violates more than one rule at once?
RFC 7915 doesn't say; Nanuk must, so every drop reports one deterministic
reason. It took three review rounds on the reference to settle
(9bdbb6b → 5d5c85e → bf2440c), and the final answer is **outer-in**, identical
both directions: IP structural drops → v4 header checksum → fragment → L4
truncation → v4 zero-UDP-checksum → non-echo ICMP → unsupported L4 → TTL≤1 →
addressing. Fragment is checked *before* L4 truncation and checksum on purpose —
a non-initial fragment's bytes were never an L4 header, so "fragment" must
outrank whatever garbage sits where the L4 header would be. The round that
mattered most (bf2440c) also caught a real `struct.error`: the ICMP checksum
patch reads `body[2:4]`, but the truncation guard only required 2 bytes, so a
2- or 3-byte ICMP L4 *raised* instead of dropping. Totality means the guard has
to cover the read it protects. The order lives authoritatively in
`siit_ref.py`'s docstrings; the audit's `7915-ledger-order` defers to it, and
the `negative` group tests one vector per reason plus two overlap cases.

## Canaries and symex

Worst-case step budgets, of 256: **PP 32** steps (`icmp46_len0_ttl2`),
**MAP 129** (`icmp46_len25_ttl2`). Imem, of 1024 words: **PP 74**, MAP **375**.
All comfortably inside budget; pinned as regression canaries in
`test_siit_program.py`.

PP symex: **26 feasible parser paths**, every one with a witness packet, each
reproduced on *both* pp_interp and the golden emulator (verdict/error/steps).
All 13 parser states reachable. Verdict/error coverage over the corpus is the
full set the parser can produce — header-violation `(2,1)` ×12, halt-drop
`(1,0)` ×6, accept `(0,0)` ×8. MAP symex stays parked (scope).

RTL cosim (d4077ff): all 70 vectors through the Amaranth `NanukCore`, diffed
against the chained ISS oracle, **70/70 first try** — including the two >256 B
tail-passthrough frames that reach past the 256 B prefetch window and the two
non-default-IHL vectors. No RTL drift, no harness bug. Unexciting, which is the
point: the MAP/PP RTL already proven on the demo corpus also holds byte-for-byte
on a real application corpus.

## Two latent finds, parked

**MAP eDSL `default=s.drop` is a dead branch** (`match_action.py:455`). The
dispatch shorthand tests `if default is self.drop:` to emit a bare `Drop`
terminator. But `self.drop` is a *bound method* — Python rebinds it on every
attribute access, so `s.drop is self.drop` is always `False`. The branch never
fires; the shorthand silently falls through and routes to a drop *state*
instead. It's why every MAP example (and the SIIT twin) uses a dedicated drop
state rather than the advertised shorthand. Behaviorally identical (verdict
DROP, one extra jmp that only `steps` would see), so nothing fails — which is
exactly why it survived. Fix candidate: compare by name, or make `drop` a
singleton sentinel rather than a bound method.

**Parser immediate-width is a lower-only gate.** The new `Movi` op's `imm` is
range-checked only in `pp_lower`, not `pp_validate` — following the parser IR's
own doctrine (dispatch/ADVI immediates are checked at lower too), *not* the MAP
IR's "ranges are IR-level" doctrine. Consequence: a malformed IR with an
out-of-range immediate passes validate, passes interp, passes symex, and fails
only at lower — the three-way disagreement on what "valid IR" means. Harmless
for well-formed programs; a trap for anyone generating parser IR by hand.
Parked with the choice of doctrine, not the fix.

---

Legs 1–3 of the four-leg architecture are landed (audit / executable-spec
vectors / in-house differential across all levels + RTL + symex). Leg 4 — the
Jool graybox replay, the one leg that can catch a *shared* misreading of the RFC
since legs 1–3 are authored from one reading — is Plan B, and it gets written
against exactly what landed here.
