#!/usr/bin/env bash
# Emulator CLI smoke test: {MOVI r0, 7; HALT accept} on an empty packet.
set -euo pipefail

EMU="$1"
DIR="$(mktemp -d)"
trap 'rm -rf "$DIR"' EXIT

# 0x10000007 = MOVI r0, 7 ; 0x2C000000 = HALT accept
printf '\x10\x00\x00\x07\x2c\x00\x00\x00' > "$DIR/prog.bin"
: > "$DIR/pkt.bin"

OUT="$("$EMU" "$DIR/prog.bin" "$DIR/pkt.bin")"
echo "$OUT"

echo "$OUT" | grep -q '"verdict": 0'
echo "$OUT" | grep -q '"error": 0'
echo "$OUT" | grep -q '"steps": 2'
echo "emu_smoke: OK"
