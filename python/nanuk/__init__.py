"""nanuk: four descending abstraction levels of one parser project.

nanuk.lang -- protocol-level eDSL (what you write)
nanuk.ir   -- portable parser-level IR (what tools exchange)
nanuk.isa  -- the architectural contract (encodings, asm, reference sims)
nanuk.rtl  -- the implementation below it (Amaranth cores + switch)

nanuk.examples holds the demo corpus: asm/eDSL twin programs, shipped in
the wheel because the playground imports them inside Pyodide.

This file stays import-free: pulling in nanuk.lang from Pyodide must never
drag amaranth (nanuk.rtl only, behind the `rtl` extra) into the bundle.
"""
