# General improvement run — backlog (2026-07-28)

Status: DRAFT — pending Bili's strikes / pre-authorizations. Becomes the work
queue for an autonomous run once frozen.

Ground rules for the run (from the mandate discussion):

- Per item: plan briefly, implement, **verify by running** (devcontainer is
  authoritative; never verify by reading), commit with a readable message,
  push, watch CI green before the next item.
- Judgment calls: make them and record them (commit message or lab notes).
- Genuine Bili-calls not pre-authorized below: do not stop — log in
  `docs/superpowers/plans/2026-07-28-decisions-needed.md` and move on.
- Out of bounds: PP ISA v0 (frozen), anything on a parked list unless named
  here, book *content* rewrites (his review pending), MLIR, paper, Tiny Tapeout.
- Stop when the queue is dry or confidence runs out; write a handoff note
  (done / skipped / decisions needed).

## Tier A — verified defects and drift (autonomous, in unless struck)

1. **Fix the dead `default=s.drop` branch** — `sw/python/nanuk/lang/match_action.py:455`
   compares a bound method by identity (`default is self.drop` is always False);
   the docstring at :441 advertises a shorthand that never fires. Fix (sentinel
   or name compare), add the test that would have caught it, keep behavior
   contract explicit.
2. **Close the fuzz coverage holes** — MAP reg-reg ALU (ADD/SUB/AND/OR/XOR)
   has no structured fuzz generator (only hand cosim cases; the drift warning at
   `hw/amaranth/tests/test_map_cosim.py:117` is still live), and PP
   `encode_ldmd` is **never emitted** by `random_instruction`
   (`test_fuzz.py:50` covers 11 of 12 encoders — unflagged blind spot). Build a
   structured MAP instruction generator, add LDMD to the PP one, add a
   generator-vs-encoder-count tripwire, add a `case _` fail-fast.
3. **`docs/development.md` is concretely wrong in three places** — :23 names
   `run_beats12.sh`/`run_beat3.sh` but not `run_siit.sh`/`build_guest_kernel.sh`;
   :34 says "no type prefixes" while 28 of the last 30 commits use them (decide
   which way and make doc match reality — recording the decision is fine);
   :39 says "future book". Also: test matrix has no row for the book build or
   the `NANUK_JOOL=1` replay suite.
4. **Book discoverability** — README Layout block omits `book/` and `web/`;
   header links Landing + Playground but not the live book; `web/site/` landing
   page has zero mentions of the book. Wire all three.
5. **Pin mdBook in `book.toml`** — the v0.4.52 pin lives only in
   `pages.yml:45`; local builds float. Also consider `[output.linkcheck]` (or
   equivalent CI link check) so broken intra-book links fail loudly.
6. **In-tree notes for parked minors that lack them** — the intentional
   `md[2]` write-ordering divergence between `examples/siit/translate.py:116`
   and `translate.asm:105` (T5-m3) is documented only in lab notes; a reader of
   either file can't know the twins differ on purpose. One comment each side.
7. **Warmup-ping counter over-report** — `benchmarks/e2e/nanuk_demo_siit.py:126`
   known +1 in the exhausted-attempts path. Two-line fix while nearby.

## Tier B — mechanical improvements with recorded judgment (autonomous, in unless struck)

8. **Book staircase stage 1: `{{#include}}` wiring** — the toolchain survey's
   own plan, unstarted: convert the 3 pasted code blocks (`07-asm-iss.md`,
   `09-lang.md`, `13-siit.md`) to `{{#include}}` from the real tested sources
   with anchors. Mechanical, orthogonal to the pending content review; zero new
   tooling (Embedonomicon pattern).
9. **Promote spec debt from plan file to spec docs** — three already-decided
   conventions sit only in `plans/2026-07-13-map-isa-v0.1-additions.md:116-124`:
   the LOOKUP action-id-packing + BEQ-chain convention (demonstrated by
   `examples/calc`, never written into the MAP ISA spec), the
   headroom-as-scratch idiom (three audits leaned on it), and the PP→md
   header-present-bitmap convention. Writing down decisions already made — no
   new design.
10. **Plans-directory status hygiene** — 364 unchecked `- [ ]` boxes across
    executed plans make the directory unreadable as status. Add a
    `Status: complete (see lab notes)` header line to each executed plan
    (backfilling boxes is busywork; a header is honest). Record the convention
    in `docs/development.md`.
11. **CI seam review** — the book build and `npm test` run only in the
    path-filtered `pages.yml`, so "CI green" means different things per
    workflow (a `spec/sail` or `examples/` change never builds the book).
    Minimal fix: widen the pages build-job path filter or add a cheap
    build-only job to `ci.yml`. Record cost trade-off in the commit.
12. **`[ir-breaking]` hatch tracking** — the escape hatch's deletion trigger
    ("the v1 freeze") lives only in a YAML comment. Add it to the spec/proto
    README so the obligation is findable.

## Tier C — needs Bili's pre-authorization (strike, or check to authorize)

13. [ ] **imm-width doctrine choice** — parser imm width is checked only at
    `pp_lower.py:157` while MAP range-checks at eDSL/IR level; validate/interp/
    symex accept IR that lower rejects. The lab note parks "the choice of
    doctrine, not the fix". *Authorize = I pick one (leaning: IR-level check for
    both engines, mirroring MAP), record it, implement the small fix.*
14. [ ] **File the i40e_bm bug upstream to SimBricks** — shrunk v6→v4 frames
    delivered as all-zeros; E1000 is a recorded workaround, report never filed.
    Outward-facing, so held for your go. *Authorize = I draft and file the
    issue with the minimal repro; or "draft only" for your review.*
15. [ ] **MAP ISA v0.x: counter tables (T2) + LPM (T3)** — the one big feature
    item; benchmarks ladder built, implementation stopped at your design calls
    (table-kind config field, LOOKUP/counter mutual totality, counter entry
    format/readback, counter-vs-learning boundary naming). *Authorize = full
    vertical, decisions made-and-recorded per the established pattern; or keep
    for discussion-first.* Sized ~3-4h alone.
16. [ ] **MAP symex with concrete tables** — parked leg (e) of SIIT parity;
    concrete-table execution is straightforward, and your taste note bundles
    the `symex.py` class refactor with it. Symbolic tables stay out (open
    design question). *Authorize = concrete tables + refactor only.*
17. [ ] **Golden vectors: root-level language-neutral conformance suite** —
    your recorded future idea (with declarative encoding tables generated from
    one truth) ahead of multi-language SW/HW ports. Medium design weight.
    *Authorize = design doc + first extraction; or discussion-first.*

## Sizing

Tiers A+B ≈ 4-5 focused hours. Adding item 15 (or 17) fills the ~8-hour
mandate. Items 13/14/16 are each well under an hour.
