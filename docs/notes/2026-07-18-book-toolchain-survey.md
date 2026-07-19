# Book-toolchain survey — what the genre actually uses

Date: 2026-07-18. Three parallel primary-evidence surveys (language/compiler
books, systems/OS/hardware books, executable/interactive books) — every claim
below was verified against the work's actual repo config, build scripts, CI,
or page/PDF fingerprints, not folklore. Context: Nanuk's book shipped its
first draft on mdBook the same day; this survey audits that choice.

## Corrections to folklore (including our own)

- **blog_os is Zola, not mdBook** (`blog/config.toml`, CI downloads zola
  0.22.1). The mdBook claim in our stack rationale was wrong for blog_os;
  the true mdBook precedents in that world are the **Rust Embedded books**.
- **Crafting Interpreters is a custom Dart tool**, not a static-site
  framework: hand-rolled Markdown parser → one AST → HTML renderer + InDesign
  XML renderer; snippets are woven from the real annotated `.java`/`.c`
  sources and **compiled at every mid-chapter checkpoint** by CI.
- **Dive into Systems is Asciidoctor + Antora** (not PreTeXt).
  **littleosbook is Pandoc** (not Sphinx). **PLAI's book PDF is exported
  from Google Docs** (the site is Pollen) — per its own PDF metadata.

## The five clusters

