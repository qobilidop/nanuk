#!/usr/bin/env bash
set -euo pipefail

# Run a command inside the nanuk dev container.
#
# Usage:
#   ./dev.sh <command> [args...]
#
# Examples:
#   ./dev.sh sail --version
#   ./dev.sh cmake -S spec/sail -B spec/sail/build
#   ./dev.sh cmake --build spec/sail/build
#   ./dev.sh ctest --test-dir spec/sail/build
#   ./dev.sh bash              # interactive shell

devcontainer exec --workspace-folder . "$@"
