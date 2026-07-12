#!/usr/bin/env bash
# End-to-end smoke for the nanuk SimBricks demo, run from the repo root on
# the host (macOS or Linux; needs Docker + the nanuk devcontainer):
#
#   demo/build_and_run.sh [path/to/prog.bin]
#
# Builds the composed PP->MAP component (build_component.sh), assembles the
# l2l3l4 parser program (or takes yours) plus the map_l2fwd MAP program, and
# runs the two-host ping experiment with no tables staged: every frame the
# parser accepts misses the empty table and floods — the original flood demo,
# riding the composed core.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
IMG=simbricks/simbricks-local:latest
OUT="$REPO/demo/out"
mkdir -p "$OUT"

"$REPO/demo/build_component.sh"

PROG="${1:-}"
echo "==> assembling programs"
./dev.sh bash -lc '
    cd sw/python && uv sync --quiet &&
    uv run nanuk-map-asm ../../examples/map_l2fwd/fwd.asm -o ../../demo/out/map.bin
'
if [ -z "$PROG" ]; then
  ./dev.sh bash -lc '
      cd sw/python &&
      uv run nanuk-asm ../../examples/l2l3l4/parse.asm -o ../../demo/out/prog.bin
  '
else
  cp "$PROG" "$OUT/prog.bin"
fi

echo "==> running experiment"
rm -f "$OUT/run.log"
docker run --rm --platform linux/amd64 \
  -v "$REPO:/nanuk:ro" -v "$OUT:/out" \
  $IMG bash -ec '
    mkdir -p /simbricks/sims/net/nanuk
    cp /out/nanuk_switch /nanuk/demo/nanuk_run.sh /nanuk/demo/nanuk_demo.py \
       /out/prog.bin /out/map.bin /simbricks/sims/net/nanuk/
    chmod +x /simbricks/sims/net/nanuk/nanuk_run.sh
    cd /out
    python3 -m simbricks.local /simbricks/sims/net/nanuk/nanuk_demo.py \
        --verbose --force --repo /simbricks --workdir /out/work 2>&1 | tee /out/run.log
'

echo "==> checking ping result"
if grep -E "[0-9]+ bytes from 10\.0\.0\.2|, 0% packet loss" "$OUT/run.log" > /dev/null; then
  echo "E2E DEMO PASSED: ping through nanuk RTL succeeded"
else
  echo "E2E DEMO FAILED: no successful ping found in $OUT/run.log"
  exit 1
fi
