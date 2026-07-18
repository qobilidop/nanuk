"""Acquisition of Jool's SIIT graybox fixtures as an independent-
interpretation oracle for the SIIT demo (Plan B, task B1).

Nothing here embeds Jool's fixture bytes: `.pkt` files are read from the
gitignored `third_party/jool` clone at test time only (see
`benchmarks/siit/fetch_jool.sh` and `jool.lock`). The manifest is extracted
from the suite's own scripts (`siit/test.sh`) -- parsed, not guessed, per
the plan's frozen "pair + mask source of truth" decision -- and the address
config is extracted from `siit/setup-jool.sh`'s `jool_siit` commands.

Scope (frozen): the SIIT graybox suite only
(`test/graybox/test-suite/siit/`) -- the `pktgen/{sender,receiver}`
combinatorial pairs and the `7915/` lettered pairs. The suite's own
`manual/`-backed "misc" test group is out of scope this plan and is
skipped by the parser below. NAT64 dirs are never touched.

Direction naming: `test.sh` actually drives FOUR helper families, not just
two -- `test46_*`/`test64_*` (cross-family translate, what this replay
plan exercises) AND `test66_11`/`test44_11` (Jool responding to itself in
one family, e.g. locally generated ICMPv6/ICMPv4 messages). We keep all
four as literal direction strings ("46"/"64"/"66"/"44", named after the
helper suffix) rather than forcing everything into 46/64: the 66/44
fixtures are still discoverable in the manifest, and it's B2's job (not
this module's) to classify them (almost certainly `out_of_scope` --
Nanuk's SIIT never locally originates ICMP messages).
"""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path

from nanuk.testkit.siit_ref import SiitConfig

# Anchored like testkit/load.py's _EXAMPLES and siit_ref._VECTORS_DIR: this
# file lives at sw/python/nanuk/testkit/jool_graybox.py, four parents up is
# the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_ROOT = _REPO_ROOT / "third_party" / "jool"
_LOCK = _REPO_ROOT / "benchmarks" / "siit" / "jool.lock"

# The one subpath this plan reads from the clone (mirrors jool.lock's
# `subpath=` line -- kept as a literal here too so this module has no
# runtime dependency on parsing that comment format).
_SUITE = "test/graybox/test-suite/siit"

# The suite's own group token for its manual/-backed tests (see
# `test.sh`'s `"$1" = "misc"` branch) -- out of scope this plan.
_SKIP_GROUPS = frozenset({"misc"})


def _lock_sha() -> str:
    for line in _LOCK.read_text().splitlines():
        if line.startswith("sha="):
            return line.split("=", 1)[1].strip()
    raise ValueError(f"{_LOCK}: no sha= line")


def jool_root() -> Path | None:
    """The Jool clone root, or None if absent.

    `NANUK_JOOL_ROOT` overrides the default `third_party/jool`; an
    override is trusted as given (no SHA check -- that's
    `fetch_jool.sh --check`'s job for the default, fetch-script-managed
    path). Presence is judged by the suite subpath actually being there,
    not just an empty directory."""
    override = os.environ.get("NANUK_JOOL_ROOT")
    root = Path(override) if override else _DEFAULT_ROOT
    return root if (root / _SUITE).is_dir() else None


def requires_jool() -> tuple[bool, str]:
    """(skip, reason) for `pytest.mark.skipif`, mirroring the cosim-gate
    style (see `test_differential.py`): gated on `NANUK_JOOL=1` AND the
    clone actually being present."""
    if os.environ.get("NANUK_JOOL") != "1":
        return True, (
            "Jool replay needs NANUK_JOOL=1 and a fetched clone "
            "(benchmarks/siit/fetch_jool.sh)"
        )
    if jool_root() is None:
        return True, "Jool clone absent -- run benchmarks/siit/fetch_jool.sh"
    return False, ""


@dataclass(frozen=True)
class Fixture:
    name: str  # unique manifest key, e.g. "7915/aat1" or "udp64/6-udp-csumok-df-nofrag"
    group: str  # test.sh's own group token: udp46/udp64/tcp64/icmpi46/icmpi64/icmpe46/icmpe64/rfc7915
    direction: str  # "46" | "64" | "66" | "44" -- the helper family used (see module docstring)
    sender: Path  # relative to the suite root (_SUITE)
    expected: Path | None  # relative to the suite root; None for a drop-expectation call (none exist today)
    exceptions: tuple[int, ...]  # byte offsets the suite itself says to ignore (their IDENTIFICATION=... lists)


