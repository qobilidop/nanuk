#!/usr/bin/env bash
# Build the nanuk_switch SimBricks component: export the composed core's
# Verilog, verilate NATIVELY with the devcontainer's Verilator 5 (the
# SimBricks image ships a broken-old 4.038), then compile the portable
# generated C++ and link inside the SimBricks container (amd64).
#
# Output: benchmarks/e2e/out/nanuk_switch (linux/amd64 binary) — reused by the
# beat run scripts. Skips the build when the binary is already newer than
# its sources; FORCE_BUILD=1 rebuilds.
set -euo pipefail

cd "$(dirname "$0")/../.."   # benchmarks/e2e -> repo root
REPO="$PWD"
[ -d "$REPO/hw/amaranth" ] || { echo "not at the repo root: $REPO" >&2; exit 1; }
IMG=simbricks/simbricks-local:latest
STAGE="$REPO/benchmarks/e2e/stage"
OUT="$REPO/benchmarks/e2e/out"
BIN="$OUT/nanuk_switch"

newer_than_sources() {
  [ -f "$BIN" ] || return 1
  for f in hw/amaranth/nanuk_amaranth/pp.py hw/amaranth/nanuk_amaranth/map.py \
           hw/amaranth/nanuk_amaranth/core.py benchmarks/e2e/nanuk_switch.cc; do
    [ "$BIN" -nt "$f" ] || return 1
  done
  return 0
}

if [ "${FORCE_BUILD:-0}" != "1" ] && newer_than_sources; then
  echo "==> component up to date ($BIN)"
  exit 0
fi

echo "==> exporting Verilog (the composed core)"
./dev.sh bash -lc '
    cd hw/amaranth && uv sync --quiet &&
    uv run nanuk-export --processor core ../../benchmarks/e2e/build/nanuk_core.v
'

echo "==> verilating with devcontainer verilator 5 (native)"
rm -rf "$STAGE" && mkdir -p "$STAGE"
./dev.sh bash -lc '
    verilator -Wno-WIDTH -Wno-PINMISSING -Wno-IMPLICIT -Wno-SELRANGE \
        -Wno-CASEINCOMPLETE -Wno-UNSIGNED -Wno-fatal \
        --timescale 1ns/1ps --cc -O2 \
        --output-split 2000 --output-split-cfuncs 500 \
        --Mdir benchmarks/e2e/stage/obj_nanuk_core \
        benchmarks/e2e/build/nanuk_core.v
    cp -r /usr/share/verilator benchmarks/e2e/stage/verilator
'

echo "==> compiling and linking inside SimBricks container"
mkdir -p "$OUT"
docker run --rm --platform linux/amd64 \
  -v "$REPO:/nanuk:ro" -v "$STAGE:/stage" -v "$OUT:/out" \
  $IMG bash -ec '
    # Emulated (Rosetta) gcc segfaults sporadically; make resumes from the
    # failed object, so retry a few times before giving up.
    retry_make() { local i; for i in 1 2 3; do make "$@" > /dev/null && return 0; done; return 1; }
    retry_make -C /stage/obj_nanuk_core -f Vnanuk_core.mk -j2 \
        VERILATOR_ROOT=/stage/verilator Vnanuk_core__ALL.a
    g++ -O1 -g -std=gnu++17 \
        -I/stage/obj_nanuk_core \
        -I/stage/verilator/include -I/stage/verilator/include/vltstd \
        -I/simbricks/lib -iquote /simbricks \
        /nanuk/benchmarks/e2e/nanuk_switch.cc \
        /stage/obj_nanuk_core/Vnanuk_core__ALL.a \
        /stage/verilator/include/verilated.cpp \
        /stage/verilator/include/verilated_threads.cpp \
        /simbricks/lib/simbricks/network/libnetwork.a \
        /simbricks/lib/simbricks/base/libbase.a \
        -lpthread -o /out/nanuk_switch
'
echo "==> built $BIN"
