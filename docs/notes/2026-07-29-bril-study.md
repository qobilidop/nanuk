# Bril study: what Nanuk should take, discuss, and refuse

> **Status: complete (2026-07-29).** Study done from primary sources; proposals
> tiered below. Tier A executed this session (commits referenced from the git
> history); Tiers B and C await Bili.

Studied at pin `e8b05b7` (sampsyo/bril upstream HEAD, 2026-05-18), cloned to
gitignored `third_party/bril`. Method: four parallel deep-reads (language
reference & docs structure; testing/conformance machinery; the multi-language
implementation ecosystem; the paper/course context via the web), all claims
below verified against the actual tree or cited URLs, not summaries.

## 1. What Bril actually is

Bril (Big Red Intermediate Language, Adrian Sampson, Cornell) is the teaching
IR for the CS 6120 grad compilers course. Canonical form is JSON ("Bril
programs are just JSON"); a subordinate human text format round-trips through
`bril2json`/`bril2txt`. The core is tiny — the complete core semantics chapter
is 69 lines of Markdown — and everything else is opt-in extensions (memory,
float, char, speculation, two generations of SSA, imports, dynamic types,
bitcast). Around that core sits a polyglot monorepo: a TypeScript reference
interpreter (`brili`), type checker (`brilck`), Rust library + fast
interpreter (`bril-rs`, `brilirs`), Cranelift and LLVM backends, OCaml/Swift
libraries, a C bytecode VM (`fastbril`), an MLIR dialect, Racket and
Rust-subset and TypeScript frontends, ~127 community benchmarks, and mdBook
docs. MIT license, 166 unique commit authors, most components attributable to
named student projects from the course.

**Correction to a belief held coming in:** there is no Bril paper. The
supposed "SPLASH-E 2023 paper" does not exist (verified against DBLP, the
SPLASH-E 2023 proceedings, and Sampson's own publication list); the artifact
under that title is a ~2,200-word personal blog post (July 2024,
cs.cornell.edu/~asampson/blog/bril.html) with **no evaluation of any kind**.
Sampson's actual SPLASH-E paper is LambdaLab (2018, different project). Any
"Bril as venue precedent" argument for a Nanuk education paper is void; what
Bril actually offers our paper story is a *related-work citation and a foil*,
not a publication template.

## 2. The load-bearing mechanism: one corpus, many implementations

This is the single most important thing in the repo, and it is small:

- Every test is an input file plus a **committed expected-output file**
  (`X.bril` + `X.out`, plus `X.prof` for benchmarks). Turnt (a ~trivial
  snapshot runner) executes a command template and diffs.
- One `turnt.toml` declares multiple **environments** — `brili` (reference),
  `brilirs`, `brilift-aot`, `brilift-jit`, `brillvm`, `fastbrili` — and every
  env that doesn't override the output mapping **diffs against the same
  committed file**. There are no per-implementation expected outputs.
- Each implementation's Makefile opts into the glob subset it supports
  (`brilirs` runs everything except `spec*`; `brilift` excludes char tests).
  Capability gaps are visible as which glob lines a backend claims.
- `.prof` files pin `total_dyn_inst` — a deterministic, machine-independent
  dynamic-instruction count — across implementations, so interpreters must
  agree on stdout *and* on the cost metric.
- Error tests set `output = {}` for non-reference envs: implementations must
  **agree on rejection (exit code), not on wording**.
- Benchmarks double as tests: every benchmark runs under the same snapshot
  machinery, so the benchmark suite is also a conformance corpus.

And the equally important negative result: **where the shared corpus does not
reach, everything rots.** The correlation across ~18 components is nearly
perfect. In CI on the shared corpus: healthy (brili, brilirs, brilift,
bril-rs, bril-txt). Outside it: bril-swift is dead at one commit and cannot
decode a float literal while its (orphaned, never-rendered) doc page claims
SSA support; bril-ocaml still implements only the deprecated SSA; fastbril
carries a stale duplicate opcode table that disagrees with its own source of
truth; a ghost `struct` extension parses in the text format and compiles in
bril-llvm but is documented nowhere and runs in no interpreter; the reference
interpreter itself cannot execute `bitcast`, an extension its own type checker
accepts — caught by no test because the root Makefile's globs skip the bitcast
directory. Drift lives exactly in the gaps of the harness.

## 3. Decision-by-decision comparison

| Design axis | Bril | Nanuk | Verdict |
|---|---|---|---|
| Semantics authority | Reference interpreter is de-facto spec; docs prose; observable details (exit codes, print format) pinned only by golden files | Sail owns semantics *and* encodings; 4 tripwired implementations | **Nanuk stronger — keep.** Bril is the foil that shows the cost: interpreter-defined leak checks, unspecified div rounding, doc/impl contradictions |
| IR schema | None. Prose + 12 hand transcriptions (TS interfaces, serde, Yojson, Decodable, strcmp chains) | `spec/proto` protobuf + buf lint/breaking in CI | **Nanuk stronger — keep.** Bril's drift (positions exist in 3 shapes; ops checked-but-not-runnable) is the empirical case for a machine-readable schema |
| Conformance across implementations | Shared corpus + committed outputs + per-impl env opt-in (§2) | Pairwise pytest parity tests; no language-neutral artifact yet | **Adopt (Tier A).** This was already backlog item 17; Bril is the existence proof at ecosystem scale |
| Cost metric | `total_dyn_inst` in committed `.prof`, cross-impl enforced | `steps` in the ISA contract, cost-model-mirrored across all 4 impls — but asserted only inside pytest | **Adopt inside Tier A** — vectors carry `steps`, making the cost model part of the committed conformance artifact |
| Error contract | Exit code 2 + "agree on rejection, not wording" | Typed error codes *in the ISA result contract* | **Nanuk stronger — keep**; vectors assert exact error codes |
| Core stability | No versioning story at all; core frozen in practice (7 commits since 2020); deprecation = banner-in-place, old page kept | v0 freeze + parked-with-named-triggers + `[ir-breaking]` hatch | **Validates Nanuk.** Sampson's one big regret (SSA retrofitted as "just another instruction", later called "fundamentally flawed") is a case study in why extension points get designed with the core |
| Extension docs | Editorial chapters; partial section-template; support matrix stated on only 2 of 10 pages, others stale | Doctrine docs + ISA docs; single implementation so far | **Tier B**: adopt a support-matrix convention when the 2nd SW impl lands |
| Benchmarks | Real computations, community-contributed, doubled as tests, one-line catalog page | Corpus-derived, binding on the ISA, coverage audit | **Validates Nanuk**; vectors extend benchmarks-as-tests to all implementations |
| Docs toolchain | mdBook, deployed on push | mdBook, deployed on push | Convergent, no action |
| Packaging | Tool-per-path, no workspace, nothing published to registries | Tool-per-path doctrine, uv/npm/CMake per subsystem | Convergent, no action |
| Ecosystem engine | Course project pipeline: proposal issue → open-source repo → blog-post PR; extensions credit their student authors | Book drafted; no exercises; not soliciting contributions yet | **Tier B** — Bili's roadmap call |
| Declarative encoding truth | fastbril's BRB spec: opcode tables in `config/*.cf` generate both C headers *and* the LaTeX spec appendix | Encodings live in Sail; Python/RTL mirror-with-tripwire | **Tier B** — precedent for the recorded "encoding tables from one truth" idea, but it competes with "Sail owns encodings" |

## 4. Tier A — high-confidence, executed this session

### A1. Golden conformance vectors (`spec/vectors/`)

Executes backlog item 17 (2026-07-28 run, deferred as "design doc + first
extraction") with the design question answered by Bril's evidence.

- **What**: committed, language-neutral JSON vector files at ISA level —
  program (referenced from `examples/`, never copied, per the benchmarks
  rule), input frame + md window, expected full result contract (verdict,
  error code, header offsets, md_out, frame out where applicable, **and
  `steps`** — the `.prof` lesson). Format generalizes the proven
  `benchmarks/siit/vectors/*.json` shape. SIIT's vectors stay where they are
  (they answer to the RFC audit, not just the ISA); the suite records them as
  a sibling corpus.
- **Why `spec/`**: the vectors are executable spec — implementations answer
  to them, which is exactly the authority claim `spec/` was re-introduced to
  carry. Root stays role-first: `spec/{sail,proto,vectors}`.
- **Generator**: the Sail-generated emulators are the golden producers;
  `spec/vectors/gen.py` (run from the `sw/python` env) regenerates; a
  tripwire test asserts regeneration is byte-identical to the committed files
  (mirror-with-tripwire, same doctrine as everywhere else).
- **Consumers now**: SW runner (ISS validated against every vector; emulator
  agreement is the tripwire above) and HW runner (RTL cosim over the same
  files). Consumers later: every future `sw/<language>` and `hw/<tool>` port
  starts by pointing at `spec/vectors/` — the Bril lesson is that this must
  exist *before* the second implementation does, because per-impl test data
  is where drift hides.
- **Completeness tripwire**: a test asserts every vector file is consumed and
  every parser/MAP example is covered by at least one vector (the bitcast
  hole — "checked by one tool, run by no glob" — is the failure mode this
  kills).

### A2. No-orphans tripwires

Bril's rot inventory is a checklist of orphan classes we can make structurally
impossible: doc pages missing from `SUMMARY.md` (two of Bril's tool pages have
never rendered), expected outputs whose inputs were deleted, tests disabled by
renaming (`.bril.BROKEN`), files no glob ever runs (`benchmarks/pi.bril`).
Added a small test module asserting: every `book/src/*.md` chapter is linked
from `SUMMARY.md`; every `examples/` program directory is exercised by the
conformance vectors or an explicitly-listed exception; every vector file
parses and is consumed. Cheap, and it converts "verified by reading" into
"verified by running" for the content tree.

## 5. Tier B — worth doing, Bili's call

1. **Declarative encoding tables from one truth.** Your recorded future idea;
   fastbril's BRB spec is the working precedent (one `.cf` table → C headers
   + spec appendix tables). The design tension is real: "Sail owns
   encodings" is doctrine, so the neutral table either gets *extracted from*
   Sail (generator reads the Sail source or the emulator) or Sail itself
   consumes generated code — the first preserves the doctrine, the second
   inverts it. Needs a design discussion before any code.
2. **Per-implementation support matrix convention.** Bril states support on 2
   of 10 extension pages and is stale on the rest; the honest version is a
   generated matrix (implementations × vector subsets they pass). Cheap once
   vectors exist and a second implementation lands; premature today.
3. **Normative-idiom pass over the spec docs/book.** Bril's "It is an error
   to… / It is *not* an error to…" idiom is genuinely good spec pedagogy (the
   negative form pre-empts exactly the questions a reader asks), and its
   float-printing spec (17 digits, `%.17e` threshold) shows the value of
   pinning observable formatting. The book is awaiting your review, so prose
   passes are yours to schedule.
4. **The course loop.** Bril's ecosystem exists because the course *requires*
   open-source projects with blog-post experience reports, and the language
   credits students by name on every extension page. If the book ever grows
   exercises or Nanuk solicits contributions, this is the model — but
   `docs/development.md` currently says we are not soliciting, so this is a
   posture decision, not a task.
5. **Paper strategy note.** The Bril-precedent argument is void (§1). The
   tech-report-then-arXiv plan stands on its own; Bril's blog-post genre is
   also now a data point that a well-written unrefereed artifact can carry a
   project's story for years.

## 6. Tier C — considered and refused

1. **JSON as canonical form / schema-by-prose.** Bril's own drift record is
   the refutation; our protobuf + buf breaking-checks exist precisely to
   prevent what happened to `pos`/`bitcast`/`struct`. Keep.
2. **Interpreter-as-spec.** The reference interpreter defines leak-check
   semantics, div rounding, and arg parsing that the docs never state; two
   other checkers disagree on coverage. Sail stays the authority; the
   emulators stay generated artifacts.
3. **A brench-style pipeline runner.** brench's job (run N pipelines, first
   is golden, extract a metric, CSV out) is served in Nanuk by pytest parity
   tests plus `steps` in the vectors; a standalone tool would add surface
   without capability. Revisit only if an optimization-pass ecosystem appears
   (that is brench's actual niche: grading *optimizations*, which Nanuk does
   not have yet).
