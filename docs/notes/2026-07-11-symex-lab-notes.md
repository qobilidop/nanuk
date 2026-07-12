# Lab notes: the symbolic executor — programs as constraint generators

*2026-07-11. The formal satellite's first piece: `nanuk_ir.symex`, Z3 path
enumeration over parser IR.*

## What it is

Total semantics and bounded iteration were designed into the ISA partly to
make this satellite cheap, and it was: ~250 lines. The packet becomes a Z3
byte array + symbolic length; interp's semantics translate op-for-op into
bitvector terms (EXT's 9-byte window read is `Concat` of `Select`s with a
symbolic alignment shift — the same algorithm as Sail's `read_pkt_bits`,
in constraint form). Every EXT/advance forks a feasible in-bounds path and
a feasible header-violation path; dispatch forks per case with accumulated
≠ constraints. Step accounting mirrors interp tick-for-tick, so each path
carries an *exact* predicted (verdict, error, steps) — not just an outcome
class.

## The payoffs, demonstrated

- **Path-coverage corpus generation**: every feasible path yields a
  witness packet (`gen_corpus`). For l2l3l4 that's the accept paths (plain,
  VLAN, QinQ within the unroll bound, options, non-UDP), the drop path,
  and the violation frontier — machine-derived, no scapy.
- **Witness validity is differentially proven**: each witness reproduces
  its exact prediction on interp AND the golden emulator. A symex bug,
  an interp bug, or a Sail bug would surface as a three-way disagreement.
- **The tunnel invention test**: given only the nanukproto parser IR,
  symex produces a packet with the right EtherType, magic `0x4E4B`, and
  version nibble — and the golden model accepts it with `h_nk` marked.
  The parser program's constraints ARE a packet generator.
- **`reachable_states`**: dead states are now detectable (all demo
  programs: fully reachable).

## v1 bounds, stated honestly

Loops are cut by a per-state `unroll` visit cap (default 3) and
enumeration by `max_paths` — symex() is an **under-approximation**: every
emitted path is feasible with an exact witness, but deep QinQ stacks
beyond the cap aren't enumerated. Nothing it says is wrong; there are
things it doesn't say. (The step budget alone admits 256-deep paths —
exhaustive enumeration there is a non-goal for v1.)

Not yet: MAP-side symex (concrete tables make it easy; symbolic table
contents are a design question), the read-before-write property, and
translation validation (Alive2/Gauntlet-style, with Isla for the asm side
— its own satellite row).

## Notes

- z3-solver lives in the compiler/lang dev groups only — never in wheels,
  so the playground bundle is untouched.
- The differential tests live in `lang/tests/test_symex_parity.py` (that
  env has the whole stack); pure-IR unit tests in
  `compiler/tests/test_symex.py`.
