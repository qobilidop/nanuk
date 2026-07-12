"""nanuk: four descending abstraction levels of one parser project.

nanuk.lang -- protocol-level eDSL (what you write)
nanuk.ir   -- portable parser-level IR (what tools exchange)
nanuk.isa  -- the architectural contract (encodings, asm, reference sims)
nanuk.rtl  -- the implementation below it (Amaranth cores + switch)

Demo programs are content, not code: they live in examples/ at the repo
root and are never imported by shipping code. What programs need at
runtime ships as library instead (nanuk.lang.headers).

This file stays import-free: pulling in nanuk.lang from Pyodide must never
drag amaranth (nanuk.rtl only, behind the `rtl` extra) into the bundle.
"""