4. **Feature-flag extension matrix** (bril-rs Cargo features +
   `cargo hack --feature-powerset`). Clever for a library that must serve
   half-implemented dialects; Nanuk's frozen-core-plus-triggers model means
   there is nothing to flag. Refuse.
5. **Text format specified by example, twice.** Bril's text grammar exists
   only as two independent, disagreeing parser grammars, with undocumented
   productions (`struct`, `nullptr`). Nanuk asm stays specified by Sail
   encodings with one shared `_asm_core`. Keep.
6. **`print`/stdout as the observable.** Bril needs it (general programs);
   Nanuk's observable is the typed result contract, which is strictly
   stronger for conformance. No change.

## 7. Addendum (2026-07-29): IR-design lessons

A second look at Bril specifically as an *IR design*, against
`spec/proto/nanuk/ir/v0/nanuk_ir.proto`. At this layer the traffic mostly
runs the other way — Bril's IR is where its drift lives, and the
protobuf/buf/closed-oneof design is the antidote — but four things
transfer, and one is a mirror.

1. **One machine-readable op-signature table.** The only uniform statement
   of every Bril op's arity and types is `OP_SIGS` in `brilck.ts` — a
   declarative table driving the type checker, uncited by the docs, so
   prose and checker drift independently. Nanuk has the inverse problem in
   miniature: each op's shape is known separately by its proto message,
   `pp_validate`/`map_validate`, the lowerings, the interpreters, and
   symex — five places per op, kept honest only by tests. A declarative
   signature table (op → operand kinds, result kind, totality/error notes)
   that validation consumes and doc tables are generated from is the
   IR-level face of **Tier B item 1** (encoding tables from one truth) and
   carries the same design tension — a neutral table competing with
   proto-as-schema needs the same discussion as one competing with
   Sail-owns-encodings. Fold it into that discussion, not a separate item.
