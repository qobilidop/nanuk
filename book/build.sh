#!/usr/bin/env bash
set -euo pipefail

# Build the book with the pinned mdBook. This is THE version pin — pages.yml
# delegates here, so local and CI builds cannot diverge (book.toml has no
# native pin field). The binary is provisioned into gitignored book/bin/
# (same category as node_modules: a staged input, not a build output —
# book/build/ itself is wiped by every mdbook run).

MDBOOK_VERSION=v0.4.52

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HERE/bin"
MDBOOK="$BIN_DIR/mdbook-$MDBOOK_VERSION"

if [ ! -x "$MDBOOK" ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)  target=x86_64-unknown-linux-gnu ;;
    Darwin-arm64)  target=aarch64-apple-darwin ;;
    Darwin-x86_64) target=x86_64-apple-darwin ;;
    *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
  esac
  mkdir -p "$BIN_DIR"
  curl -sSL "https://github.com/rust-lang/mdBook/releases/download/${MDBOOK_VERSION}/mdbook-${MDBOOK_VERSION}-${target}.tar.gz" \
    | tar -xz -C "$BIN_DIR"
  mv "$BIN_DIR/mdbook" "$MDBOOK"
fi

exec "$MDBOOK" build "$HERE" "$@"
