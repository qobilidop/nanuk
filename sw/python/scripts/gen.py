#!/usr/bin/env python3
"""Regenerate nanuk/ir/nanuk_ir_pb2.py from nanuk/ir/nanuk_ir.proto.

The generated file is checked in so nanuk needs no build-time protoc
dependency; rerun this (in an env with the dev group, e.g.
`uv run python scripts/gen.py` from sw/python/) after editing the schema.
"""

import sys
from pathlib import Path

from grpc_tools import protoc

PKG = Path(__file__).resolve().parents[1] / "nanuk" / "ir"


def main() -> int:
    return protoc.main(
        [
            "protoc",
            f"-I{PKG}",
            f"--python_out={PKG}",
            str(PKG / "nanuk_ir.proto"),
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
