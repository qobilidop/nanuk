#!/usr/bin/env bash
# M2 beats 1+2: the table is the forwarding policy.
#
#   Phase A (flood):    empty tables -> ping works via flood-on-miss;
#                       harvest the hosts' MACs from the component's
#                       first-seen-DMAC debug lines.
#   Phase B (unicast):  tables map both MACs to their ports -> ping works
#                       AND flooded=0 in the stats (pure unicast).
#   Phase C (flip):     host1's MAC deliberately mapped to the WRONG port
#                       -> ping must fail. Same silicon, same programs;
#                       only the table changed.
#
# Run from anywhere: demo/run_beats12.sh
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
IMG=simbricks/simbricks-local:latest
SB="$REPO/demo"
OUT="$SB/out"

"$SB/build_component.sh"

echo "==> assembling programs"
./dev.sh bash -lc '
    cd sw/python && uv sync --quiet &&
    uv run nanuk-asm ../../examples/l2l3l4/parse.asm -o ../../demo/out/prog.bin &&
    uv run nanuk-map-asm ../../examples/map_l2fwd/fwd.asm -o ../../demo/out/map.bin
'

run_phase() {  # $1 = phase name; tables.txt (or absence) already staged in $OUT
  local phase="$1"
  echo "==> running phase $phase"
  rm -f "$OUT/run-$phase.log"
  docker run --rm --platform linux/amd64 \
    -v "$REPO:/nanuk:ro" -v "$OUT:/out" \
    $IMG bash -ec '
      mkdir -p /simbricks/sims/net/nanuk
      cp /out/nanuk_switch /nanuk/demo/nanuk_run.sh \
         /nanuk/demo/nanuk_demo.py /out/prog.bin /out/map.bin \
         /simbricks/sims/net/nanuk/
      [ -f /out/tables.txt ] && cp /out/tables.txt /simbricks/sims/net/nanuk/
      chmod +x /simbricks/sims/net/nanuk/nanuk_run.sh
      cd /out
      python3 -m simbricks.local /simbricks/sims/net/nanuk/nanuk_demo.py \
          --verbose --force --repo /simbricks --workdir /out/work 2>&1
    ' | tee "$OUT/run-$phase.log" > /dev/null
}

ping_ok() { grep -qE ", 0% packet loss" "$OUT/run-$1.log"; }
ping_dead() { grep -qE ", 100% packet loss" "$OUT/run-$1.log"; }
stat_field() { grep -oE "$2=[0-9]+" "$OUT/run-$1.log" | tail -1 | cut -d= -f2; }

# ---- Phase A: flood ----
rm -f "$OUT/tables.txt"
run_phase A
ping_ok A || { echo "PHASE A FAILED: no clean ping via flood"; exit 1; }
echo "phase A ok: ping via flood-on-miss"

# NIC MACs are pinned in nanuk_demo.py (QEMU randomizes them otherwise).
MAC_PORT0="02:6e:61:00:00:01"
MAC_PORT1="02:6e:61:00:00:02"
# Sanity: the pinned MACs must actually appear on the wire in phase A.
grep -q "dmac $MAC_PORT0" "$OUT/run-A.log" || {
  echo "PHASE A FAILED: pinned MAC $MAC_PORT0 never seen"; exit 1; }
grep -q "dmac $MAC_PORT1" "$OUT/run-A.log" || {
  echo "PHASE A FAILED: pinned MAC $MAC_PORT1 never seen"; exit 1; }
mac_to_hex() { echo "0x${1//:/}"; }
echo "pinned MACs confirmed on the wire"

# ---- Phase B: unicast ----
cat > "$OUT/tables.txt" <<EOF
table 0 48 8
entry 0 $(mac_to_hex "$MAC_PORT0") 0x1
entry 0 $(mac_to_hex "$MAC_PORT1") 0x2
EOF
run_phase B
ping_ok B || { echo "PHASE B FAILED: no clean ping with unicast tables"; exit 1; }
# Broadcast ARP (and any IPv6 ND multicast) legitimately floods; the bulk —
# every ICMP echo — must be table-driven unicast.
FLOODED=$(stat_field B flooded)
SENT=$(stat_field B sent)
[ "${FLOODED:-99}" -le 4 ] || {
  echo "PHASE B FAILED: flooded=$FLOODED (> 4; unicast not working)"; exit 1; }
[ "$((SENT - FLOODED))" -ge 10 ] || {
  echo "PHASE B FAILED: only $((SENT - FLOODED)) unicast frames"; exit 1; }
echo "phase B ok: unicast by table (sent=$SENT flooded=$FLOODED)"

# ---- Phase C: policy flip ----
cat > "$OUT/tables.txt" <<EOF
table 0 48 8
entry 0 $(mac_to_hex "$MAC_PORT0") 0x1
entry 0 $(mac_to_hex "$MAC_PORT1") 0x1
EOF
run_phase C
ping_dead C || { echo "PHASE C FAILED: ping survived a wrong-port table"; exit 1; }
echo "phase C ok: wrong-port table kills the ping — the table IS the policy"

echo "BEATS 1+2 PASSED"
