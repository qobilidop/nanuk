"""Nanuk: three descending abstraction levels of one parser project.

nanuk.lang -- protocol-level eDSL (what you write)
nanuk.ir   -- portable parser-level IR (what tools exchange)
nanuk.isa  -- the architectural contract (encodings, asm, reference sims)

The RTL below the ISA lives in sibling hardware projects (hw/amaranth's
nanuk_amaranth package); their cosim tests consume this package as the
oracle.

nanuk.testkit is not a level: it is the conformance machinery (golden-model
emulator rig, pcap fixtures) shared by every test suite. Dev-only — it
imports scapy and is excluded from wheels.

Demo programs are content, not code: they live in examples/ at the repo
root, standalone by design, and are never imported by shipping code.

This file stays import-free: pulling in nanuk.lang from Pyodide must never
drag anything heavy into the bundle.
"""
