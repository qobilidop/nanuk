# Examples

Each directory pairs a hand-written ISA-level program (`.asm`, the
teaching copy) with its language-level twin (`.py`, the eDSL copy); the
parity tests hold the two behaviorally identical. `drop_all/` is asm-only
by design: the negative gate for the SimBricks demo (load it and the
network goes dark).

These are content, not library code: nothing that ships imports them.
Standard protocol headers they build on ship with the toolchain as
`nanuk.lang.headers` (the p4include pattern), so every program here reads
the way a user's program would. Tests import the eDSL twins as the
`examples` namespace package (the repo root is on pytest's `pythonpath`).
