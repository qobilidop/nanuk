# Plan — SIIT demo, part B: the Jool oracle

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-07-18
**Spec:** [SIIT demo design](../specs/2026-07-18-siit-demo-design.md), test leg 4.
**Depends on:** Plan A (complete: reference translator, 70-vector suite,
program, parity, cosim — branch `siit`).
**Status:** Ready.

**Goal:** Replay Jool's graybox fixtures — the independent-interpretation
oracle — against the Nanuk SIIT reference translator, classifying every
in-scope fixture as pass / divergence / out-of-scope, with divergences
documented in the audit as findings.

**Architecture:** Zero GPL bytes in-tree. A pinned-commit fetch script clones
Jool into gitignored `third_party/`; an extraction step parses the graybox
test *scripts* (which pair sender/expected `.pkt` files and carry byte-
exception masks) into a manifest; a replay harness wraps the raw L3 fixtures
in our Ethernet framing, mirrors the suite's Jool configuration into a
`SiitConfig`, runs `translate()`, applies the masks, and emits a
per-fixture report consumed by a generated section of the audit. The oracle
compares against the **reference translator** (the executable spec); the
program is already proven byte-identical to the reference by Plan A's legs,
so a reference-level comparison is a program-level comparison by transitivity.

**Tech stack:** Python in `sw/python` (testkit + tests), shell for the fetch
script, network-gated like cosim (`NANUK_JOOL=1`).

## Global constraints

- **Zero GPL bytes committed.** Jool's clone lives in gitignored
  `third_party/jool`; fixtures are read from there at test time; nothing
  under `benchmarks/` or `sw/` may embed fixture bytes, and no `.pkt`
  content may appear in committed test code or reports beyond
  fixture *names*, byte *offsets*, and our own prose describing differences.
- Zero ISA / core-interface / Sail / RTL changes; zero changes to the
  committed vectors, the hand asm, or the twins.
- Python only via `./dev.sh`; `uv run --no-sync`; ruff after.
- Jool replay tests are gated on `NANUK_JOOL=1` AND the presence of the
  clone (skip with a reason naming the fetch script otherwise). CI does NOT
  run them in this plan (a cached-clone CI job is a later nicety; keep the
  suite green without network).
- Divergences are findings, not failures: the replay test FAILS only on
  (a) harness errors, (b) a fixture classified pass whose bytes mismatch,
  or (c) an UNDOCUMENTED divergence — every divergence must have a matching
  entry in the audit's divergence log (that closure IS the test).
- Casing: "Nanuk" in prose; the artifact is the "SIIT translator";
  Jool is named as the independent oracle, never the spec.

## Frozen decisions

- **Pin**: clone `https://github.com/NICMx/Jool` at the current `main` HEAD
  at implementation time; record the 40-char SHA in
  `benchmarks/siit/jool.lock` (committed: URL + SHA + the graybox subpaths
  used — a pointer file, no GPL content).
- **Scope**: the SIIT graybox suite only (`test/graybox/test-suite/siit/`):
  the `pktgen/{sender,receiver}` combinatorial pairs (udp46/udp64/tcp46/
  tcp64/icmp-ping groups) plus the `7915/` lettered pairs. NAT64 dirs are
  out of scope (stateful). Manual dir: out of scope this plan.
- **Pair + mask source of truth**: the suite's own scripts (`siit/test.sh`
  and the `rfc/` docs beside it) — parse, don't guess. Each manifest entry:
  test name, sender file, expected file, direction, exception byte-offsets
  (their `IDENTIFICATION=4,5,10,11`-style lists), source group.
