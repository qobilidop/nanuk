#!/usr/bin/env python3
"""Regenerate nanuk/ir/nanuk_ir_pb2.py from the IR schema in spec/proto/.

The schema's source of truth is spec/proto/ (language-neutral, shared by
every implementation); this package vendors the generated code so nanuk
needs no build-time protoc dependency. The include path is the schema's
leaf directory on purpose: gencode lands flat in nanuk/ir/, keeping the
import path (nanuk.ir.nanuk_ir_pb2) a per-language detail rather than a
mirror of the proto package. Rerun (in an env with the dev group, e.g.
`uv run python scripts/gen.py` from sw/python/) after editing the schema.
"""

import sys
from pathlib import Path

from grpc_tools import protoc

_HERE = Path(__file__).resolve()
SCHEMA = _HERE.parents[3] / "spec" / "proto" / "nanuk" / "ir" / "v0"
PKG = _HERE.parents[1] / "nanuk" / "ir"


def main() -> int:
    return protoc.main(
        [
            "protoc",
            f"-I{SCHEMA}",
            f"--python_out={PKG}",
            str(SCHEMA / "nanuk_ir.proto"),
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