# --- test.sh manifest parsing -----------------------------------------------
#
# A small deliberate parser, not regex soup: `test.sh` is POSIX sh, structured
# as (a) a handful of top-level `VAR=4,5,10,11`-style exception-list
# assignments, then (b) a sequence of `if [ -z "$1" -o "$1" = "GROUP" ]; then
# ... fi` blocks (no nesting), each calling one of four helper families:
#
#   test64_auto/test46_auto <sender> <expected> [exceptions]
#       -- pktgen/{sender,receiver} combinatorial pairs; group is the
#          enclosing if-block's token (udp64, tcp64, icmpi46, ...).
#   test64_11/test46_11/test66_11/test44_11 <subdir> <sender> <expected> [exceptions]
#       -- one sender, one expected, both under <subdir>/ (always "7915"
#          in practice -- the "misc" block uses "manual" but is skipped).
#   test64_12/test46_12 <subdir> <sender> <expected1> <expected2> [exceptions]
#       -- one sender, TWO expected files (an IPv4/IPv6 fragmentation
#          split). Fixture only carries one `expected`, so each becomes
#          two manifest entries sharing the sender, named "<base>#1"/"#2".
#
# Exceptions are shell words like "$IDENTIFICATION" or "$TOS,$IDENTIFICATION"
# or bare literals like "44,45,46,47"; resolved by textual substitution of
# the top-level variable assignments, then split on commas to ints.

_CALL_RE = re.compile(r"^(test(?:64|46|66|44)_(?:auto|11|12))\s+(.+?)\s*$")
# A DELIBERATELY BROADER pattern than `_CALL_RE`: it matches any line that
# *looks like* a direction-prefixed test invocation (`test64_<anything>
# <args>`), including helper variants `_CALL_RE` does not know how to parse.
# The definitions (`test64_auto() {`) are excluded because `()` follows the
# name with no whitespace. Used only by the parse-completeness guard below
# (carried forward from B1's review): every invocation-looking line inside an
# in-scope group block MUST be turned into fixtures, or the parse is silently
# partial and we raise rather than ship a truncated manifest.
_INVOKE_RE = re.compile(r"^test(?:64|46|66|44)_\w+\s")
_GROUP_IF_RE = re.compile(r'^\s*if\s+\[\s+-z\s+"\$1"\s+-o\s+"\$1"\s*=\s*"([A-Za-z0-9_]+)"\s+\];\s*then\s*$')
_VAR_RE = re.compile(r"^([A-Z_]+)=([0-9]+(?:,[0-9]+)*)\s*$")


