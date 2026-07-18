#!/usr/bin/env bash
# SIIT beats: a v4-only guest converses with a v6-only guest across address
# families through one Nanuk core running examples/siit/{parse,translate}.asm.
#
#   Beat 1 (ping):   v4 guest `ping -c 10 192.0.2.1`      -> 0% loss (10/10).
#                    ICMP echo translated v4->v6 on the way in and v6->v4 on
#                    the reply, both through the same silicon.
#   Beat 2 (iperf):  v4 guest iperf UDP to 192.0.2.1 -> a real bulk stream
#                    across the translator's growing (v4->v6, +20B) direction.
#   Beat 3 (ttl):    v4 guest `ping -c 12 -t 1 192.0.2.1`  -> 100% loss. The
#                    translator refuses hop-limit <= 1 (RFC 7915) with a
#                    silent DROP -- no ICMP error -- so nothing comes back.
#
# The SimBricks base guest kernel (linux-5.15.93) is built `CONFIG_IPV6=n`, so
# the v6 side answers IPv6 on the wire with a userspace AF_PACKET responder
# (siit_responder.py), not a kernel stack. That covers ICMPv6 echo (ping) and
# receiving the UDP stream. iperf TCP is out: it needs a real kernel IPv6
# stack on the v6 side, which this guest image cannot provide (documented; the
# fix is an IPv6-enabled guest kernel, outside benchmarks/e2e/).
#
# The switch runs in middlebox flood mode (-x): translate.asm rewrites the
# frame but takes no forwarding decision (md[0] untouched), so the packaging
# floods each translated frame out the far port. The DEMO_SIIT table plane
# (t0/t1/t2, from testkit.siit_tables()) rides the same tables.txt path as
# every other beat -- no datapath change.
#
# Run from anywhere: benchmarks/e2e/run_siit.sh [ping|iperf_udp|ttl]
#   With no argument, all three beats run (the committed, reviewed flow).
#   With one, only that beat runs -- for fast iteration while tuning a single
#   beat; not how the beats are meant to be verified for the report.
set -euo pipefail

ONLY="${1:-}"
case "$ONLY" in
  ""|ping|iperf_udp|ttl) ;;
  *) echo "usage: $0 [ping|iperf_udp|ttl]" >&2; exit 1 ;;
esac

cd "$(dirname "$0")/../.."   # benchmarks/e2e -> repo root
REPO="$PWD"
[ -d "$REPO/hw/amaranth" ] || { echo "not at the repo root: $REPO" >&2; exit 1; }
IMG=simbricks/simbricks-local:latest
SB="$REPO/benchmarks/e2e"
OUT="$SB/out"
mkdir -p "$OUT"

"$SB/build_component.sh"

echo "==> assembling SIIT programs"
./dev.sh bash -lc '
    cd sw/python && uv sync --quiet &&
    uv run nanuk-pp-asm  ../../examples/siit/parse.asm     -o ../../benchmarks/e2e/out/prog.bin &&
    uv run nanuk-map-asm ../../examples/siit/translate.asm -o ../../benchmarks/e2e/out/map.bin
'

# DEMO_SIIT table plane, baked from testkit.siit_tables():
#   t0: v4 addr (32b) -> EAMT v6 addr high 64    192.0.2.1 -> 2001:db8:1::/...
#   t1: v4 addr (32b) -> EAMT v6 addr low 64                -> ...::c001
#   t2: v6 addr low 64 -> EAMT v4 addr (32b)     ::c001    -> 192.0.2.1
# (t3 flood is installed by the switch at boot; -x floods all-but-ingress.)
# This block hand-mirrors testkit.siit_tables() (DEMO_SIIT) rather than
# calling it -- this script is pure bash with no Python table-plane writer of
# its own. That's tolerable only because drift is loud, not silent: this
# path is exercised solely by a manual e2e run (never CI), so a stale
# mirror fails a beat immediately and visibly, not in a way that a future
# reader could miss. sw/python/tests/test_siit_vectors.py has a tripwire
# (test_e2e_tables_heredoc_matches_siit_tables) that parses this exact
# heredoc and diffs it against siit_tables() on every test run -- keep the
# two in lockstep, or that test is the one that will tell you first.
echo "==> writing SIIT tables.txt"
cat > "$OUT/tables.txt" <<'EOF'
table 0 32 64
entry 0 0xc0000201 0x20010db800010000
table 1 32 64
entry 1 0xc0000201 0xc001
table 2 64 32
entry 2 0xc001 0xc0000201
EOF

