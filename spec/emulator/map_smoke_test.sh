#!/usr/bin/env bash
# Smoke test for nanuk-map-emu: MOVI r0, 0xF; SEND r0, 0 over a 4-byte frame.
# Words hand-encoded from the M1 plan's table: 0x1000000F, 0x2C000000.
set -euo pipefail

EMU="$1"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

printf '\x10\x00\x00\x0f\x2c\x00\x00\x00' > "$TMP/prog.bin"
printf '\xde\xad\xbe\xef' > "$TMP/pkt.bin"
cat > "$TMP/ctx.txt" <<EOF
ingress 1
EOF

OUT=$("$EMU" "$TMP/prog.bin" "$TMP/pkt.bin" "$TMP/ctx.txt")
echo "$OUT"

EXPECTED='{"verdict": 0, "error": 0, "egress": 15, "delta": 0, "steps": 2, "frame": "deadbeef"}'
if [ "$OUT" != "$EXPECTED" ]; then
    echo "map_smoke_test: FAIL"
    echo "expected: $EXPECTED"
    exit 1
fi
echo "map_smoke_test: PASS"