2. **The SSA-retrofit lesson, aimed at the parked dispatch accelerator.**
   `phi` failed as "just another instruction" because its meaning is not
   local — it depends on the arrival edge. Nanuk's IR is clean on this
   today (`Lookup`'s control flow is explicit in the op; `Dispatch` is a
   structured terminator). The warning is for the parked v0.x dispatch
   accelerator (transition table / PSEEK): when it un-parks, it must land
   as structured control — a new `Terminator` kind beside `Dispatch` —
   never as an op whose semantics depend on machine context. This
   addendum is the note the un-parking session should inherit.
3. **Provenance: spec one shape or don't add it.** Bril's source positions
   decayed into three incompatible shapes because the spec licensed it
   ("tools can't require positions … to follow any particular rules").
   Nanuk's provenance today is `debug_name` plus rendering order,
   explicitly semantic-weight-free — right discipline. If real source
   spans ever enter the IR (playground/book pressure), they enter as one
   proto message with required-or-absent semantics, schema-enforced;
   optional-and-unconstrained is how you get permanent inconsistency.
4. **A text form for the IR — trigger only.** Bril treats human-writable
   text as first-class and it earns its keep pedagogically, but maintains
   two independent, disagreeing grammars and never tests the round-trip.
   Nanuk renders IR (playground) and deliberately cannot parse it; the
   eDSL is the authoring surface. Trigger: book exercises at the IR level
   ("write the IR by hand"). If that comes, build one grammar with
   round-trip tests — the test Bril never wrote.
