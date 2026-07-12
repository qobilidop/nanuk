#!/usr/bin/env bash
# Build nanuk-isa + nanuk-ir + nanuk-lang wheels into web/public/wheels/
# with a manifest, and copy the bridge next to them. Run via uv
# (devcontainer or any host with uv).
set -euo pipefail
WEB="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(dirname "$WEB")"
OUT="$WEB/public/wheels"
rm -rf "$OUT" && mkdir -p "$OUT"
(cd "$REPO/spec/isa" && uv build --wheel --out-dir "$OUT" --quiet)
(cd "$REPO/compiler" && uv build --wheel --out-dir "$OUT" --quiet)
(cd "$REPO/lang" && uv build --wheel --out-dir "$OUT" --quiet)
cp "$WEB/py/bridge.py" "$WEB/public/bridge.py"
(cd "$OUT" && ls *.whl | python3 -c \
  'import json,sys; print(json.dumps({"wheels": sys.stdin.read().split()}))' \
  > "$OUT/manifest.json")
echo "wheels: $(cat "$OUT/manifest.json")"
