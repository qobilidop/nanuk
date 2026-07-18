#!/usr/bin/env bash
set -euo pipefail

# Idempotent, pinned-commit fetch for Jool's SIIT graybox test suite -- the
# independent-interpretation oracle for the SIIT demo (Plan B). Clones a
# shallow, single-commit checkout into gitignored third_party/jool at the
# SHA recorded in jool.lock. Zero GPL bytes land under benchmarks/ or sw/:
# only this script and the jool.lock pointer file are committed.
#
# Usage:
#   benchmarks/siit/fetch_jool.sh          # fetch/update to the locked SHA,
#                                           # prints the checkout path
#   benchmarks/siit/fetch_jool.sh --check  # verify only, no network;
#                                           # nonzero exit if absent or at
#                                           # the wrong SHA
#
# bash 3.2 compatible (macOS default /bin/bash) -- no associative arrays,
# no [[ ]]-only features.

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
LOCK="$HERE/jool.lock"
CLONE_DIR="$REPO_ROOT/third_party/jool"

url="$(grep '^url=' "$LOCK" | cut -d= -f2-)"
sha="$(grep '^sha=' "$LOCK" | cut -d= -f2-)"

current_sha() {
	if [ -d "$CLONE_DIR/.git" ]; then
		(cd "$CLONE_DIR" && git rev-parse HEAD 2>/dev/null) || true
	fi
}

if [ "${1:-}" = "--check" ]; then
	got="$(current_sha)"
	if [ "$got" = "$sha" ]; then
		echo "$CLONE_DIR"
		exit 0
	fi
	echo "jool clone absent or at the wrong SHA (want $sha, got ${got:-<absent>});" \
		"run benchmarks/siit/fetch_jool.sh" >&2
	exit 1
fi

if [ "$(current_sha)" = "$sha" ]; then
	echo "$CLONE_DIR"
	exit 0
fi

rm -rf "$CLONE_DIR"
mkdir -p "$CLONE_DIR"
git init -q "$CLONE_DIR"
(
	cd "$CLONE_DIR"
	git remote add origin "$url"
	git fetch --depth 1 origin "$sha"
	git checkout -q FETCH_HEAD
)

echo "$CLONE_DIR"
