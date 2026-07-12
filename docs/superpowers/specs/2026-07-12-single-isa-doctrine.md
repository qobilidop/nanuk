# Single-ISA doctrine (why not one ISA for PP + MAP)

**Date:** 2026-07-12
**Status:** decided (discussion with Bili; no code changes — this records
design doctrine and the arguments, as the deparser doc did for the
opposite extreme)

## Context

The [deparser & editor doctrine](2026-07-12-deparser-editor-doctrine.md)
settled the "three engines?" extreme (no). This doc settles the mirror
question: what if the Parser ISA and MAP ISA were **one unified ISA**?
The [MAT extension design](2026-07-11-map-extension-design.md) already
has a "Why sibling ISAs, not one ISA" section; this discussion
stress-tested that call from the opposite direction and produced
sharper arguments than the original four. Verdict unchanged: **two
sibling ISAs; worse to merge.**

## The naive objection is weak — name the real one

The union is only **19 instructions** (11 + 12 minus shared
MOVI/BEQ/BNE/JMP), still RISC-small, and the encodings coexist in one
6-bit opcode plane (EXT 26 bits, LOOKUP exactly 32). Opcode count and
encoding are NOT the argument against. The real arguments:

1. **The ISAs are machine-state disciplines, and the disciplines
   conflict.** PP: monotonic cursor, bit-granular, 256B read-only
   window, *produces* offsets+SMD, HALT verdict. MAP: header-base +
   signed byte offset, byte-granular, headroom+256B read-write window,
   *consumes* offsets+SMD, SEND(bitmap, delta)/DROP. A union machine
   carries the cross product of both state models, not the sum of two
   opcode lists.
2. **Header bases don't exist during parsing — the parser's job is to
   create them.** MAP addressing (`LD rd, hdr+off`) is only
   well-defined after hdr_offsets are populated; the PP→MAP contract
   is not an interface that could be inlined away, it is the data
   structure that makes the second addressing model meaningful. The
   ParseResult is the MAP's memory map.
3. **The mode-bit decay.** To keep a union total in Sail you'd gate
   the conflicting semantics behind a phase bit (parse phase: window
   RO, cursor live, tables trap; edit phase: writes allowed, cursor
   frozen). That reinvents the two-engine boundary as runtime control
   state every instruction's semantics must consult — totality must be
   defined over instructions × phases, so the union model is *bigger*
   than the two models combined. The union doesn't eliminate the
   boundary; it smears it.
4. **The diamond problem.** The split is not an ISA artifact — it
   recurs at every level already built: lang (parse states vs
   tables/actions), IR (ParserProgram vs MapProgram, validators reject
   each other's terminators), demos (two genres), playground (two
   debugger phases), symex (two tractable analyses, composition =
   sequential). Unifying only the ISA makes the stack two-shaped above
   and one-shaped below, discarding shape information at lowering that
   every tool then has to recover. Same shape at every level is what
   makes the four-rung ladder and level-diffing work.
5. **Verification tractability.** Parser symex works because parser
   state is small and the buffer read-only; a union drags the write
   window and tables into every path enumeration. (Plus the original
   doc's per-instance hardware cost and two-small-Sail-models points.)

## The honest steelman (and why it loses here)

- **Artifact count halves.** Four semantics implementations per engine
  (Sail, interp, ISS, RTL) = eight artifacts; unify → four. The
  strongest practical argument; mirror-with-tripwire makes the
  duplication a real, deliberate cost.
- **Parked items dissolve.** Run-to-completion unified core makes
  decap-then-reparse just "parse again after the edit"; the PP→MAP
  metadata pass-through question stops existing.
- **Precedent exists.** Intel IXP / Netronome NFP (one microengine
  ISA, pooled cores); eBPF/XDP in software (one ISA, one program that
  parses, decides, edits).

But the single ISA is the right answer to a *different architecture
question* — pool-of-general-cores vs pipeline-of-specialized-stages.
Pooling wins when the workload mix is unknown; one-ISA wins when a big
software ecosystem amortizes it (eBPF's verifier/JIT/tooling);
run-to-completion wins when the pipeline throughput story is already
forfeit. None hold for Nanuk: pipeline is the architecture story
(xISA / EZchip lineage), the "ecosystem" is tiny per-stage firmware.

**Middle point dismissed:** a RISC-V-style shared base
(MOVI/branches/JMP) + P/M extensions formalizes what the siblings
share informally. The sharing already exists where it's cheap
(_asm_core.py, encoding idioms, Sail machinery, school rules) without
the coupling cost — PP v0 is frozen; a shared base would let
MAP-driven base changes ripple into a frozen ISA. Aesthetics that
purchase a versioning problem.

## The pedagogical clincher

The project optimizes for education. A unified ISA teaches
general-purpose ISA design — RISC-V already teaches that better.
The two-ISA design teaches Nanuk's actual thesis: **an ISA is the
interface contract for a job, and different jobs get different
contracts.** The contrast pairs are the curriculum — bit machine vs
byte machine, cursor vs header-relative, read-only vs read-write,
HALT-verdict vs SEND-delta — and they are only visible because there
are two ISAs to put side by side. Doing the design exercise twice is
the course; merging deletes the second half.

## Doctrine

Combined with the deparser doc: three engines is too many (nothing to
reassemble), one is too few (the boundary is load-bearing —
semantically, verificationally, pedagogically). **Two is the minimum
number of engines that makes the stage contract architectural rather
than a software convention.**
