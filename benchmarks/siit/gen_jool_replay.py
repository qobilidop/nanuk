"""Generates the committed Jool graybox replay report
(benchmarks/siit/jool-replay.md) -- SIIT leg 4, the independent-
interpretation oracle. See benchmarks/siit/README.md for the four-leg test
architecture and benchmarks/siit/audit.md for the RFC 7915 audit whose stable
disposition IDs the replay classification cites.

Run (from the repo root, in the devcontainer), with the pinned Jool clone
present (benchmarks/siit/fetch_jool.sh) and the gate set:

    cd sw/python && NANUK_JOOL=1 uv run --no-sync \
        python ../../benchmarks/siit/gen_jool_replay.py

Deterministic by construction: every classification comes from replaying a
fixed `.pkt` pair through the reference `translate()` and inspecting the
result -- no randomness, no timestamps. `test_jool_replay.py` is the drift
tripwire: it regenerates the report in memory and asserts the committed
counts match, and asserts the closure property (every cited audit id exists;
no `pass` with mismatching bytes; no unclassified outcome).

**Zero GPL bytes committed:** the report reproduces only fixture names, byte
offsets, and our own prose. Fixture `.pkt` bytes are read from the gitignored
clone at run time and never written here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from nanuk.testkit import jool_graybox as jg
from nanuk.testkit.jool_replay import replay_all, write_report

_OUT = Path(__file__).resolve().parent / "jool-replay.md"


def main() -> int:
    root = jg.jool_root()
    if root is None:
        print(
            "Jool clone absent -- run benchmarks/siit/fetch_jool.sh and set "
            "NANUK_JOOL=1 before regenerating.",
            file=sys.stderr,
        )
        return 1
    report = replay_all(root)
    write_report(report, _OUT)
    unclassified = [r.fixture for r in report.results if r.kind == "unclassified"]
    if unclassified:
        print(f"UNCLASSIFIED fixtures (investigate, do not commit): {unclassified}", file=sys.stderr)
        return 2
    print(f"Wrote {_OUT} ({len(report.results)} fixtures).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
