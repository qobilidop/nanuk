"""The demo corpus: each example pairs a hand-written ISA-level program
(`.asm`, the teaching copy) with its language-level twin (`.py`, the eDSL
copy); the parity tests hold the two behaviorally identical.

This lives inside the package (not a repo-root `examples/`) so the demos
ship in the wheel: the playground imports `l2l3l4` inside Pyodide (the
baked PP rig for composed runs, and the header values that
`nanukproto` builds on).

drop_all/ is asm-only by design: the negative gate for the SimBricks demo
(load it and the network goes dark).
"""
