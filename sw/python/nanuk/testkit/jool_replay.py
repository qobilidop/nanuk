"""Jool graybox replay -- the independent-interpretation oracle for the SIIT
demo (Plan B, task B2).

Legs 1-3 (RFC audit, reference translator, in-house differential replay) are
all authored from one reading of RFC 7915. This leg is the only one that can
catch a *shared* misreading, because it replays a completely independent
implementation's fixtures (Jool's SIIT graybox suite) through our reference
translator and classifies every difference.

Nothing here embeds Jool's fixture bytes: `.pkt` files are read from the
gitignored `third_party/jool` clone at test time only (see
`benchmarks/siit/fetch_jool.sh`). The manifest and config come from the
suite's own scripts via `jool_graybox` (parsed, not guessed).

Classification taxonomy (frozen in the plan). Each fixture ends as exactly
one of:

  pass            -- our reference output equals Jool's expected bytes under
                     Jool's own byte-exception mask (their `IDENTIFICATION=..`
                     lists), plus our Ethernet header stripped at the boundary.
  divergence(id)  -- both translators send, but the bytes differ ONLY in a
                     way attributable to a named, documented policy decision;
                     `id` is an audit row and the comparison mask is DERIVED
                     from that policy (never a per-fixture fudge).
  out_of_scope(id)-- the fixture exercises a capability Nanuk defers or
                     refuses (fragmentation, ICMP-error translation/generation,
                     extension headers, forward-all, hairpinning); `id` is the
                     audit row that defers/refuses it.
  drop_agrees     -- Jool expects no output and we also drop. (None arise: the
                     graybox framework only ever asserts expected *arrivals*.)

A divergence is a FINDING, not a failure. The replay test fails only on a
harness error, a `pass` whose bytes actually mismatch, or an UNCLASSIFIED
outcome (which would mean the oracle found something we have not dispositioned
-- exactly the STOP-and-report signal this leg exists to raise).

Ethernet boundary: graybox `.pkt` files are raw L3. We wrap the sender in our
framing (testkit MACs, EtherType by IP version) and strip the 14 B Ethernet
header from our output before comparing against the raw-L3 expected file.

Byte-exception semantics: Jool's kernel graybox compares expected-vs-actual
two ways and requires both to agree; its `old_algorithm` (test/graybox/mod/
expecter.c) is a plain byte-by-byte compare over the L3 packet that *skips the
excepted offsets* and requires equal length. That is exactly what we
implement here (offsets are relative to the L3 packet, as in their tooling).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from nanuk.testkit.jool_graybox import (
    _SUITE,
    Fixture,
    _lock_sha,
    jool_config,
    load_manifest,
)
from nanuk.testkit.siit_ref import SiitConfig, translate

# Testkit framing MACs. Irrelevant to L3 translation (they pass through and are
# stripped before comparison), but fixed for determinism.
_MAC_DST = bytes.fromhex("aabbccddee01")
_MAC_SRC = bytes.fromhex("aabbccddee02")
_ET_IPV4 = 0x0800
_ET_IPV6 = 0x86DD

# --- audit-row citations ----------------------------------------------------
# Every id below is a stable row in benchmarks/siit/audit.md. The replay
# closure test asserts each cited id is actually present there.

# out_of_scope -- deferred/refused capabilities Jool exercises but Nanuk does not
_FRAG_46 = "7915-4.1-frag"  # v4->v6 fragment: we drop, Jool adds a Fragment Header
_FRAG_64 = "7915-5.1.1-fragment"  # v6->v4 fragment: we drop, Jool derives the v4 frag fields
_FRAG_EMIT = "7915-4.1-df0-fragment"  # translated packet exceeds the min MTU; Jool fragments on egress
_ICMPERR_XLATE_46 = "7915-4.3-inner-xlate"  # v4 ICMP error in: inner-packet translation deferred
_ICMPERR_XLATE_64 = "7915-5.3-inner-xlate"  # v6 ICMP error in: same, other direction
_ICMPERR_GEN_46 = "7915-4.4-generate"  # v4 in, unprocessable: Jool ORIGINATES an ICMPv4 error
_ICMPERR_GEN_64 = "7915-5.4-generate"  # v6 in, unprocessable: Jool ORIGINATES an ICMPv6 error
_EXTHDR = "7915-5.1-exthdr"  # IPv6 extension header (hop-by-hop/routing/dest-opt/no-next): traversal deferred
_FORWARD_46 = "7915-4.5-forward-all"  # v4 unknown transport (e.g. DCCP): we drop, Jool forwards
_FORWARD_64 = "7915-5.5-forward-all"  # v6 unknown transport: same, other direction
_HAIRPIN = "7757-hairpin"  # RFC 7757 §4 hairpinning: a single-pass translator cannot loop the packet back

# divergence -- both send, documented sovereign policy difference
_DF_DIV = "7915-5.1-df"  # we always set DF=1 (RFC 8021 atomic-fragment safety); Jool sets DF=0 for small packets

# IPv6 next-header values that are extension headers (not upper-layer L4). NH
# 44 (Fragment) is handled by the `fragment` drop before we get here.
_EXT_HEADERS = frozenset({0, 43, 60, 59})  # hop-by-hop, routing, destination-options, no-next-header

# ICMP error types (as opposed to echo request/reply, which we DO translate).
_ICMP4_ERROR_TYPES = frozenset({3, 4, 5, 11, 12})  # dest-unreach, quench, redirect, time-exceeded, param-problem
_ICMP6_ERROR_TYPES = frozenset({1, 2, 3, 4})  # dest-unreach, packet-too-big, time-exceeded, param-problem

# The DF-divergence's DERIVED mask: the IPv4 flags byte carrying DF (offset 6)
# and the IPv4 header-checksum bytes (offsets 10-11), which any DF change
# necessarily perturbs. Grounded in the named policy (7915-5.1-df), not fitted
# per fixture.
_DF_MASK = frozenset({6, 10, 11})
_DF_BIT = 0x40  # the Don't-Fragment flag within IPv4 header byte 6


@dataclass(frozen=True)
class Result:
    fixture: str
    group: str
    direction: str
    kind: str  # "pass" | "divergence" | "out_of_scope" | "drop_agrees" | "unclassified"
    audit_id: str  # "" for pass/drop_agrees; the cited row otherwise
    detail: str  # one-line human-readable story (no fixture bytes)


@dataclass
class Report:
    sha: str  # the pinned Jool SHA these fixtures came from
    results: list[Result] = field(default_factory=list)


def _wrap(l3: bytes) -> bytes:
    et = _ET_IPV4 if (l3[0] >> 4) == 4 else _ET_IPV6
    return _MAC_DST + _MAC_SRC + struct.pack("!H", et) + l3


def _masked_diff(ours: bytes, expected: bytes, mask: frozenset[int]) -> list[int] | None:
    """Jool's `old_algorithm`: equal length required (None on mismatch), then
    a byte-by-byte compare over the L3 packet skipping the masked offsets.
    Returns the list of differing offsets ([] means equal-under-mask)."""
    if len(ours) != len(expected):
        return None
    return [i for i in range(len(ours)) if i not in mask and ours[i] != expected[i]]


def _icmp_type(l3: bytes) -> int | None:
    v = l3[0] >> 4
    if v == 4 and l3[9] == 1:
        ihl = (l3[0] & 0x0F) * 4
        return l3[ihl] if len(l3) > ihl else None
    if v == 6 and l3[6] == 58:
        return l3[40] if len(l3) > 40 else None
    return None


def _is_icmp_error(l3: bytes) -> bool:
    t = _icmp_type(l3)
    if t is None:
        return False
    return t in (_ICMP4_ERROR_TYPES if (l3[0] >> 4) == 4 else _ICMP6_ERROR_TYPES)


def classify(fixture: Fixture, cfg: SiitConfig, suite_root: Path) -> Result:
    """Replay one fixture through the reference translator and classify the
    outcome per the frozen taxonomy. Never per-fixture special-casing: the
    verdict is derived from the reference's own result and the packet's own
    structure (protocol, next-header, ICMP type), so the same rule that
    classifies one fragment classifies them all."""
    sender = (suite_root / fixture.sender).read_bytes()
    expected = (suite_root / fixture.expected).read_bytes() if fixture.expected else None
    d = fixture.direction

    def result(kind: str, audit_id: str, detail: str) -> Result:
        return Result(fixture.name, fixture.group, d, kind, audit_id, detail)

    # -- In-family (66/44): Jool receives one family and emits the same family.
    # A single-pass translator cannot: either Jool ORIGINATED an ICMP error in
    # response (which we refuse to generate), or it HAIRPINNED the packet
    # (translate -> EAMT dst loops back -> translate again), which we do not do.
    if d in ("66", "44"):
        if expected is not None and _is_icmp_error(expected):
            gen = _ICMPERR_GEN_64 if d == "66" else _ICMPERR_GEN_46
            fam = "ICMPv6" if d == "66" else "ICMPv4"
            return result(
                "out_of_scope",
                gen,
                f"Jool originates an {fam} error back to the sender; Nanuk refuses ICMP-error generation (silent drop).",
            )
        return result(
            "out_of_scope",
            _HAIRPIN,
            "same-family in/out via RFC 7757 hairpinning; Nanuk's single-pass translator does not loop the packet back.",
        )

    # -- Cross-family (46/64): run the translator.
    r = translate(_wrap(sender), cfg)

    if r.verdict == "sent":
        assert r.frame is not None
        ours = r.frame[14:]  # strip our Ethernet header
        assert expected is not None
        diff = _masked_diff(ours, expected, frozenset(fixture.exceptions))
        if diff == []:
            return result("pass", "", "output matches Jool's expected bytes under their mask.")
        if diff is None:
            # We sent one datagram; Jool's expectation is a different length.
            # The split-expectation (`#`-suffixed) fixtures are Jool's egress
            # fragments of a packet that exceeds the minimum IPv6 MTU -- we do
            # not fragment, so neither fragment can match.
            if "#" in fixture.name:
                return result(
                    "out_of_scope",
                    _FRAG_EMIT,
                    "translated packet exceeds the minimum IPv6 MTU; Jool fragments on egress into this piece, Nanuk emits one unfragmented datagram.",
                )
            return result(
                "unclassified",
                "",
                f"SENT but length differs (ours={len(ours)}, expected={len(expected)}) with no fragmentation signal -- INVESTIGATE.",
            )
        # Non-empty diff under Jool's mask. Is it ONLY the always-DF=1 policy?
        residual = _masked_diff(ours, expected, frozenset(fixture.exceptions) | _DF_MASK)
        if (
            d == "64"
            and residual == []
            and set(diff) <= _DF_MASK
            and (ours[6] ^ expected[6]) == _DF_BIT
        ):
            return result(
                "divergence",
                _DF_DIV,
                "IPv4 DF: Nanuk always sets DF=1 (RFC 8021 atomic-fragment safety); Jool clears DF on this sub-MTU packet. "
                "Bytes agree under the derived mask {DF flag + IPv4 header checksum}.",
            )
        return result(
            "unclassified",
            "",
            f"SENT, differs at L3 offsets {diff} beyond Jool's mask -- unexplained by any documented policy; INVESTIGATE.",
        )

    # -- Our reference dropped. Map the ledger reason to the capability Jool
    # exercised that we defer/refuse.
    why = r.why
    if why == "fragment":
        return result(
            "out_of_scope",
            _FRAG_64 if d == "64" else _FRAG_46,
            "fragment (or atomic-fragment header): Jool translates the fragment fields; Nanuk defers fragmentation (drop).",
        )
    if why == "icmp_error":
        return result(
            "out_of_scope",
            _ICMPERR_XLATE_64 if d == "64" else _ICMPERR_XLATE_46,
            "ICMP error message: Jool translates the embedded packet-in-error; Nanuk defers ICMP-error translation (drop).",
        )
    if why == "unsupported_l4":
        nexthdr = sender[6] if (sender[0] >> 4) == 6 else sender[9]
        if d == "64" and nexthdr in _EXT_HEADERS:
            return result(
                "out_of_scope",
                _EXTHDR,
                f"IPv6 extension header (next-header {nexthdr}): Jool skips it and translates the payload; Nanuk defers extension-header traversal (drop).",
            )
        return result(
            "out_of_scope",
            _FORWARD_46 if d == "46" else _FORWARD_64,
            "unknown transport protocol: Jool forwards it (RFC 7915 MUST-forward); Nanuk drops per the rewrite-only totality doctrine (documented divergence).",
        )
    return result(
        "unclassified",
        "",
        f"dropped with unexpected reason {why!r} against a Jool expected output -- INVESTIGATE.",
    )


def replay(fixture: Fixture, cfg: SiitConfig, suite_root: Path) -> Result:
    """Classify a single fixture. Thin alias over `classify` for the plan's
    named interface."""
    return classify(fixture, cfg, suite_root)


def replay_all(root: Path) -> Report:
    """Replay every manifest fixture under the Jool clone `root`, in manifest
    (deterministic) order, against the config mirrored from `setup-jool.sh`."""
    suite_root = root / _SUITE
    cfg = jool_config(root)
    report = Report(sha=_lock_sha())
    for fixture in load_manifest(root):
        report.results.append(classify(fixture, cfg, suite_root))
    return report


# --- report generation ------------------------------------------------------

_CLASS_ORDER = ["pass", "divergence", "out_of_scope", "drop_agrees", "unclassified"]

# Human-readable one-liners per audit id, for the report's cross-reference
# table. Prose only -- no fixture bytes.
_ID_STORY = {
    _DF_DIV: "always DF=1 on v6->v4 (RFC 8021 atomic-fragment safety) vs Jool's size-conditional DF=0",
    _FRAG_46: "v4->v6 fragment translation deferred (drop)",
    _FRAG_64: "v6->v4 fragment translation deferred (drop)",
    _FRAG_EMIT: "egress fragmentation of an over-MTU translated packet deferred (one datagram, unfragmented)",
    _ICMPERR_XLATE_46: "v4 ICMP-error (inner packet) translation deferred (drop)",
    _ICMPERR_XLATE_64: "v6 ICMP-error (inner packet) translation deferred (drop)",
    _ICMPERR_GEN_46: "ICMPv4-error generation refused (Nanuk never originates a packet)",
    _ICMPERR_GEN_64: "ICMPv6-error generation refused (Nanuk never originates a packet)",
    _EXTHDR: "IPv6 extension-header traversal deferred (drop)",
    _FORWARD_46: "v4 unknown-transport forward-all refused; drop (totality doctrine)",
    _FORWARD_64: "v6 unknown-transport forward-all refused; drop (totality doctrine)",
    _HAIRPIN: "RFC 7757 hairpinning deferred (single-pass translator)",
}


def _counts_by_kind(report: Report) -> dict[str, int]:
    counts = dict.fromkeys(_CLASS_ORDER, 0)
    for r in report.results:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    return counts


def _counts_by_id(report: Report) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in report.results:
        if r.audit_id:
            counts[r.audit_id] = counts.get(r.audit_id, 0) + 1
    return counts


def render_report(report: Report) -> str:
    """Render `jool-replay.md` deterministically: counts by classification,
    the divergence/out-of-scope cross-reference, and the fixture-name lists
    per class. Names and prose only -- no fixture bytes, ever."""
    kinds = _counts_by_kind(report)
    ids = _counts_by_id(report)
    total = len(report.results)

    L: list[str] = []
    L.append("# SIIT leg 4 -- Jool graybox replay results")
    L.append("")
    L.append(
        "Generated by `nanuk.testkit.jool_replay.replay_all` over the pinned Jool "
        "SIIT graybox suite. **This file is committed but generated** -- regenerate "
        "with `benchmarks/siit/gen_jool_replay.py` (needs `NANUK_JOOL=1` + the "
        "clone); a replay test tripwires the committed counts against a fresh run."
    )
    L.append("")
    L.append(f"- **Pinned Jool SHA:** `{report.sha}`")
    L.append(f"- **Fixtures replayed:** {total}")
    L.append("")
    L.append(
        "Every `.pkt` is read from the gitignored clone at run time; nothing below "
        "reproduces fixture bytes -- only fixture *names*, byte *offsets*, and our "
        "own prose describing differences (**zero GPL bytes committed**)."
    )
    L.append("")
    L.append(
        "The Nanuk **program** (hand asm + twins) implements RFC 6052 /96 + "
        "EAMT-exact only; these replay claims are at the **reference** level, where "
        "RFC 6052 all-six-prefix-lengths and RFC 7757 prefix EAMT are implemented, "
        "so the reference expresses Jool's actual /40 pool6 + /24<->/120 EAMT "
        "config. See [`README.md`](README.md) leg 4 for the program-vs-reference "
        "scope split."
    )
    L.append("")
    L.append(
        "Jool's own graybox suite has no `tcp46` group (no v4->v6 TCP fixtures at "
        "all), so this replay contributes zero oracle coverage for that direction "
        "-- our own committed `tcp46` vectors (`benchmarks/siit/vectors/tcp46.json`) "
        "are the only TCP v4->v6 coverage this repo has, in-house rather than "
        "cross-checked against Jool."
    )
    L.append("")

    L.append("## Counts by classification")
    L.append("")
    L.append("| classification | count |")
    L.append("|---|---:|")
    for k in _CLASS_ORDER:
        L.append(f"| {k} | {kinds[k]} |")
    L.append(f"| **total** | **{total}** |")
    L.append("")

    L.append("## Divergences and out-of-scope, by audit id")
    L.append("")
    L.append(
        "Each id is a stable row in [`audit.md`](audit.md). `divergence` rows are "
        "differences under a documented sovereign policy (both translators send; "
        "the comparison mask is derived from the policy). `out_of_scope` rows are "
        "capabilities Nanuk defers or refuses that Jool exercises."
    )
    L.append("")
    L.append("| kind | audit id | count | what differs |")
    L.append("|---|---|---:|---|")
    # divergence rows first, then out_of_scope, each alphabetical by id
    div_ids = sorted(i for i in ids if any(r.audit_id == i and r.kind == "divergence" for r in report.results))
    oos_ids = sorted(i for i in ids if any(r.audit_id == i and r.kind == "out_of_scope" for r in report.results))
    for i in div_ids:
        L.append(f"| divergence | `{i}` | {ids[i]} | {_ID_STORY.get(i, '')} |")
    for i in oos_ids:
        L.append(f"| out_of_scope | `{i}` | {ids[i]} | {_ID_STORY.get(i, '')} |")
    L.append("")

    L.append("## Fixtures per classification")
    L.append("")
    for k in _CLASS_ORDER:
        names = [r.fixture for r in report.results if r.kind == k]
        if not names:
            continue
        if k in ("divergence", "out_of_scope"):
            L.append(f"### {k} ({len(names)})")
            L.append("")
            by_id: dict[str, list[str]] = {}
            for r in report.results:
                if r.kind == k:
                    by_id.setdefault(r.audit_id, []).append(r.fixture)
            for i in sorted(by_id):
                L.append(f"- **`{i}`** ({len(by_id[i])}): {', '.join(by_id[i])}")
            L.append("")
        else:
            L.append(f"### {k} ({len(names)})")
            L.append("")
            L.append(", ".join(names))
            L.append("")

    return "\n".join(L).rstrip() + "\n"


def write_report(report: Report, path: Path) -> None:
    """Write the rendered report to `path` deterministically."""
    path.write_text(render_report(report))
