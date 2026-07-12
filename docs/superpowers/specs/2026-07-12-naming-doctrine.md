# Nanuk — Naming Doctrine

**Date:** 2026-07-12
**Status:** Adopted and fully applied (commits `9feef29`..`5e447d3`). Governs
all future implementations (`sw/<language>`, `hw/<design-tool>`) and any new
subsystem naming.
**Siblings:** [Single-ISA doctrine](2026-07-12-single-isa-doctrine.md) ·
[Deparser/editor doctrine](2026-07-12-deparser-editor-doctrine.md)

## The hierarchy

Four levels, one word each. Most naming confusion is one level's word used at
another level.

| Level | Word | Nanuk name | What it is |
|---|---|---|---|
| Family | — | **Nanuk** | The project/family name only — never a device |
| Packaged device | role-qualified | **nanuk_switch**, future **nanuk_nic** | The core + role periphery (ports/forwarder for a switch; host interface/DMA for a NIC) |
| Composed datapath | **core** | **the Nanuk core** | PP → MAP composed: the reusable IP ("switch core" in ASIC vocabulary; the future Tiny Tapeout tile) |
| ISA-running block | **processor** | **PP**, **MAP** | Own ISA, PC, registers, fetch–decode–execute, assembler, ISS, Sail spec |
| Sub-processor block | **unit** (reserved) | future: lookup unit, checksum unit | Functional blocks inside a processor — no ISA, no PC (CPU "functional unit" sense) |

"Engine" stays the informal *category* word in prose (the doctrine docs speak
of "two engines"); the proper names and expansions are PP — parser processor
and MAP — match-action processor, as the MAT-arc design doc already defined.

## The two-tier rule

- **Paired types spell the engine:** `Parser*` / `MatchAction*`.
  Proto messages (`ParserProgram`/`MatchActionProgram`), Python classes
  (`ParserProcessor`/`MatchActionProcessor`, `ParserInterpResult`/
  `MatchActionInterpResult`), eDSL classes (`Parser`, `MatchActionProgram`).
- **Paired tokens use the short forms:** `pp` / `map`.
  Files and modules (`pp_asm.py`/`map_asm.py`, `hw/amaranth/{pp,map}.py`),
  Sail model dirs (`spec/sail/model/{pp,map}`), Verilog modules
  (`nanuk_pp`/`nanuk_map`), CLIs (`nanuk-pp-asm`/`nanuk-map-asm`,
  `nanuk-pp-emu`/`nanuk-map-emu`), env vars (`NANUK_PP_EMU`/`NANUK_MAP_EMU`).

Refinements learned while applying it:

- **PP is never the unmarked default.** The original sin was `iss.py` beside
  `iss_map.py` — the parser as the unqualified case. Every pair marks both
  sides.
- **Functions in per-engine modules stay unmarked** — the module carries the
  engine (`pp_asm.assemble` / `map_asm.assemble`). Only names co-exported
  into one namespace mark both sides (`pp_interp`/`map_interp`,
  `to_pp_asm`/`to_map_asm`).
- **The lang layer spells its domain** (`parser.py`, `match_action.py`): the
  eDSL is what learners read, so its files match its classes rather than the
  token tier.
- **Mnemonic-tier proto payloads keep distinctive short names** (`Extract`,
  `MapLoad`, `Lookup`): there `Map` is a generic-word disambiguator, like the
  bare distinctive names on both sides — only the structural triple
  (Program/State/Op) carries the spelled pair.
- **Collision exception:** `hw/amaranth` keeps `PPResult`/`MAPResult` — the
  spelled names would collide with testkit's `ParserResult` in every cosim
  test's imports. Symmetry is the hard rule; the tier is a guideline.

## Family-name casing (adopted 2026-07-12)

The family name follows the same two tiers as everything else:

- **Nanuk** is the spelled form — prose, headings, UI copy, docstring
  sentences, the paper/book. The Sail/`sail`, Amaranth/`amaranth`,
  Linux/`linux` convention.
- **nanuk** is the token form — repo, PyPI package, paths, and every
  derived identifier (`nanuk_switch`, `nanuk-pp-asm`, `NANUK_PP_EMU`,
  `project(nanuk)`, `nanuk.ir.v0`).

Boundary cases, decided:

- **Module-path headlines stay tokens.** A docstring that opens with the
  module's own dotted/underscored path (`nanuk.ir: ...`,
  `nanuk_amaranth: ...`) names the token and keeps its case; a headline
  that opens with the bare brand (`Nanuk: three descending abstraction
  levels...`) is prose and capitalizes — the NumPy-docstring convention.
- **Dependency references are tokens** (`"nanuk"` in a deps list,
  `nanuk = { path = ... }`, pdoc/import arguments).
- **The Czech common noun** *nanuk* (popsicle) is not the brand and
  stays lowercase when cited.
- An all-lowercase wordmark (the systemd/npm pattern) was considered and
  rejected: it fights sentence casing forever, third parties capitalize
  it anyway, and the name is a borrowed proper noun (Inuktitut *nanuq*).

## Rejected names, and why (keep for the book)

- **MAU** — the strongest prior art (Tofino/RMT's official term) and
  precisely why it's wrong here: Tofino's Match-Action Unit is a
  reconfigurable match *stage* (TCAM/SRAM crossbars + VLIW action engines),
  not an instruction-fetching processor. The name would tell P4-literate
  readers exactly the wrong story against Nanuk's central "ISA-based, not
  PISA-based" claim.
- **PPP** — Point-to-Point Protocol to every networking reader.
- **PU / PaP** — no meaningful precedent.
- **ParsingProcessor / PacketParsingProcessor** — HW naming uses noun
  adjuncts ("branch unit", not "branching unit"); "packet" discriminates
  nothing inside a packet processor.
- **PP ≈ pre-processor** was weighed and dismissed: that reading lives in
  C-toolchain contexts; here PP never appears without `nanuk_`/`map` nearby,
  and every definition site expands it.
- **"Nanuk" as a device name** — rejected to keep the family/role split:
  the same core will embed in differently-packaged roles (`nanuk_switch`
  today, `nanuk_nic` someday), the ARM-Cortex/Corundum shape.