5. **The honest mirror.** At the IR level, *Nanuk* is the
   interpreter-as-spec project: Sail specs the ISA, but IR semantics are
   "what the lowering produces," with the interpreters mirrored to it —
   two implementations and a tripwire (better than Bril's one), yet the
   only prose statement of IR semantics is the proto file's comment
   conventions. The cheap fix is prose, not machinery: the
   normative-idiom pass (**Tier B item 3**) pays most in the proto
   comments / the book's IR chapter — per op family, state what
   validation rejects vs. what errors at runtime vs. what is total.

Not imported, with reasons: the flat stringly instruction record (its
generic-tooling win is what caused the twelve-schema drift); the
`AbstractProgram` stringly escape hatch (Nanuk's IR is closed by design —
validators rejecting the other engine's terminators is a feature); the
Constant/Value/Effect taxonomy (shaped for a CFG-of-functions IR,
meaningless for state machines). Convergence for the record: Bril's
materialize-constants regularity is the instinct that surfaced `Movi` as a
first-class value in the SIIT arc — Nanuk got there from demonstrated
need, the better direction.

## 8. Sources

- Clone: `third_party/bril` @ `e8b05b7` (disposable; re-clone to verify).
- Blog post: cs.cornell.edu/~asampson/blog/bril.html (2024-07-26).
- Course: cs.cornell.edu/courses/cs6120/2025fa/ (syllabus → project pipeline).
- Berkeley CS 265 (mwillsey/bril fork) — the one confirmed external course.
- Key in-repo evidence: `test/interp/turnt.toml` (five envs, one corpus);
  `brilirs/Makefile` (glob opt-in); `benchmarks/turnt.toml` (`.prof`
  cross-impl); `docs/lang/core.md:5` (the one-sentence conformance model);
  `fastbril/doc/main.tex` + `fastbril/config/*.cf` (BRB single-truth
  codegen); `bril-ts/types.ts` vs `brili.ts` (bitcast gap).
