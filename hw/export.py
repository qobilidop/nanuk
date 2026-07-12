"""Emit Verilog for the nanuk cores.

Usage (env: the python/ project with the rtl extra):
    uv run --project ../python python export.py build/nanuk_core.v
    uv run --project ../python python export.py --core map build/nanuk_map_core.v
"""

import argparse
import sys
from pathlib import Path

from amaranth.back import verilog

from nanuk.rtl.core import NanukCore
from nanuk.rtl.map_core import MapCore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "output", type=Path, help="output Verilog file (e.g. build/nanuk_core.v)"
    )
    parser.add_argument(
        "--core",
        choices=["parser", "map"],
        default="parser",
        help="which core to export (default: parser)",
    )
    args = parser.parse_args(argv)

    if args.core == "map":
        text = verilog.convert(MapCore(), name="nanuk_map_core")
    else:
        text = verilog.convert(NanukCore(), name="nanuk_core")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
