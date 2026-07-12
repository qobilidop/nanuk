#!/usr/bin/env bash
# Build the nanuk SimBricks component (composed PP->MAP): export both cores'
# Verilog, verilate NATIVELY with the devcontainer's Verilator 5 (the
# SimBricks image ships a broken-old 4.038), then compile the portable
# generated C++ and link inside the SimBricks container (amd64).
#
# Output: demo/out/nanuk_hw (linux/amd64 binary) — reused by the
# beat run scripts. Skips the build when the binary is already newer than
# its sources; FORCE_BUILD=1 rebuilds.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
IMG=simbricks/simbricks-local:latest
STAGE="$REPO/demo/stage"
OUT="$REPO/demo/out"
BIN="$OUT/nanuk_hw"

newer_than_sources() {
  [ -f "$BIN" ] || return 1
  for f in hw/amaranth/nanuk_amaranth/core.py hw/amaranth/nanuk_amaranth/map_core.py demo/nanuk_hw.cc; do
    [ "$BIN" -nt "$f" ] || return 1
  done
  return 0
}

if [ "${FORCE_BUILD:-0}" != "1" ] && newer_than_sources; then
  echo "==> component up to date ($BIN)"
  exit 0
fi

echo "==> exporting Verilog (both cores)"
./dev.sh bash -lc '
    cd hw/amaranth && uv sync --quiet &&
    uv run nanuk-export ../../demo/build/nanuk_core.v &&
    uv run nanuk-export --core map ../../demo/build/nanuk_map_core.v
'

echo "==> verilating with devcontainer verilator 5 (native)"
rm -rf "$STAGE" && mkdir -p "$STAGE"
./dev.sh bash -lc '
    for core in nanuk_core nanuk_map_core; do
        verilator -Wno-WIDTH -Wno-PINMISSING -Wno-IMPLICIT -Wno-SELRANGE \
            -Wno-CASEINCOMPLETE -Wno-UNSIGNED -Wno-fatal \
            --timescale 1ns/1ps --cc -O2 \
            --output-split 2000 --output-split-cfuncs 500 \
            --Mdir demo/stage/obj_$core \
            demo/build/$core.v
    done
    cp -r /usr/share/verilator demo/stage/verilator
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
    retry_make -C /stage/obj_nanuk_map_core -f Vnanuk_map_core.mk -j2 \
        VERILATOR_ROOT=/stage/verilator Vnanuk_map_core__ALL.a
    g++ -O1 -g -std=gnu++17 \
        -I/stage/obj_nanuk_core -I/stage/obj_nanuk_map_core \
        -I/stage/verilator/include -I/stage/verilator/include/vltstd \
        -I/simbricks/lib -iquote /simbricks \
        /nanuk/demo/nanuk_hw.cc \
        /stage/obj_nanuk_core/Vnanuk_core__ALL.a \
        /stage/obj_nanuk_map_core/Vnanuk_map_core__ALL.a \
        /stage/verilator/include/verilated.cpp \
        /stage/verilator/include/verilated_threads.cpp \
        /simbricks/lib/simbricks/network/libnetwork.a \
        /simbricks/lib/simbricks/base/libbase.a \
        -lpthread -o /out/nanuk_hw
'
echo "==> built $BIN"
