#!/usr/bin/env bash
# Build an IPv6-enabled SimBricks guest kernel for the SIIT iperf TCP beat.
#
# The SimBricks base guest kernel (linux-5.15.93, /simbricks/images/bzImage)
# is built `# CONFIG_IPV6 is not set` -- no kernel IPv6 stack at all -- which
# is why the SIIT scenario's v6 guest answers ping from userspace and why
# iperf TCP (a real kernel TCP/IPv6 stack on the v6 side) was out of reach.
#
# This script rebuilds the SAME kernel the image ships -- same 5.15.93
# source, same gem5-timers patch, same config -- with two deltas:
#   CONFIG_IPV6=y   the point of the exercise
#   CONFIG_E1000=y  the config ships it =m, and the module tree on the guest
#                   disk belongs to the STOCK build -- a rebuilt kernel must
#                   not depend on disk modules, or eth0 never appears
#                   (verified the hard way: first boot had IPv6 and no NIC)
# The result is cached at benchmarks/e2e/out/bzImage-ipv6; run_siit.sh mounts
# it over /simbricks/images/bzImage for the iperf_tcp beat only, so the three
# reviewed stock-kernel beats are untouched.
#
# Everything (fetch, patch, configure, compile) runs inside the SimBricks
# container: it carries the toolchain (gcc 11, make, flex, bison, bc) and the
# authoritative config + patch under /simbricks/images/kernel/. On an Apple
# Silicon host this is an amd64 build under Rosetta -- expect tens of
# minutes on first run; later runs hit the cache and return immediately.
set -euo pipefail

cd "$(dirname "$0")/../.."   # benchmarks/e2e -> repo root
REPO="$PWD"
[ -d "$REPO/hw/amaranth" ] || { echo "not at the repo root: $REPO" >&2; exit 1; }
IMG=simbricks/simbricks-local:latest
OUT="$REPO/benchmarks/e2e/out"
mkdir -p "$OUT"

KVER=5.15.93
BZ="$OUT/bzImage-ipv6"
TARBALL="$OUT/linux-$KVER.tar.xz"

if [ -f "$BZ" ]; then
  echo "guest kernel cached: $BZ"
  exit 0
fi

if [ ! -f "$TARBALL" ]; then
  echo "==> fetching linux-$KVER source"
  curl -fL --retry 3 -o "$TARBALL.part" \
    "https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-$KVER.tar.xz"
  mv "$TARBALL.part" "$TARBALL"
fi

echo "==> building IPv6-enabled guest kernel (linux-$KVER) in the SimBricks container"
docker run --rm --platform linux/amd64 \
  -v "$OUT:/out" \
  $IMG bash -ec '
    KVER='"$KVER"'
    cd /tmp
    tar xf /out/linux-$KVER.tar.xz
    cd linux-$KVER
    patch -p1 < /simbricks/images/kernel/linux-$KVER-timers-gem5.patch
    cp /simbricks/images/kernel/config-$KVER .config
    ./scripts/config --enable CONFIG_IPV6
    ./scripts/config --enable CONFIG_E1000
    make olddefconfig
    # Sanity: both deltas actually took as =y (built-in, not =m -- this
    # kernel must not depend on the disk image'"'"'s module tree).
    grep -q "^CONFIG_IPV6=y" .config || { echo "CONFIG_IPV6=y did not take" >&2; exit 1; }
    grep -q "^CONFIG_E1000=y" .config || { echo "CONFIG_E1000=y did not take" >&2; exit 1; }
    # Emulated (Rosetta) gcc segfaults sporadically on random objects in this
    # container (same flake as build_component.sh); make resumes from the
    # failed object, so retry until it converges. A kernel build has ~20x the
    # objects of the Verilator build, so give it more rounds than the usual 3.
    ok=""
    for i in $(seq 1 12); do
      if make -j"$(nproc)" bzImage; then ok=1; break; fi
      echo "==> make round $i failed (Rosetta gcc flake?), retrying" >&2
    done
    [ -n "$ok" ] || { echo "kernel build failed after 12 rounds" >&2; exit 1; }
    cp arch/x86/boot/bzImage /out/bzImage-ipv6
  '
echo "guest kernel built: $BZ"
