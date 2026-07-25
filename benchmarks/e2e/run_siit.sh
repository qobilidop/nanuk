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
#   Beat 4 (tcp):    real kernel TCP across the boundary: v4 guest iperf TCP
#                    to a kernel-terminated iperf server on the v6 guest.
#
# The SimBricks base guest kernel (linux-5.15.93) is built `CONFIG_IPV6=n`, so
# in beats 1-3 the v6 side answers IPv6 on the wire with a userspace AF_PACKET
# responder (siit_responder.py), not a kernel stack. That covers ICMPv6 echo
# (ping) and receiving + classifying the UDP stream. TCP cannot be faked that
# way, so beat 4 boots an IPv6-enabled rebuild of the same kernel
# (build_guest_kernel.sh, mounted over the image's bzImage for that beat
# only) and lets the v6 guest's real kernel TCP/IPv6 stack terminate iperf.
#
# The switch runs in middlebox flood mode (-x): translate.asm rewrites the
# frame but takes no forwarding decision (md[0] untouched), so the packaging
# floods each translated frame out the far port. The DEMO_SIIT table plane
# (t0/t1/t2, from testkit.siit_tables()) rides the same tables.txt path as
# every other beat -- no datapath change.
#
# Run from anywhere: benchmarks/e2e/run_siit.sh [ping|iperf_udp|iperf_tcp|ttl]
#   With no argument, all four beats run (the committed, reviewed flow).
#   With one, only that beat runs -- for fast iteration while tuning a single
#   beat; not how the beats are meant to be verified for the report.
set -euo pipefail

ONLY="${1:-}"
case "$ONLY" in
  ""|ping|iperf_udp|iperf_tcp|ttl) ;;
  *) echo "usage: $0 [ping|iperf_udp|iperf_tcp|ttl]" >&2; exit 1 ;;
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
  # The iperf_tcp beat boots the IPv6-enabled rebuild of the guest kernel
  # (build_guest_kernel.sh) by mounting it over the image's bzImage; the
  # other beats run the stock CONFIG_IPV6=n kernel untouched.
  local kernel_mount=()
  if [ "$beat" = iperf_tcp ]; then
    kernel_mount=(-v "$OUT/bzImage-ipv6:/simbricks/images/bzImage:ro")
  fi
  # ${arr[@]+...}: macOS bash 3.2 treats an empty array as unset under -u.
  docker run --rm --platform linux/amd64 -e "SIIT_BEAT=$beat" \
    -v "$REPO:/nanuk:ro" -v "$OUT:/out" ${kernel_mount[@]+"${kernel_mount[@]}"} \
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
  # Receiver-side gate: the v6 responder logs every UDP datagram it receives
  # with iperf's own application seq number. Its last log line carries the
  # final tallies: dups=0 means nothing on the path duplicated a frame;
  # pos=N/M with N==M means the paced data datagrams (positive seqs 1..M)
  # all arrived through the translator, none lost, none duplicated. (The
  # switch count exceeding iperf's is explained -- and pinned here -- as
  # iperf's unanswered close phase: a barrage of distinct negative-seq FIN
  # datagrams; see nanuk_demo_siit.py.)
  RESP_LAST=$(grep -oE "unique=[0-9]+ dups=[0-9]+ pos=[0-9]+/[0-9]+" "$OUT/run-siit-iperf_udp.log" | tail -1)
  RESP_DUPS=$(echo "$RESP_LAST" | grep -oE "dups=[0-9]+" | cut -d= -f2)
  RESP_POS=$(echo "$RESP_LAST" | grep -oE "pos=[0-9]+/[0-9]+" | cut -d= -f2)
  [ -n "$RESP_LAST" ] || {
    echo "BEAT 2 (UDP) FAILED: no responder classification lines in the log"; exit 1; }
  [ "${RESP_DUPS:-1}" -eq 0 ] || {
    echo "BEAT 2 (UDP) FAILED: responder saw duplicated datagrams ($RESP_LAST) --"\
         "something on the path is duplicating frames"; exit 1; }
  [ "${RESP_POS%/*}" = "${RESP_POS#*/}" ] || {
    echo "BEAT 2 (UDP) FAILED: paced data datagrams lost in translation"\
         "($RESP_LAST: received/expected = $RESP_POS)"; exit 1; }
  # Conservation report (not gated: counters are read at slightly different
  # instants): guest driver TX  ==  switch translated + switch rx-queue-full
  # drops closes the books frame-for-frame when it holds.
  TX_PKTS=$(awk '/SIIT_SND_BEGIN/,/SIIT_SND_END/' "$OUT/run-siit-iperf_udp.log" \
            | grep -A1 "TX:" | grep -oE "^\[[^]]*\] +[0-9]+ +[0-9]+" | awk '{print $3}' | tail -2)
  QDROPS=$(grep -c "rx queue full" "$OUT/run-siit-iperf_udp.log" || true)
  echo "beat 2 ok: iperf sent $UDP_SENT datagrams, switch translated $UDP_TRANSLATED"\
       "(grew=${UDP_GREW:-0} - warmup=${WARMUP:-0} pings) v4->v6 -- reconciled >= 0.9x;"\
       "receiver: $RESP_LAST (zero dups, all paced data datagrams delivered)"
  echo "        conservation: guest TX $(echo $TX_PKTS | awk '{print $2-$1}') =="\
       "translated $UDP_TRANSLATED + queue-full $QDROPS + 0 unexplained"\
       "(surplus = iperf close-phase FIN barrage, distinct negative seqs)"
