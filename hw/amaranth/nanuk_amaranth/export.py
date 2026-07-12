"""Emit Verilog for the Nanuk core's processors.

Usage:
    uv run nanuk-export ../../demo/build/nanuk_pp.v
    uv run nanuk-export --processor map ../../demo/build/nanuk_map.v
"""

import argparse
import sys
from pathlib import Path

from amaranth.back import verilog

from nanuk_amaranth.map import MatchActionProcessor
from nanuk_amaranth.pp import ParserProcessor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "output", type=Path, help="output Verilog file (e.g. build/nanuk_pp.v)"
    )
    parser.add_argument(
        "--processor",
        choices=["pp", "map"],
        default="pp",
        help="which processor to export (default: pp)",
    )
    args = parser.parse_args(argv)

    if args.processor == "map":
        text = verilog.convert(MatchActionProcessor(), name="nanuk_map")
    else:
        text = verilog.convert(ParserProcessor(), name="nanuk_pp")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
