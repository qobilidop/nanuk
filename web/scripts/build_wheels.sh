#!/usr/bin/env bash
# Build the Nanuk wheel into web/public/wheels/ with a manifest, and copy
# the bridge next to it. Run via uv (devcontainer or any host with uv).
# The wheel's [rtl] extra (amaranth) is not requested, so the Pyodide
# bundle stays protobuf-only.
set -euo pipefail
WEB="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(dirname "$WEB")"
OUT="$WEB/public/wheels"
rm -rf "$OUT" && mkdir -p "$OUT"
(cd "$REPO/sw/python" && uv build --wheel --out-dir "$OUT" --quiet)
cp "$WEB/py/bridge.py" "$WEB/public/bridge.py"
(cd "$OUT" && ls *.whl | python3 -c \
  'import json,sys; print(json.dumps({"wheels": sys.stdin.read().split()}))' \
  > "$OUT/manifest.json")
echo "wheels: $(cat "$OUT/manifest.json")"
