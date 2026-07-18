"""Acquisition tests for the Jool graybox oracle (SIIT demo, Plan B, task
B1): manifest extraction from `siit/test.sh` and the pool6/EAMT config
mirror from `siit/setup-jool.sh`. Gated on `NANUK_JOOL=1` and the pinned
clone being present (see `jool_graybox.requires_jool`).

The expected fixture counts, groups, and config values below were read
directly off `third_party/jool`'s `test/graybox/test-suite/siit/{test.sh,
setup-jool.sh}` at the pinned SHA during implementation -- this test is a
tripwire against the parser silently drifting from what those scripts
actually say, not a guess.
"""

import socket
import subprocess
from pathlib import Path

import pytest

from nanuk.testkit import jool_graybox as jg

_skip, _reason = jg.requires_jool()
pytestmark = pytest.mark.skipif(_skip, reason=_reason)

# Counts by group, read off test.sh's own `if [ ... "$1" = "GROUP" ]` blocks
# at the pinned SHA. 8 groups total; "misc" (manual/-backed) is out of
# scope this plan and must NOT appear.
EXPECTED_GROUP_COUNTS = {
    "udp64": 10,
    "udp46": 10,
    "tcp64": 10,
    "icmpi64": 4,
    "icmpi46": 4,
    "icmpe64": 2,
    "icmpe46": 2,
    "rfc7915": 82,
}
EXPECTED_TOTAL = 124


def test_root_present() -> None:
    root = jg.jool_root()
    assert root is not None
    assert (root / jg._SUITE / "test.sh").is_file()


def test_manifest_group_counts() -> None:
    root = jg.jool_root()
    fixtures = jg.load_manifest(root)
    assert len(fixtures) == EXPECTED_TOTAL

    counts: dict[str, int] = {}
    for f in fixtures:
        counts[f.group] = counts.get(f.group, 0) + 1
    assert counts == EXPECTED_GROUP_COUNTS
    assert "misc" not in counts  # manual/ dir: out of scope this plan


def test_manifest_files_exist() -> None:
    root = jg.jool_root()
    suite_root = root / jg._SUITE
    fixtures = jg.load_manifest(root)
    assert fixtures  # sanity: parser actually found something
    for f in fixtures:
        assert (suite_root / f.sender).is_file(), f"{f.name}: sender {f.sender} missing"
        if f.expected is not None:
            assert (suite_root / f.expected).is_file(), f"{f.name}: expected {f.expected} missing"


def test_manifest_names_unique() -> None:
    root = jg.jool_root()
    fixtures = jg.load_manifest(root)
    names = [f.name for f in fixtures]
    assert len(names) == len(set(names))


def test_manifest_exceptions_are_int_tuples() -> None:
    root = jg.jool_root()
    fixtures = jg.load_manifest(root)
    for f in fixtures:
        assert isinstance(f.exceptions, tuple)
        assert all(isinstance(x, int) for x in f.exceptions)


def test_manifest_directions_known() -> None:
    root = jg.jool_root()
    fixtures = jg.load_manifest(root)
    assert {f.direction for f in fixtures} <= {"46", "64", "66", "44"}
    # The pktgen groups are strictly cross-family (46/64); only rfc7915
    # carries the in-family 66/44 pairs (Jool responding to itself).
    for f in fixtures:
        if f.group != "rfc7915":
            assert f.direction in ("46", "64")


def test_known_fixture_first_rfc7915_aa_pair() -> None:
    """`test46_11 7915 aat1 aae1` -- the first rfc7915 `aa` pair, no
    exceptions, direction 46 (sent client6ns -> client4ns per test46_11's
    definition, i.e. the v4->v6 direction)."""
    root = jg.jool_root()
    fixtures = {f.name: f for f in jg.load_manifest(root)}
    fx = fixtures["7915/aat1"]
    assert fx.group == "rfc7915"
    assert fx.direction == "46"
    assert fx.sender == Path("7915/aat1.pkt")
    assert fx.expected == Path("7915/aae1.pkt")
    assert fx.exceptions == ()
    suite_root = root / jg._SUITE
    assert (suite_root / fx.sender).is_file()
    assert (suite_root / fx.expected).is_file()


