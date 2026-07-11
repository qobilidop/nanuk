"""Emit Verilog for the nanuk core.

Usage: python export.py build/nanuk_core.v
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from amaranth.back import verilog

from nanuk_hw.core import NanukCore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "output", type=Path, help="output Verilog file (e.g. build/nanuk_core.v)"
    )
    args = parser.parse_args(argv)

    text = verilog.convert(NanukCore(), name="nanuk_core")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
