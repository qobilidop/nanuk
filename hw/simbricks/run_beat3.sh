#!/usr/bin/env bash
# M2 beat 3: ping through two nanuk switches doing nanukproto tunnel
# encap/decap on the wire between them.
#
#   Phase A (flood): no tunnel table -> push misses everything -> plain
#                    flood end to end; harvest host1's MAC from sw_encap's
#                    dmac debug (unicast dmac arriving on encap port 0).
#   Phase B (tunnel): tunnel table maps host1's MAC -> encap port 1;
#                    ping must pass AND sw_encap stats show delta_pos>0
#                    (frames encapsulated) AND sw_decap delta_neg>0
#                    (frames decapsulated).
#
# Run from anywhere: hw/simbricks/run_beat3.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
REPO="$PWD"
IMG=simbricks/simbricks-local:latest
SB="$REPO/hw/simbricks"
OUT="$SB/out"

"$SB/build_component.sh"

echo "==> assembling programs (encap + decap pairs)"
./dev.sh bash -lc '
    cd python && uv sync --quiet &&
    uv run nanuk-asm nanuk/examples/l2l3l4/parse.asm -o ../hw/simbricks/out/encap-prog.bin &&
    uv run nanuk-map-asm nanuk/examples/nanukproto/tunnel_push.asm -o ../hw/simbricks/out/encap-map.bin &&
    uv run nanuk-asm nanuk/examples/nanukproto/parse_tunnel.asm -o ../hw/simbricks/out/decap-prog.bin &&
    uv run nanuk-map-asm nanuk/examples/nanukproto/tunnel_pop.asm -o ../hw/simbricks/out/decap-map.bin
'

run_phase() {  # $1 = phase; encap-tables.txt optionally staged in $OUT
  local phase="$1"
  echo "==> running tunnel phase $phase"
  docker run --rm --platform linux/amd64 \
    -v "$REPO:/nanuk:ro" -v "$OUT:/out" \
    $IMG bash -ec '
      D=/simbricks/sims/net/nanuk
      mkdir -p $D/encap $D/decap
      cp /out/nanuk_hw /nanuk/hw/simbricks/nanuk_run.sh \
         /nanuk/hw/simbricks/nanuk_demo_tunnel.py $D/
      cp /out/encap-prog.bin $D/encap/prog.bin
      cp /out/encap-map.bin  $D/encap/map.bin
      cp /out/decap-prog.bin $D/decap/prog.bin
      cp /out/decap-map.bin  $D/decap/map.bin
      [ -f /out/encap-tables.txt ] && cp /out/encap-tables.txt $D/encap/tables.txt
      printf "#!/bin/sh\nNANUK_DIR=%s exec %s/nanuk_run.sh \"\$@\"\n" $D/encap $D > $D/nanuk_run_encap.sh
      printf "#!/bin/sh\nNANUK_DIR=%s exec %s/nanuk_run.sh \"\$@\"\n" $D/decap $D > $D/nanuk_run_decap.sh
      chmod +x $D/nanuk_run.sh $D/nanuk_run_encap.sh $D/nanuk_run_decap.sh
      cd /out
      python3 -m simbricks.local $D/nanuk_demo_tunnel.py \
          --verbose --force --repo /simbricks --workdir /out/work-tunnel 2>&1
    ' | tee "$OUT/run-tunnel-$phase.log" > /dev/null
}

ping_ok() { grep -qE ", 0% packet loss" "$OUT/run-tunnel-$1.log"; }
max_stat() {  # $1 phase, $2 field — max across both switches' stats lines
  grep -oE "$2=[0-9]+" "$OUT/run-tunnel-$1.log" | cut -d= -f2 | sort -n | tail -1
}

# ---- Phase A: no tunnel table (plain flood both hops) ----
rm -f "$OUT/encap-tables.txt"
run_phase A
ping_ok A || { echo "TUNNEL PHASE A FAILED: no clean ping via flood"; exit 1; }
HOST1_MAC="02:6e:61:00:00:02"   # pinned in nanuk_demo_tunnel.py
grep -q "dmac $HOST1_MAC" "$OUT/run-tunnel-A.log" || {
  echo "TUNNEL PHASE A FAILED: pinned host1 MAC never seen"; exit 1; }
echo "phase A ok (flood); host1 mac = $HOST1_MAC"

# ---- Phase B: tunnel host1-bound traffic ----
cat > "$OUT/encap-tables.txt" <<EOF
table 1 48 8
entry 1 0x${HOST1_MAC//:/} 0x2
EOF
run_phase B
ping_ok B || { echo "TUNNEL PHASE B FAILED: no clean ping through the tunnel"; exit 1; }
# Encap is the only producer of delta_pos, decap the only one of delta_neg,
# so the max across both switches' stats lines attributes correctly.
ENC=$(max_stat B delta_pos)
DEC=$(max_stat B delta_neg)
[ "${ENC:-0}" -gt 0 ] || { echo "TUNNEL PHASE B FAILED: sw_encap never encapsulated (delta_pos=${ENC:-?})"; exit 1; }
[ "${DEC:-0}" -gt 0 ] || { echo "TUNNEL PHASE B FAILED: sw_decap never decapsulated (delta_neg=${DEC:-?})"; exit 1; }
echo "phase B ok: ping rode the tunnel (encap delta_pos=$ENC, decap delta_neg=$DEC)"

echo "BEAT 3 PASSED"