def _parse_vars(text: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for line in text.splitlines():
        m = _VAR_RE.match(line.strip())
        if m:
            variables[m.group(1)] = m.group(2)
    return variables


def _resolve_exceptions(raw: str | None, variables: dict[str, str]) -> tuple[int, ...]:
    if not raw:
        return ()
    expanded = raw
    for varname, value in variables.items():
        expanded = expanded.replace(f"${varname}", value)
    return tuple(int(tok) for tok in expanded.split(",") if tok)


def _unique(seen: dict[str, int], base: str) -> str:
    """Some sender/subdir pairs are legitimately invoked twice under
    different runtime settings (e.g. rfc7915's `cct1` under `cc` vs `ck`,
    or `ect1` under both `amend-udp-checksum-zero` settings) -- disambiguate
    with a `@2`, `@3`, ... suffix rather than silently colliding names."""
    seen[base] = seen.get(base, 0) + 1
    n = seen[base]
    return base if n == 1 else f"{base}@{n}"


def _parse_test_sh(text: str) -> list[Fixture]:
    variables = _parse_vars(text)
    fixtures: list[Fixture] = []
    current_group: str | None = None
    seen: dict[str, int] = {}
    candidate_calls = 0  # lines that look like an in-scope test invocation
    matched_calls = 0  # lines the parser actually turned into fixture(s)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _GROUP_IF_RE.match(raw_line)
        if m:
            current_group = m.group(1)
            continue
        if line == "fi":
            current_group = None
            continue
        if current_group is None or current_group in _SKIP_GROUPS:
            continue
        if _INVOKE_RE.match(line):
            candidate_calls += 1
        m = _CALL_RE.match(line)
        if not m:
            continue
        matched_calls += 1

        call, argstr = m.group(1), m.group(2)
        args = argstr.split()
        call_prefix, kind = call.split("_", 1)
        direction = call_prefix[len("test") :]  # "test64_auto" -> "64"

        if kind == "auto":
            sender, expected = args[0], args[1]
            exceptions = _resolve_exceptions(args[2] if len(args) > 2 else None, variables)
            name = _unique(seen, f"{current_group}/{sender}")
            fixtures.append(
                Fixture(
                    name=name,
                    group=current_group,
                    direction=direction,
                    sender=Path("pktgen") / "sender" / f"{sender}.pkt",
                    expected=Path("pktgen") / "receiver" / f"{expected}.pkt",
                    exceptions=exceptions,
                )
            )
        elif kind == "11":
            subdir, sender, expected = args[0], args[1], args[2]
            exceptions = _resolve_exceptions(args[3] if len(args) > 3 else None, variables)
            name = _unique(seen, f"{subdir}/{sender}")
            fixtures.append(
                Fixture(
                    name=name,
                    group=current_group,
                    direction=direction,
                    sender=Path(subdir) / f"{sender}.pkt",
                    expected=Path(subdir) / f"{expected}.pkt",
                    exceptions=exceptions,
                )
            )
        elif kind == "12":
            subdir, sender, expected1, expected2 = args[0], args[1], args[2], args[3]
            exceptions = _resolve_exceptions(args[4] if len(args) > 4 else None, variables)
            base = _unique(seen, f"{subdir}/{sender}")
            for i, expected in enumerate((expected1, expected2), start=1):
                fixtures.append(
                    Fixture(
                        name=f"{base}#{i}",
                        group=current_group,
                        direction=direction,
                        sender=Path(subdir) / f"{sender}.pkt",
                        expected=Path(subdir) / f"{expected}.pkt",
                        exceptions=exceptions,
                    )
                )

    # Parse-completeness guard (B1 review carry-forward). `_INVOKE_RE` counts
    # every invocation-looking line in an in-scope block; `_CALL_RE` is the
    # narrower pattern the parser understands. If a line looks like a test
    # call but the parser did not turn it into fixtures, the manifest is
    # silently partial -- refuse loudly rather than under-report coverage.
    if candidate_calls != matched_calls:
        raise ValueError(
            f"test.sh parse is incomplete: {candidate_calls} invocation-looking "
            f"lines in scope, but only {matched_calls} were parsed into fixtures. "
            "An unrecognized test helper variant would be silently dropped -- "
            "extend _CALL_RE (and Fixture handling) to cover it, do not ship a "
            "truncated manifest."
        )
    return fixtures


def load_manifest(root: Path) -> list[Fixture]:
    """Parse `<root>/test/graybox/test-suite/siit/test.sh` into the
    fixture manifest. `sender`/`expected` are returned relative to the
    suite root (`root / _SUITE`); resolve against that same root to open
    the files."""
    text = (root / _SUITE / "test.sh").read_text()
    return _parse_test_sh(text)


# --- setup-jool.sh config mirror --------------------------------------------
#
# Only setup-jool.sh's `jool_siit` commands are mirrored (frozen decision:
# the config mirror reads setup*.sh, not test.sh's mid-run reconfiguration
# in the "misc"/rfc7915 MTU and amend-udp-checksum-zero toggles -- those
# are runtime knobs for out-of-scope tests, not the base config this
# replay's SiitConfig represents).
#
# As of B2 the mirror is FAITHFUL to Jool's real config: the pool6 is a /40
# (`2001:db8:100::/40`) mirrored with its true prefix length, and the two
# EAMT entries are the /24<->/120 PREFIX pairs Jool actually configures.
# `SiitConfig` now models both (RFC 6052 all six prefix lengths; RFC 7757
# prefix EAMT with longest-prefix-match), so the reference oracle translates
# these exactly as Jool's addressing does -- no would-be-/96 truncation, no
# network-address-only EAMT stand-in. (The Nanuk *program* still implements
# only /96 + exact EAMT; that scope split lives in benchmarks/siit and is a
# program-vs-reference distinction, not a limitation of this mirror.)

_INSTANCE_POOL6_RE = re.compile(r"instance\s+add\s+--netfilter\s+-6\s+(\S+)")
_EAMT_ADD_RE = re.compile(r"eamt\s+add\s+(\S+)\s+(\S+)")


def jool_config(root: Path) -> SiitConfig:
    """Mirror `setup-jool.sh`'s `jool_siit instance add`/`eamt add` calls
    into a `SiitConfig`, faithfully: the pool6 prefix length and the EAMT
    prefix pairs are preserved exactly as Jool configures them."""
    text = (root / _SUITE / "setup-jool.sh").read_text()
    pool6: bytes | None = None
    pool6_len = 96
    eamt: list[tuple[str, str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _INSTANCE_POOL6_RE.search(line)
        if m:
            net, plen = m.group(1).split("/", 1)
            pool6 = socket.inet_pton(socket.AF_INET6, net)[:12]
            pool6_len = int(plen)
            continue
        m = _EAMT_ADD_RE.search(line)
        if m:
            first, second = m.group(1), m.group(2)
            # Jool's own CLI detects family by character (':' -> v6, '.' ->
            # v4), not by position -- setup-jool.sh happens to write v6
            # first, but don't assume that; match Jool's own rule. CIDRs are
            # kept intact so SiitConfig sees the true prefix pair.
            v6_raw, v4_raw = (first, second) if ":" in first else (second, first)
            eamt.append((v4_raw, v6_raw))

    kwargs: dict[str, object] = {"eamt": tuple(eamt)}
    if pool6 is not None:
        kwargs["pool6"] = pool6
        kwargs["pool6_len"] = pool6_len
    return SiitConfig(**kwargs)