def test_known_fixture_split_expectation_pair() -> None:
    """`test46_12 7915 cct1 cce1 cce2 44,45,46,47` -- one sender, two
    expected fragments; our Fixture shape carries one `expected` each, so
    this becomes two manifest entries sharing the sender."""
    root = jg.jool_root()
    fixtures = {f.name: f for f in jg.load_manifest(root)}
    fx1 = fixtures["7915/cct1#1"]
    fx2 = fixtures["7915/cct1#2"]
    assert fx1.sender == fx2.sender == Path("7915/cct1.pkt")
    assert fx1.expected == Path("7915/cce1.pkt")
    assert fx2.expected == Path("7915/cce2.pkt")
    assert fx1.exceptions == fx2.exceptions == (44, 45, 46, 47)
    # The same sender/expected pair is invoked again later under a
    # different `lowest-ipv6-mtu` (the "ck" test) with no exceptions --
    # disambiguated rather than colliding.
    fx1b = fixtures["7915/cct1@2#1"]
    assert fx1b.exceptions == ()


def test_jool_config_pool6_and_eamt() -> None:
    """setup-jool.sh configures pool6 via `instance add --netfilter -6
    2001:db8:100::/40` and two EAMT PREFIX pairs via `eamt add`. As of B2
    the mirror is faithful: the /40 prefix length and the /24<->/120 EAMT
    prefixes are preserved exactly, and SiitConfig translates within them
    (RFC 6052 all six prefix lengths; RFC 7757 prefix EAMT with LPM)."""
    from nanuk.testkit.siit_ref import _addr46, _addr64

    root = jg.jool_root()
    cfg = jg.jool_config(root)

    assert cfg.pool6 == socket.inet_pton(socket.AF_INET6, "2001:db8:100::")[:12]
    assert cfg.pool6_len == 40

    assert cfg.eamt == (
        ("1.0.0.0/24", "2001:db8:3::/120"),
        ("10.0.0.0/24", "2001:db8:2::/120"),
    )
    # These are general PREFIX pairs, not exact host pairs, so they must NOT
    # populate the exact-host dicts the program table-plane consumes (the
    # program implements /96 + EAMT-exact only -- the scope split).
    assert cfg.eamt46 == {}
    assert cfg.eamt64 == {}

    # But the reference oracle translates within the ranges via LPM, both
    # ways: 1.0.0.96 <-> 2001:db8:3::60, and the second entry's range too.
    assert _addr46(socket.inet_aton("1.0.0.96"), cfg) == socket.inet_pton(
        socket.AF_INET6, "2001:db8:3::60"
    )
    assert _addr64(
        socket.inet_pton(socket.AF_INET6, "2001:db8:2::7"), cfg
    ) == socket.inet_aton("10.0.0.7")
    # A /40 pool6 address (outside both EAMT ranges) round-trips via RFC 6052.
    assert _addr64(
        socket.inet_pton(socket.AF_INET6, "2001:db8:1c6:3364:2::"), cfg
    ) == socket.inet_aton("198.51.100.2")


def test_parse_completeness_guard_fires_on_unrecognized_call() -> None:
    """B1 review carry-forward: an invocation-looking line inside an
    in-scope group block that the parser cannot turn into fixtures must
    raise, not be silently dropped. Corrupt a copy of the real test.sh by
    injecting an unknown helper variant into the rfc7915 block and prove the
    guard fires."""
    root = jg.jool_root()
    text = (root / jg._SUITE / "test.sh").read_text()
    # Sanity: the clean text parses without raising.
    assert jg._parse_test_sh(text)

    # Inject a bogus, unparseable invocation right after a known in-scope
    # call. `test64_frobnicate ...` matches the broad invocation pattern but
    # not _CALL_RE (no auto/11/12 suffix), so it must trip the guard.
    marker = "test46_11 7915 aat1 aae1"
    assert marker in text
    corrupted = text.replace(marker, marker + "\n\ttest64_frobnicate 7915 xxt1 xxe1", 1)
    with pytest.raises(ValueError, match="parse is incomplete"):
        jg._parse_test_sh(corrupted)


def test_pinned_sha_matches_lock() -> None:
    root = jg.jool_root()
    got = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert got == jg._lock_sha()
