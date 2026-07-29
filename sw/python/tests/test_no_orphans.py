"""No-orphans tripwires.

The Bril lesson (docs/notes/2026-07-29-bril-study.md): rot lives exactly
where the harness doesn't reach — doc pages missing from the book's
SUMMARY that silently never render, example programs no corpus exercises,
expected outputs whose inputs are gone. These tests make those states
fail loudly instead of accumulating.
"""

import re
from pathlib import Path

from nanuk.testkit.vectors import load_all

REPO_ROOT = Path(__file__).resolve().parents[3]

# Examples not (yet) covered by spec/vectors/, each with the corpus that
# does cover it. Extending the vectors over the benchmark ladder retires
# these entries.
VECTOR_EXCEPTIONS = {
    "calc": "MAP ladder E3 — tests/test_benchmarks_map.py",
    "icmp_echo": "MAP ladder E1 — tests/test_benchmarks_map.py",
    "mpls_sp": "PP ladder P5 — tests/test_benchmarks_pp.py",
    "overlay_dc": "PP ladder P6 — tests/test_benchmarks_pp.py",
    "srcroute": "MAP ladder T0/E5 — tests/test_benchmarks_map.py",
    "union": "PP ladder P7 — tests/test_benchmarks_pp.py",
    "siit": "RFC-audit vectors at benchmarks/siit/vectors — tests/test_siit_vectors.py",
}


def test_every_example_reached_by_a_vector_corpus():
    covered = set()
    for f in load_all():
        for rel in f.programs.values():
            covered.add(Path(rel).parts[1])  # examples/<name>/...
    example_dirs = {
        p.name for p in (REPO_ROOT / "examples").iterdir() if p.is_dir()
    }
    for name in sorted(example_dirs):
        assert name in covered or name in VECTOR_EXCEPTIONS, (
            f"examples/{name} is exercised by no conformance corpus: add vectors "
            f"in spec/vectors/ or an exception here naming its corpus"
        )
    stale = sorted(covered & set(VECTOR_EXCEPTIONS))
    assert not stale, f"now vector-covered, drop from VECTOR_EXCEPTIONS: {stale}"
    ghosts = sorted(covered - example_dirs)
    assert not ghosts, f"vectors reference deleted examples: {ghosts}"


def test_every_book_chapter_is_linked_from_summary():
    src = REPO_ROOT / "book" / "src"
    summary = (src / "SUMMARY.md").read_text()
    linked = set(re.findall(r"\]\(([^)]+\.md)\)", summary))
    for page in sorted(src.glob("*.md")):
        if page.name == "SUMMARY.md":
            continue
        assert page.name in linked, (
            f"book/src/{page.name} is not in SUMMARY.md — mdBook will never "
            f"render it (link it or delete it)"
        )
    missing = sorted(name for name in linked if not (src / name).exists())
    assert not missing, f"SUMMARY.md links to missing pages: {missing}"


def test_every_vector_file_is_nonempty_and_names_unique():
    for f in load_all():
        assert f.vectors, f"{f.path} contains no vectors"
        names = [v.name for v in f.vectors]
        assert len(names) == len(set(names)), f"duplicate vector names in {f.path}"
        for rel in f.programs.values():
            assert (REPO_ROOT / rel).exists(), f"{f.path} references missing {rel}"
