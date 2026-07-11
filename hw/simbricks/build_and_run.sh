#!/usr/bin/env bash
# End-to-end driver for the nanuk SimBricks demo, run from the repo root on
# the host (macOS or Linux; needs Docker):
#
#   hw/simbricks/build_and_run.sh [path/to/prog.bin]
#
# Steps: assemble the demo parser program (in the nanuk devcontainer),
# export nanuk_core.v (host venv or devcontainer), then inside the SimBricks
# container: verilate + compile nanuk_hw, stage sims/net/nanuk, and run the
# experiment with the local runtime. Greps the ping output for success.
set -euo pipefail

cd "$(dirname "$0")/../.."
REPO="$PWD"
IMG=simbricks/simbricks-local:latest
OUT="$REPO/hw/simbricks/out"

PROG="${1:-}"
if [ -z "$PROG" ]; then
  echo "==> assembling demo parser program"
  ./dev.sh bash -lc 'cd spec/python && uv run nanuk-asm ../../examples/l2l3l4/parse.asm -o ../../hw/simbricks/prog.bin'
  PROG="hw/simbricks/prog.bin"
else
  cp "$PROG" hw/simbricks/prog.bin
fi

if [ ! -f hw/build/nanuk_core.v ]; then
  echo "==> exporting nanuk_core.v"
  ./dev.sh bash -lc 'cd hw && uv sync --quiet && uv run python export.py build/nanuk_core.v'
fi

echo "==> building and running inside SimBricks container"
mkdir -p "$OUT"
docker run --rm --platform linux/amd64 \
  -v "$REPO:/nanuk:ro" -v "$OUT:/out" \
  $IMG bash -ec '
    mkdir -p /simbricks/sims/net/nanuk/rtl
    cp /nanuk/hw/simbricks/nanuk_hw.cc /nanuk/hw/simbricks/nanuk_run.sh \
       /nanuk/hw/simbricks/nanuk_demo.py /nanuk/hw/simbricks/prog.bin \
       /simbricks/sims/net/nanuk/
    cp /nanuk/hw/build/nanuk_core.v /simbricks/sims/net/nanuk/rtl/
    chmod +x /simbricks/sims/net/nanuk/nanuk_run.sh
    cd /simbricks/sims/net/nanuk

    echo "==> verilating nanuk_core"
    # --output-split + -O1: the 2048-bit extraction datapath generates huge
    # C++ TUs that make cc1plus segfault (ICE) at -O3 under emulation.
    verilator +1364-2005ext+v -Wno-WIDTH -Wno-PINMISSING -Wno-LITENDIAN \
        -Wno-IMPLICIT -Wno-SELRANGE -Wno-CASEINCOMPLETE -Wno-UNSIGNED \
        -Wno-fatal --timescale 1ns/1ps --cc -O2 \
        --output-split 5000 --output-split-cfuncs 5000 \
        -CFLAGS "-I/simbricks/lib -iquote /simbricks -O1 -g" \
        --Mdir obj_dir rtl/nanuk_core.v \
        --exe /simbricks/sims/net/nanuk/nanuk_hw.cc \
        /simbricks/lib/simbricks/network/libnetwork.a \
        /simbricks/lib/simbricks/base/libbase.a
    make -C obj_dir -f Vnanuk_core.mk -j"$(nproc)" > /dev/null
    cp obj_dir/Vnanuk_core nanuk_hw

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
