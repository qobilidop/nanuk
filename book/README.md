# The Nanuk book

This directory holds the source of *Nanuk: A Packet Processor from Chip to
Language* — the long-form narrative that walks the whole project, from the
formal ISA spec down to the RTL core and back up to the programming language.
It is the destination the `docs/notes/` lab notes were always writing toward.

## Status

**Draft.** Part I (The Spec) and Part II (The Machine) are substantive drafts;
Parts III–IV and the appendix are being written. Chapter titles carrying a
*(draft)* marker are stubs awaiting their pass. Nothing here is final prose.

## Building

The book is [mdBook](https://rust-lang.github.io/mdBook/). From the repo root:

```bash
mdbook serve book      # live-reload preview at http://localhost:3000
mdbook build book      # render to book/build (the deploy artifact)
```

CI pins mdBook and renders the same `mdbook build book`, then composes
`book/build` into the published site under `/nanuk/book/` alongside the
playground at `/nanuk/play/`. See `.github/workflows/pages.yml`.

## Why mdBook, and why markdown-pure

We chose mdBook because the source stays **plain CommonMark** — no custom macro
layer, no framework lock-in. Every chapter is a `.md` file a person can read
raw. That keeps the book refactorable: if a later toolchain (a static-site
generator, a print pipeline, a different renderer) wins, the content ports with
a find-and-replace on the front matter, not a rewrite. We pin the mdBook binary
rather than track latest so a render in two years matches a render today —
the same determinism doctrine the project applies to its emulators.

## License

The book inherits **CC-BY-4.0**, following the carve-out in
`docs/development.md`: repository *code* is Apache-2.0, but `docs/notes/`
content — the raw material this book is built from — is CC-BY-4.0, and the
book inherits that license.