fi

# ---- Beat 3: negative gate, TTL=1 must be dropped ----
if [ -z "$ONLY" ] || [ "$ONLY" = ttl ]; then
  run_beat ttl
  grep -qE ", 100% packet loss" "$OUT/run-siit-ttl.log" || {
    echo "BEAT 3 FAILED: TTL=1 ping was NOT fully dropped (see $OUT/run-siit-ttl.log)"; exit 1; }
  echo "beat 3 ok: TTL=1 ping -> 100% loss (translator drops hop-limit<=1, no ICMP error)"
fi

# ---- Beat 4 (iperf_tcp): real kernel TCP across the boundary ----
# Needs the IPv6-enabled rebuild of the guest kernel (build_guest_kernel.sh;
# cached after the first build). The v6 guest terminates iperf TCP in its
# kernel; every segment of the connection -- SYN, data, ACKs, FIN -- crosses
# the translator, growing v4->v6 and shrinking v6->v4, so BOTH direction
# counters must run high.
if [ -z "$ONLY" ] || [ "$ONLY" = iperf_tcp ]; then
  "$SB/build_guest_kernel.sh"
  run_beat iperf_tcp
  grep -qE "Mbits/sec|Kbits/sec|bits/sec" "$OUT/run-siit-iperf_tcp.log" || {
    echo "BEAT 4 (TCP) FAILED: no iperf transfer (see $OUT/run-siit-iperf_tcp.log)"; exit 1; }
  TCP_GREW=$(grep -oE "grew=[0-9]+" "$OUT/run-siit-iperf_tcp.log" | tail -1 | cut -d= -f2)
  TCP_SHRUNK=$(grep -oE "shrunk=[0-9]+" "$OUT/run-siit-iperf_tcp.log" | tail -1 | cut -d= -f2)
  TCP_CORE_ERR=$(grep -oE "core_err=[0-9]+" "$OUT/run-siit-iperf_tcp.log" | tail -1 | cut -d= -f2)
  [ "${TCP_CORE_ERR:-1}" -eq 0 ] || {
    echo "BEAT 4 (TCP) FAILED: core errors (core_err=$TCP_CORE_ERR)"; exit 1; }
  # A TCP connection is bidirectional by construction: data v4->v6 (grew),
  # ACKs v6->v4 (shrunk). Both low means no real connection ran through the
  # translator, whatever the client printed.
  [ "${TCP_GREW:-0}" -ge 20 ] && [ "${TCP_SHRUNK:-0}" -ge 20 ] || {
    echo "BEAT 4 (TCP) FAILED: expected a bidirectional TCP stream through"\
         "the translator (grew=${TCP_GREW:-0} shrunk=${TCP_SHRUNK:-0}, need >= 20 each)"; exit 1; }
  TCP_BW=$(grep -oE "[0-9.]+ [KM]bits/sec" "$OUT/run-siit-iperf_tcp.log" | tail -1)
  echo "beat 4 ok: iperf TCP $TCP_BW through the translator -- kernel TCP/IPv6"\
       "terminated it (grew=$TCP_GREW v4->v6, shrunk=$TCP_SHRUNK v6->v4, core_err=0)"
fi

echo "SIIT BEATS PASSED"