1. **Custom weaver pipelines (the genre's masterpieces).** Crafting
   Interpreters (Dart; snippets compiled from real source), PBRT (homegrown
   noweb-descendant tangling fragments from the real pbrt-v4 renderer), Real
   World OCaml (OCaml over Pandoc; every code block *executed* via
   ocaml-mdx, output diffed), Eloquent JavaScript (custom Node; build-time
   output diffing AND an in-browser CodeMirror sandbox), the xv6 book
   (LaTeX whose file/line references are resolved against a fresh clone of
   the kernel so citations cannot drift). Defining property: **the book's
   code is the real code, and a machine checks it**.
2. **mdBook (the Rust world's default).** TRPL + Rustonomicon + async book +
   embedded books + rustc-dev-guide — all mdBook, no two configured alike.
   Verification ranges from none (rustc-dev-guide) to the survey's most
   rigorous: the Embedonomicon's CI compiles every stage, objdump/nm-diffs
   against committed goldens, and boots each binary under QEMU. TRPL's print
   path is five chained custom CLIs feeding No Starch — bespoke pain.
3. **Notebook/Sphinx frameworks (data-science default, heavy).** Fuzzing
   Book (fully bespoke Make pipeline over raw notebooks, four selectable
   backends), d2l.ai (Sphinx + d2lbook, per-framework build execution),
   QuantEcon (classic Jupyter Book v1; Thebe off by default), Think Python
   3e (Jupyter Book with `execute_notebooks: off` — even Downey pre-bakes),
   The Turing Way + Project Pythia (migrated to the new mystmd engine —
   the JB1→JB2 migration churn is real and ongoing).
4. **Quarto (academic multi-format).** Harvard's MLSysBook: multi-volume,
   HTML+PDF+EPUB, six-plus config files and custom filter extensions —
   powerful, config-heavy. Quarto 1.9 added a native Typst book backend
   (no mature independent Typst book literature exists yet).
5. **Zero-toolchain.** mal (`process/guide.md`, GitHub as CMS), os-tutorial
   (24 README folders), Ray Tracing in One Weekend (Markdeep single-file),
   OSTEP (hand HTML + private LaTeX). Massive readership, no machinery.

Curiosities: Linux From Scratch's DocBook is *executable* — jhalfs parses
the book's `<screen>` blocks into the actual build scripts, so the book IS
the program. Nand2Tetris runs on Wix.

## In-browser execution (our playground-embed bet)

The genre's live-execution successes are client-side: Eloquent JavaScript's
sandbox, The Book of Shaders (glslCanvas, real-time WebGL), futurecoder
(pure Pyodide in a Web Worker, zero backend), TU Delft's TeachBooks
(Sphinx-Thebe rewired to a client-side Pyodide kernel). Meanwhile the
flagship *build-time* execution books keep turning execution off or
outsourcing it (Colab/Binder links). Nanuk already ships Pyodide + the
locked iframe/deep-link contract — the book embedding the playground is the
futurecoder/Book-of-Shaders pattern, not a compromise.

## What this changes for Nanuk's book

The genre's lesson is not "use tool X" — the masterpieces share a
**property, not a framework**: prose woven from real, mechanically-verified
code. That property has been achieved on custom Dart, Pandoc+mdx, mdBook+CI,
and LaTeX+regex alike. Nanuk already lives by mirror-with-tripwire; the book
should inherit it in stages:

1. **Now (cheap, mdBook-native):** adopt `{{#include path:anchor}}` from the
   real `examples/` and `sw/` sources instead of pasted code blocks, as
   chapters get revised. The included code is already tested by repo CI —
   that is the Embedonomicon pattern with zero new tooling.
2. **Later (if drift bites prose that can't be `#include`d):** snippet
   tripwire tests in `sw/python/tests` asserting quoted blocks match source
   — the xv6 pattern.
3. **Only if we ever want mid-chapter program-state weaving** (Nystrom's
   checkpoint compiles): a custom mdBook preprocessor — the one place the
   genre says bespoke tooling earns its keep.

Print path, if ever wanted: every surveyed print pipeline is bespoke pain
(TRPL's 5 CLIs, Nystrom's InDesign JS scripting, RWO's in-repo LaTeX).
Quarto remains the least-pain migration for a PDF edition; the
markdown-pure source keeps that door open, which was the point.

## Compact reference table

| Work | Stack | Code-verification | Print |
|---|---|---|---|
| Crafting Interpreters | custom Dart, one AST → HTML + InDesign XML | snippets woven from real source; every checkpoint compiled | self-pub (Genever Benning), InDesign |
| Game Programming Patterns | one Python script | none (weave only) | same, cruder ancestor |
| TRPL / Rust book family | mdBook (×5, all configured differently) | `mdbook test` + custom trpl crate … down to none | TRPL: 5 chained CLIs → No Starch |
| Rust Embedded / Embedonomicon | mdBook | CI compiles, objdump-diffs, QEMU-boots every stage | none |
| Real World OCaml | custom OCaml over Pandoc | ocaml-mdx executes every block, diffs output | Cambridge UP, same source |
| Eloquent JavaScript | custom Node | build-time output diffing + in-browser sandbox | No Starch via render_latex.mjs |
| blog_os | Zola | per-chapter branch CI, QEMU boot tests, nightly | none |
| xv6 book | LaTeX (riscv) / troff (x86) | line-refs resolved against fresh kernel clone | PDF is the artifact |
| PBRT | homegrown literate programming | fragments tangled from the real renderer | MIT Press |
| OSTEP | hand HTML + private LaTeX | none | CreateSpace |
| Linux From Scratch | DocBook XML | jhalfs executes the book | PDF + HTML |
| Dive into Systems | Asciidoctor + Antora | none observed | No Starch |
| Ray Tracing in One Weekend | Markdeep | reader-driven (CMake) | browser print-to-PDF |
| RISC-V ISA manual | AsciiDoctor (+ antora layout) | n/a (spec) | asciidoctor-pdf |
| Fuzzing/Debugging Book | bespoke Make over notebooks | build-time kernel execution | LaTeX from notebooks |
| d2l.ai | Sphinx + d2lbook | per-framework build execution | xelatex |
| QuantEcon | Jupyter Book v1 | cached execution; Thebe off | LaTeX target |
| Think Python 3e | Jupyter Book v1, execution OFF | pre-baked; readers use Colab | book.tex |
| Turing Way / Project Pythia | mystmd (JB2 engine, migrated) | n/a / notebooks | MyST export |
| Software Foundations | coqdoc + Make | proofs machine-checked at build | coqdoc→pdflatex |
| Book of Shaders | glslCanvas/glslEditor | live in-browser WebGL | static print |
| futurecoder | Pyodide in a Web Worker | client-side checking | n/a |
| MLSysBook | Quarto (multi-volume, 6+ configs) | standard render | PDF+EPUB+HTML |
| mal / os-tutorial | none (GitHub markdown) | none | none |
| Writing an Interpreter in Go / Writing a C Compiler | closed prose (ebook/No Starch) | reader-driven external test harness | POD / publisher |

Full per-work evidence (config files, URLs) lives in the session's survey
transcripts; the load-bearing citations are the config files named above.