run_beat() {  # $1 = SIIT_BEAT value; log -> $OUT/run-siit-$1.log
  local beat="$1"
  echo "==> running SIIT beat: $beat"
  rm -f "$OUT/run-siit-$beat.log"
  docker run --rm --platform linux/amd64 -e "SIIT_BEAT=$beat" \
    -v "$REPO:/nanuk:ro" -v "$OUT:/out" \
    $IMG bash -ec '
      D=/simbricks/sims/net/nanuk
      mkdir -p $D
      cp /out/nanuk_switch /nanuk/benchmarks/e2e/nanuk_demo_siit.py \
         /nanuk/benchmarks/e2e/siit_responder.py \
         /out/prog.bin /out/map.bin /out/tables.txt $D/
      # Wrapper: nanuk_switch in middlebox flood mode (-x) with the SIIT
      # programs and table plane baked in.
      cat > $D/nanuk_run_siit.sh <<WRAP
#!/bin/sh
BIN="\$(dirname "\$0")/nanuk_switch"
DIR="\$(dirname "\$0")"
exec "\$BIN" "\$@" -x -f "\$DIR/prog.bin" -m "\$DIR/map.bin" -t "\$DIR/tables.txt"
WRAP
      chmod +x $D/nanuk_run_siit.sh
      cd /out
      python3 -m simbricks.local $D/nanuk_demo_siit.py \
          --verbose --force --repo /simbricks --workdir /out/work-siit-'"$beat"' 2>&1
    ' | tee "$OUT/run-siit-$beat.log" > /dev/null
}

# ---- Beat 1: ping across address families ----
if [ -z "$ONLY" ] || [ "$ONLY" = ping ]; then
  run_beat ping
  grep -qE ", 0% packet loss" "$OUT/run-siit-ping.log" || {
    echo "BEAT 1 FAILED: no clean ping v4->v6 (see $OUT/run-siit-ping.log)"; exit 1; }
  echo "beat 1 ok: ping 192.0.2.1 -> 0% loss (ICMP echo translated both ways)"
fi

# ---- Beat 2: iperf UDP through the translator (growing direction) ----
if [ -z "$ONLY" ] || [ "$ONLY" = iperf_udp ]; then
  run_beat iperf_udp
  grep -qE "Mbits/sec|Kbits/sec|bits/sec" "$OUT/run-siit-iperf_udp.log" || {
    echo "BEAT 2 (UDP) FAILED: no iperf transfer (see $OUT/run-siit-iperf_udp.log)"; exit 1; }
  # Cross-check against the switch's own counters -- not just iperf's
  # self-report. The guest's _wait_up connectivity poll (shared with beat 1)
  # sends its own ICMP echoes through the translator before iperf starts,
  # each one a "grew" frame at the switch too; subtract those (reported by
  # the guest as SIIT_WARMUP_PINGS) before comparing to what iperf sent.
  # nanuk_switch's rx_queue is bounded and drains only as fast as the
  # Verilator core can be simulated in real time, so a fast iperf send rate
  # can outrun that drain rate and most datagrams never reach the switch at
  # all (frames_in never counts them -- this is not a switch-side drop; see
  # nanuk_demo_siit.py). The reconciliation gate below (>= 90% of iperf's own
  # sent count actually arriving) is what makes the reported throughput
  # trustworthy.
  UDP_SENT=$(grep -oE "Sent [0-9]+ datagrams" "$OUT/run-siit-iperf_udp.log" | tail -1 | grep -oE "[0-9]+")
  UDP_GREW=$(grep -oE "grew=[0-9]+" "$OUT/run-siit-iperf_udp.log" | tail -1 | cut -d= -f2)
  WARMUP=$(grep -oE "SIIT_WARMUP_PINGS=[0-9]+" "$OUT/run-siit-iperf_udp.log" | tail -1 | cut -d= -f2)
  UDP_TRANSLATED=$(( ${UDP_GREW:-0} - ${WARMUP:-0} ))
  [ -n "${UDP_SENT:-}" ] && [ "$UDP_SENT" -gt 0 ] || {
    echo "BEAT 2 (UDP) FAILED: could not parse iperf's sent-datagram count"; exit 1; }
  THRESH=$(( (UDP_SENT * 9 + 9) / 10 ))   # ceil(0.9 * sent)
  [ "$UDP_TRANSLATED" -ge "$THRESH" ] || {
    echo "BEAT 2 (UDP) FAILED: iperf sent $UDP_SENT datagrams but only $UDP_TRANSLATED"\
         "(grew=${UDP_GREW:-0} - warmup=${WARMUP:-0}) reached the switch"\
         "(need >= $THRESH = 0.9x sent)"; exit 1; }
  echo "beat 2 ok: iperf sent $UDP_SENT datagrams, switch translated $UDP_TRANSLATED"\
       "(grew=${UDP_GREW:-0} - warmup=${WARMUP:-0} pings) v4->v6 -- reconciled >= 0.9x"
  echo "note: iperf TCP is not run -- it needs a kernel IPv6 stack on the v6 side,"
  echo "      which the SimBricks base guest kernel (CONFIG_IPV6=n) cannot provide."
fi

# ---- Beat 3: negative gate, TTL=1 must be dropped ----
if [ -z "$ONLY" ] || [ "$ONLY" = ttl ]; then
  run_beat ttl
  grep -qE ", 100% packet loss" "$OUT/run-siit-ttl.log" || {
    echo "BEAT 3 FAILED: TTL=1 ping was NOT fully dropped (see $OUT/run-siit-ttl.log)"; exit 1; }
  echo "beat 3 ok: TTL=1 ping -> 100% loss (translator drops hop-limit<=1, no ICMP error)"
fi

echo "SIIT BEATS PASSED"