- **Classification taxonomy** (the report's verdict per fixture):
  - `pass` — our reference output matches expected bytes under their masks
    (plus our Eth header stripped/added at the boundary).
  - `divergence(<id>)` — bytes differ ONLY in ways attributable to a named,
    documented policy difference; each divergence id is a row in the audit's
    divergence log (existing rows: DF-always-set, TTL-decrement policy if it
    surfaces, zero-checksum drop-vs-forward, trailer passthrough). Masks for
    a named divergence are DERIVED (e.g. "TTL byte + IPv4 checksum bytes"),
    never free-form per-fixture fudges.
  - `out_of_scope(<audit-id>)` — the fixture exercises a deferred/refused
    capability (ICMP errors, fragments, hairpin EAM, options-ext-headers…);
    must cite the audit row that defers/refuses it.
  - `drop_agrees` — Jool's script expects no output (their drop) and our
    reference also drops; reasons recorded.
- **Ethernet boundary**: graybox `.pkt` files are raw L3. Replay wraps input
  in our framing (testkit MACs, EtherType by IP version) and strips the 14B
  Ethernet header from our output before comparison.
- **Config mirror**: read the suite's `setup*.sh` Jool commands for pool6 +
  EAMT entries; mirror into a `SiitConfig` built in the harness (NOT into
  DEMO_SIIT — the demo config stays ours).
- **Report artifact**: the harness writes
  `benchmarks/siit/jool-replay.md` (committed, regenerated by the test in a
  `--update` mode): counts by classification, the divergence log
  cross-references, and the fixture-name lists per class. Names and our
  prose only — no fixture bytes.

---

### Task B1: Fetch script, manifest extraction, config mirror

**Files:**
- Create: `benchmarks/siit/fetch_jool.sh`, `benchmarks/siit/jool.lock`
- Create: `sw/python/nanuk/testkit/jool_graybox.py`
- Test: `sw/python/tests/test_jool_graybox.py`

**Interfaces (B2 relies on):**
- `fetch_jool.sh`: idempotent; clones/updates `third_party/jool` to the
  locked SHA (reads `jool.lock`); prints the checkout path; `--check` mode
  exits nonzero if absent/wrong-SHA without fetching.
- `jool_graybox.py`: `jool_root() -> Path | None` (env `NANUK_JOOL_ROOT`
  override, default `third_party/jool`, None if absent);
  `load_manifest(root) -> list[Fixture]` where
  `Fixture(name, group, direction, sender: Path, expected: Path | None,
  exceptions: tuple[int, ...])` — parsed from the suite's scripts;
  `jool_config(root) -> SiitConfig` — pool6 + EAMT mirrored from setup
  scripts.
- Gating helper `requires_jool()` for pytest skip (mirrors the cosim-gate
  style in existing tests).

**Steps:**
- [ ] Tests first (skip-gated): manifest loads with >0 fixtures per expected
  group; every referenced `.pkt` file exists; exceptions parse to int
  tuples; a known fixture (e.g. the first rfc7915 `aa` pair) has both files
  and direction 46/64 consistent with its script call; `jool_config`
  returns a pool6 and any EAMT entries actually configured (assert against
  what the setup scripts contain — read them during implementation and pin
  the parsed values in the test).
- [ ] `fetch_jool.sh` + `jool.lock` (pin = `git ls-remote` HEAD at
  implementation time). Run it; verify `--check`.
- [ ] Implement `jool_graybox.py`; tests green with the clone present
  (`NANUK_JOOL=1`); confirm clean skip without.
- [ ] Full SW suite green (997 baseline; new tests skip without the env).
- [ ] Commit: `feat(benchmarks,testkit): Jool graybox acquisition — pinned fetch, manifest, config mirror`

### Task B2: Replay harness, classification, audit integration

**Files:**
- Create: `sw/python/nanuk/testkit/jool_replay.py`
- Test: `sw/python/tests/test_jool_replay.py`
- Create (generated, committed): `benchmarks/siit/jool-replay.md`
- Modify: `benchmarks/siit/audit.md` (divergence log grows as findings
  demand), `benchmarks/siit/README.md` (leg 4 status + counts)

**Interfaces:**
- `jool_replay.replay(fixture, cfg) -> Result(classification, detail)`
  implementing the frozen taxonomy; `replay_all(root) -> Report`;
  `write_report(report, path)` deterministic.
- The pytest asserts the closure property: no fixture may end
  `divergence(<id>)` with an id absent from audit.md; no `pass` with
  mismatching bytes; counts in the committed `jool-replay.md` match a fresh
  in-test regeneration (tripwire, like the vector regen test).

**Steps:**
- [ ] Tests first (the closure/tripwire asserts above, skip-gated).
- [ ] Implement replay: Eth wrap/strip, masks, classification. Expect
  discovery here — run, READ the actual divergences, classify them one by
  one. For each new divergence class: decide whether it is (a) our
  documented policy (→ audit divergence-log row, derived mask), (b) a BUG
  in our reference (→ STOP, report as finding to the controller — the
  oracle just did its job), or (c) out-of-scope reach (→ cite audit row).
  Do not bulk-mask anything.
- [ ] Generate `jool-replay.md`; update audit divergence log + README leg-4
  status with real counts.
- [ ] Full SW suite green both with and without `NANUK_JOOL=1`; ruff.
- [ ] Commit: `feat(benchmarks): Jool graybox replay — independent-interpretation oracle, classified`

### Task B3: Close out part B

- [ ] Append a section to docs/notes/2026-07-18-siit-core-lab-notes.md (or a
  sibling note if cleaner): what the oracle found — counts, each divergence
  class and its story, any reference bugs caught.
- [ ] Gates: SW suite (with and without the env), ruff; push; CI green on
  the PR.
- [ ] Commit: `docs(notes): what the Jool oracle found`

## Self-review notes

- The one deliberately open discovery: the actual divergence classes —
  the plan freezes the taxonomy and the closure property, not the findings.
- B2's "STOP on reference bug" is the oracle's whole purpose; the controller
  adjudicates whether the reference or Jool reads the RFC correctly.
- CI network dependency deliberately avoided; a cached-clone CI job is
  parked for later.
