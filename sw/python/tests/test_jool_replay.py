"""Jool graybox replay -- closure + tripwire tests (SIIT demo, Plan B, task
B2). Gated on `NANUK_JOOL=1` and the pinned clone (see
`jool_graybox.requires_jool`); CI does not run these (no network in the plan).

A divergence is a FINDING, not a failure -- so these tests do NOT fail on a
fixture ending `divergence(...)` or `out_of_scope(...)`. They fail only on the
things that would mean the oracle was mishandled:

  * a `pass` whose bytes do not actually match under Jool's mask,
  * a `divergence`/`out_of_scope` citing an audit id absent from audit.md
    (the closure property),
  * any `unclassified` outcome (the STOP-and-report signal), and
  * the committed jool-replay.md drifting from a fresh regeneration (the
    tripwire, like the vector regen test).
"""

from __future__ import annotations

import re

import pytest

from nanuk.testkit import jool_graybox as jg
from nanuk.testkit import jool_replay as jr

_skip, _reason = jg.requires_jool()
pytestmark = pytest.mark.skipif(_skip, reason=_reason)

_REPO_ROOT = jg._REPO_ROOT
_AUDIT = _REPO_ROOT / "benchmarks" / "siit" / "audit.md"
_REPORT = _REPO_ROOT / "benchmarks" / "siit" / "jool-replay.md"

# Pinned expected classification counts (read off the committed report; these
# are the leg-4 headline numbers and a second guard alongside the byte-exact
# regen tripwire below).
EXPECTED_KIND_COUNTS = {
    "pass": 22,
    "divergence": 2,
    "out_of_scope": 100,
    "drop_agrees": 0,
    "unclassified": 0,
}
EXPECTED_TOTAL = 124


def _report() -> jr.Report:
    root = jg.jool_root()
    assert root is not None
    return jr.replay_all(root)


def test_total_fixtures() -> None:
    assert len(_report().results) == EXPECTED_TOTAL


def test_no_unclassified() -> None:
    """The STOP signal: an unclassified outcome means the oracle found
    something we have not dispositioned. There must be none."""
    unclassified = [r.fixture for r in _report().results if r.kind == "unclassified"]
    assert unclassified == [], f"unclassified fixtures need adjudication: {unclassified}"


def test_kind_counts_match_pinned() -> None:
    report = _report()
    counts = dict.fromkeys(EXPECTED_KIND_COUNTS, 0)
    for r in report.results:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    assert counts == EXPECTED_KIND_COUNTS


def test_pass_bytes_actually_match_under_mask() -> None:
    """No `pass` may hide a byte mismatch. Independently re-translate every
    `pass` fixture and re-run Jool's masked comparison; the diff must be
    empty (not None -- lengths must match too)."""
    root = jg.jool_root()
    suite = root / jg._SUITE
    cfg = jg.jool_config(root)
    fixtures = {f.name: f for f in jg.load_manifest(root)}
    for r in _report().results:
        if r.kind != "pass":
            continue
        f = fixtures[r.fixture]
        sender = (suite / f.sender).read_bytes()
        expected = (suite / f.expected).read_bytes()
        out = jr.translate(jr._wrap(sender), cfg)
        assert out.verdict == "sent", f"{r.fixture}: pass but reference did not send"
        diff = jr._masked_diff(out.frame[14:], expected, frozenset(f.exceptions))
        assert diff == [], f"{r.fixture}: classified pass but bytes differ at {diff}"


def test_closure_every_cited_audit_id_exists() -> None:
    """Closure property: every audit id a fixture cites (divergence OR
    out_of_scope) must be a real row in audit.md."""
    audit = _AUDIT.read_text()
    cited = {r.audit_id for r in _report().results if r.audit_id}
    missing = sorted(i for i in cited if f"`{i}`" not in audit)
    assert missing == [], f"cited audit ids absent from audit.md: {missing}"


def test_no_divergence_id_absent_from_audit() -> None:
    """The plan's explicit closure clause, stated on its own: no fixture may
    end divergence(<id>) with an id absent from audit.md."""
    audit = _AUDIT.read_text()
    for r in _report().results:
        if r.kind == "divergence":
            assert f"`{r.audit_id}`" in audit, f"{r.fixture}: divergence id {r.audit_id} not in audit.md"


def test_committed_report_is_byte_identical_to_regen() -> None:
    """Tripwire (mirrors the vector regen test): the committed jool-replay.md
    must equal a fresh in-test regeneration exactly -- counts, cross-refs,
    fixture lists, all of it."""
    assert _REPORT.is_file(), "benchmarks/siit/jool-replay.md is not committed"
    assert jr.render_report(_report()) == _REPORT.read_text(), (
        "jool-replay.md is stale -- regenerate with "
        "benchmarks/siit/gen_jool_replay.py (NANUK_JOOL=1 + clone)"
    )


def test_report_carries_no_fixture_bytes() -> None:
    """Zero-GPL-bytes guard: the committed report is names + prose + offsets
    only. Reject any long hex run that could be embedded packet bytes."""
    text = _REPORT.read_text()
    # SHA line aside, there must be no >=16-hex-digit blob (a .pkt fragment).
    body = "\n".join(ln for ln in text.splitlines() if "Pinned Jool SHA" not in ln)
    assert not re.search(r"\b[0-9a-fA-F]{16,}\b", body), "report may contain fixture bytes"
