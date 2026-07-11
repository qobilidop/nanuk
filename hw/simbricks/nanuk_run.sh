#!/bin/sh
# Wrapper launched by the SimBricks orchestration as the switch executable.
# Appends the parser program to the argv the orchestrator builds (sockets,
# sync flags). NANUK_PROG overrides the default demo program.
exec "$(dirname "$0")/nanuk_hw" "$@" -f "${NANUK_PROG:-$(dirname "$0")/prog.bin}"
