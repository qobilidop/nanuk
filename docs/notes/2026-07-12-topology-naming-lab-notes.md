# Lab notes: repo topology for many implementations, and the naming doctrine

*2026-07-12. Covers the sw/hw/spec root triad, extracting the Amaranth RTL
and the IR schema into their own homes, adopting buf for schema tripwires,
and the naming doctrine that renamed nearly everything (16 commits,
`3bb5362`..`5e447d3`).*

## The thesis: one spec, many implementations

The day started with a language question — what besides Python for the SW
stack (verdict: Rust for rigor, OCaml for Sail kinship, TypeScript for a
self-hosting playground) — and the plan crystallized immediately after: there
will be several SW implementations and several HW design solutions, so the
repo topology should say so. Three moves, each a commit series:

    spec/{sail,proto}    the contracts (ISA semantics, IR interchange)
    sw/{python,…}        software implementations
    hw/{amaranth,…}      hardware implementations

`spec/` is a deliberate re-litigation of the 0649a81 rename that had
*removed* it: that decision was made when the root was medium-first
(`sail/ python/ web/`). With `sw/` and `hw/` the root went role-first, and
the triad completes it — the authority claim belongs back in the path. Inner
dirs stay tool/language-named (tool-per-path holds one level down).

Two extraction seams turned out to be already clean, which is worth
remembering as evidence the layering was honest:

- `nanuk/rtl` imported *nothing* from `nanuk.{isa,ir,lang}` — all coupling
  lived in the cosim tests. That's the correct place: cosimulation is the
  conformance boundary, not a library dependency. So `hw/amaranth` is its
  own uv project whose *design* depends only on amaranth; the nanuk package
  (ISS oracle) arrives as an editable dev dependency for its tests.
- `tests/support` was promoted to `nanuk.testkit` so sibling projects can
  import the same oracle machinery. Excluded from wheels (hatch `exclude`),
  which turns the scapy-never-ships rule structural; editable installs are
  the only consumers.

## The IR schema is a contract, so it lives with the contracts

`nanuk_ir.proto` moved out of the Python package to
`spec/proto/nanuk/ir/v0/` (path mirrors the proto package — every schema
toolchain assumes it). Decisions that came with it:

- **Gencode-only packages.** Wheels ship `nanuk_ir_pb2.py`, never the
  `.proto` — the pregenerated-binding convention (prost crates, googleapis).
  The schema's one distribution channel is `spec/proto/`; the BSR is the
  future channel if an external consumer appears (deferred; buf.yaml carries
  no module name yet).
- **No canonical IR byte encoding — intentional.** The canonical artifact of
  record is assembly/binary; IR equality is semantic. Cross-language golden
  tests must compare parsed messages or lowered output, never encoded IR
  bytes (they'd be flaky by design and would silently crown one encoder
  canonical).
- **buf tripwires.** `buf lint` (v0 excepted from PACKAGE_VERSION_SUFFIX —
  buf insists versions start at v1) and `buf breaking` vs HEAD~1 in CI, buf
  CLI pinned in the devcontainer. `buf format` deliberately *not* enforced:
  it flattens the hand-aligned teaching comments and mangles continuation
  comments. Since v0 is the mutable dev-phase contract, breaking is allowed
  but only consciously: `[ir-breaking]` in the commit message passes CI, and
  the acknowledgment lands in history. Delete the hatch at the v1 freeze.
  The tripwire-forces-acknowledgment shape, again.

The hatch got its first real use the same day (the symmetry sweep below) —
verified beforehand that the check actually fires on a field renumber.

## Naming: four levels, two tiers

The trigger: "nanuk core" was claimed by the *parser* RTL class (`NanukCore`
predated the second processor), while the design docs had already defined
"PP — parser processor" and "MAP — match-action processor". The doctrine
(full write-up: `docs/superpowers/specs/2026-07-12-naming-doctrine.md`):

- **nanuk** = family name only → packaged roles are qualified
  (`nanuk_switch` — né `nanuk_hw` — and someday `nanuk_nic`).
- **the nanuk core** = the composed PP→MAP datapath, the reusable IP.
- **PP / MAP** = the processors ("engines" in prose).
- **unit** = reserved for future sub-processor blocks (lookup unit,
  checksum unit).
- Two tiers: paired *types* spell the engine (`Parser*`/`MatchAction*`),
  paired *tokens* go short (`pp`/`map`).

The rejected-names list is a keeper for the book — especially MAU, where the
*strongest* prior art (Tofino) is the reason to refuse it: their Match-Action
Unit is a reconfigurable stage, not an instruction processor, so borrowing
the name would tell the best-informed readers exactly the wrong story.

Applying the doctrine became a full symmetry sweep once Bili named the
principle: **PP is never the unmarked default**. `iss.py`-beside-
`iss_map.py` was the original sin; now every pair marks both sides, through
Python modules (`pp_asm`/`map_asm`), proto structure
(`ParserProgram`/`MatchActionProgram`, an `[ir-breaking]` commit), tools
(`nanuk-pp-asm`, `nanuk-pp-emu`, `NANUK_PP_EMU`), Sail dirs
(`model/{pp,map}`), and the eDSL (`parser.py`/`match_action.py` — the lang
layer spells its domain because learners read it). One collision exception
recorded in the doctrine doc: hw keeps `PPResult`/`MAPResult` because the
spelled names would clash with testkit's `ParserResult` in every cosim
test's imports.

## Gotchas

- `devcontainer up` silently reuses the running container after an image
  rebuild — buf "wasn't installed" until `--remove-existing-container`.
- Passing a newline-separated file list as one quoted shell variable to
  `sed`/`perl` makes it one giant filename; the tools *warn and exit 0*, so
  `&& echo done` lies. Use `xargs`. (Bitten twice in one day.)
- BSD sed has no `\b`; use perl for word-boundary renames — and expect verb
  collateral ("tests validate it" → "tests pp_validate it") worth a diff
  review pass.
- pdoc imports what it documents: `nanuk.ir.pp_symex` needs z3 in the docs
  env, which is why API docs build from `hw/amaranth` (the one env that
  imports both packages) with z3 in its docs group.
- The Rosetta gcc segfault flake hit again during the `nanuk_switch`
  component rebuild; `retry_make` absorbed it as designed.
- CI's `buf breaking` compares only the last commit hop (`HEAD~1`), so a
  multi-commit push can carry an unexercised mid-batch break. Accepted: the
  local `--against main` command in CONTRIBUTING is the thorough form.

## State at close

All suites green at every step: 12 ctest (fresh Sail configure+build at the
new paths, twice), SW 375, HW 113, playground 11, SimBricks component
rebuilt and linked against the renamed Verilated processors. Eight commits
of the naming arc unpushed at wrap-up. Next moves on the table: `sw/rust/`
or `hw/sv/` — the topology, the schema home, and the naming were all
preparation for exactly those.
