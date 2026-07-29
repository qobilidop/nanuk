# Conformance vectors

Committed, language-neutral golden vectors: the executable half of the spec.
Every implementation of the Nanuk ISAs — the Sail-generated emulators, the
Python ISS, the Amaranth RTL, and any future `sw/<language>` or `hw/<tool>`
port — answers to these files. A new implementation starts here, not by
writing its own test data.

The mechanism is Bril's (see
`docs/notes/2026-07-29-bril-study.md`): one shared corpus with committed
expected outputs that all implementations diff against. Where Bril's
ecosystem lacked that reach, its implementations drifted; a port with
private test data is where drift hides.

## Format

One JSON file per (program, corpus) pair, under `pp/` (parser only) and
`pipeline/` (composed PP → MAP). Files are self-contained: they carry the
table plane and full input state, and reference programs by repo-relative
path into `examples/` (programs are referenced, never copied — the
benchmarks rule). Expectations are the *complete* result contract, including
`steps`: the cost model is part of what implementations must agree on
(Bril's `total_dyn_inst` lesson).

```jsonc
{
  "suite": "pipeline",                 // or "pp"
  "programs": {"pp": "examples/...", "map": "examples/..."},
  "tables": [ {"key_width": 48, "action_width": 8, "entries": {"0x...": "0x..."}} ],
  "vectors": [
    {
      "name": "plain",
      "frame": "<hex>",                // input frame
      "md_in": [0, 0, 0, 0, 0, 0, 0, 0],  // md window at ingress (slot 0 = ingress port by switch convention)
      "pp": { "verdict": 0, "error": 0, "payload_offset": 34, "steps": 21,
              "hdr_present": [...], "hdr_offset": [...], "md": [...] },
      "map": { "verdict": 0, "error": 0, "md": [...], "delta": 0, "steps": 4,
               "frame": "<hex or null>" }   // null on drop/error; absent/null on PP short-circuit
    }
  ]
}
```

Error vectors assert exact error *codes* — the codes are ISA contract, so
implementations must agree on rejection and its reason (stronger than
Bril's exit-code-only error conformance).

## Regenerating

The Sail emulators are the golden producers:

    cd sw/python && uv run python ../../spec/vectors/gen.py

Regeneration must be a no-op against the committed files; the emulator leg
of `sw/python/tests/golden/test_conformance_vectors.py` enforces exactly
that (mirror-with-tripwire). The ISS leg and
`hw/amaranth/tests/test_vectors_cosim.py` hold the other implementations to
the same files — the RTL leg needs no emulator binary, so it runs ungated.

## Scope

v1 covers the demo examples (l2l3l4, nanukproto push/pop, drop_all,
map_l2fwd, map_ttl). The benchmark-ladder examples (mpls_sp, overlay_dc,
union, srcroute, icmp_echo, calc) are covered by their committed acceptance
tests and are listed as explicit exceptions in
`sw/python/tests/test_no_orphans.py`; extending the vectors over the
benchmark ladder is the natural follow-up. SIIT's vectors live at
`benchmarks/siit/vectors/` (they answer to the RFC audit, not just the
ISA) and stay there as a sibling corpus.
