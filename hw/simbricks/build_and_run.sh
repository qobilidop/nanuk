#!/usr/bin/env bash
# End-to-end driver for the nanuk SimBricks demo, run from the repo root on
# the host (macOS or Linux; needs Docker + the nanuk devcontainer):
#
#   hw/simbricks/build_and_run.sh [path/to/prog.bin]
#
# Steps: assemble the demo parser program and export nanuk_core.v (nanuk
# devcontainer); verilate NATIVELY with the devcontainer's Verilator 5 —
# the SimBricks image ships Verilator 4.038, whose weak output splitting
# produces C++ that crashes the (emulated) compiler; then inside the
# SimBricks container: compile the portable generated C++, link the
# component, run the experiment with the local runtime, check ping output.
set -euo pipefail

cd "$(dirname "$0")/../.."
REPO="$PWD"
IMG=simbricks/simbricks-local:latest
OUT="$REPO/hw/simbricks/out"
STAGE="$REPO/hw/simbricks/stage"

PROG="${1:-}"
if [ -z "$PROG" ]; then
  echo "==> assembling demo parser program"
  ./dev.sh bash -lc 'cd python && uv sync --quiet && uv run nanuk-asm ../examples/l2l3l4/parse.asm -o ../hw/simbricks/prog.bin'
else
  cp "$PROG" hw/simbricks/prog.bin
fi

if [ ! -f hw/build/nanuk_core.v ]; then
  echo "==> exporting nanuk_core.v"
  ./dev.sh bash -lc 'cd python && uv sync --quiet --extra rtl && cd ../hw && uv run --project ../python python export.py build/nanuk_core.v'
fi

echo "==> verilating with devcontainer verilator 5 (native)"
rm -rf "$STAGE" && mkdir -p "$STAGE"
./dev.sh bash -lc '
    verilator -Wno-WIDTH -Wno-PINMISSING -Wno-IMPLICIT -Wno-SELRANGE \
        -Wno-CASEINCOMPLETE -Wno-UNSIGNED -Wno-fatal \
        --timescale 1ns/1ps --cc -O2 \
        --output-split 2000 --output-split-cfuncs 500 \
        --Mdir hw/simbricks/stage/obj_dir \
        hw/build/nanuk_core.v
    cp -r /usr/share/verilator hw/simbricks/stage/verilator
'

echo "==> compiling and running inside SimBricks container"
mkdir -p "$OUT"
rm -f "$OUT/run.log"
docker run --rm --platform linux/amd64 \
  -v "$REPO:/nanuk:ro" -v "$STAGE:/stage" -v "$OUT:/out" \
  $IMG bash -ec '
    echo "==> compiling verilated model archive (emulated amd64)"
    make -C /stage/obj_dir -f Vnanuk_core.mk -j2 \
        VERILATOR_ROOT=/stage/verilator Vnanuk_core__ALL.a > /dev/null

    echo "==> linking component"
    mkdir -p /simbricks/sims/net/nanuk
    g++ -O1 -g -std=gnu++17 \
        -I/stage/obj_dir -I/stage/verilator/include \
        -I/stage/verilator/include/vltstd \
        -I/simbricks/lib -iquote /simbricks \
        /nanuk/hw/simbricks/nanuk_hw.cc \
        /stage/obj_dir/Vnanuk_core__ALL.a \
        /stage/verilator/include/verilated.cpp \
        /stage/verilator/include/verilated_threads.cpp \
        /simbricks/lib/simbricks/network/libnetwork.a \
        /simbricks/lib/simbricks/base/libbase.a \
        -lpthread -o /simbricks/sims/net/nanuk/nanuk_hw

    cp /nanuk/hw/simbricks/nanuk_run.sh /nanuk/hw/simbricks/nanuk_demo.py \
       /nanuk/hw/simbricks/prog.bin /simbricks/sims/net/nanuk/
    chmod +x /simbricks/sims/net/nanuk/nanuk_run.sh

    echo "==> running experiment"
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
