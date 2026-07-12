"""Python mirror of the two nanuk ISAs: assemblers, encodings, and
instruction-set simulators.

Sail owns the truth (spec/sail/model/parser, spec/sail/model/map); this package is
the dependency-free mirror of the encoding and semantics layer. The spec
test suites tripwire drift: golden-word encoding pins plus differential
runs against the generated emulators.
"""
