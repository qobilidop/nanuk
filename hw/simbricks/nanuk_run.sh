#!/bin/sh
# Wrapper launched by the SimBricks orchestration as the switch executable.
# Appends the program/table arguments to the argv the orchestrator builds
# (sockets, sync flags). NANUK_DIR selects a per-switch directory of
# prog.bin / map.bin / tables.txt (defaults to this script's directory);
# NANUK_PROG / NANUK_MAP_PROG / NANUK_TABLES override individual files.
BIN="$(dirname "$0")/nanuk_hw"
DIR="${NANUK_DIR:-$(dirname "$0")}"
TABLES="${NANUK_TABLES:-$DIR/tables.txt}"
if [ -f "$TABLES" ]; then
    exec "$BIN" "$@" -f "${NANUK_PROG:-$DIR/prog.bin}" \
        -m "${NANUK_MAP_PROG:-$DIR/map.bin}" -t "$TABLES"
else
    exec "$BIN" "$@" -f "${NANUK_PROG:-$DIR/prog.bin}" \
        -m "${NANUK_MAP_PROG:-$DIR/map.bin}"
fi
