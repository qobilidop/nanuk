# Examples

Each directory pairs a hand-written ISA-level program (`.asm`, the
teaching copy) with its language-level twin (`.py`, the eDSL copy); the
parity tests hold the two behaviorally identical. `drop_all/` is asm-only
by design: the negative gate for the SimBricks demo (load it and the
network goes dark).

These are content, not library code: nothing that ships imports them,
and each program is standalone by design — headers and wire constants
are declared in the file that uses them, so every example reads complete
on one page. Tests treat the eDSL twins as fixtures and load them by
path (`tests.support.load.load_example`).
