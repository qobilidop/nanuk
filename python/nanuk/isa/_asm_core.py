"""Shared two-pass assembler machinery for the two nanuk ISAs.

asm.py (parser) and map_asm.py (MAP) own their mnemonic tables and CLIs;
this module owns everything ISA-independent: comment/label/.equ parsing,
symbol resolution, operand-count checking, and word emission. Error message
formats are pinned by both test suites — change them deliberately.
"""

import re
from pathlib import Path

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AsmError(Exception):
    def __init__(self, lineno: int, message: str):
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno


class Line:
    def __init__(self, lineno: int, mnemonic: str, operands: list[str]):
        self.lineno = lineno
        self.mnemonic = mnemonic
        self.operands = operands


def parse_lines(text: str, predefined: dict[str, int] | None = None):
    """First pass: strip comments, collect .equ constants and labels,
    and assign a word address to every instruction."""
    symbols: dict[str, int] = dict(predefined or {})
    program: list[Line] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue

        if line.lower().startswith(".equ"):
            parts = line.split()
            if len(parts) != 3:
                raise AsmError(lineno, ".equ expects: .equ NAME VALUE")
            name = parts[1]
            if not _LABEL_RE.match(name):
                raise AsmError(lineno, f"bad .equ name {name!r}")
            if name in symbols:
                raise AsmError(lineno, f"duplicate symbol {name!r}")
            try:
                symbols[name] = int(parts[2], 0)
            except ValueError:
                raise AsmError(lineno, f"bad .equ value {parts[2]!r}") from None
            continue

        while ":" in line:
            label, rest = line.split(":", 1)
            label = label.strip()
            if not _LABEL_RE.match(label):
                raise AsmError(lineno, f"bad label {label!r}")
            if label in symbols:
                raise AsmError(lineno, f"duplicate symbol {label!r}")
            symbols[label] = len(program)
            line = rest.strip()
        if not line:
            continue

        parts = line.split(None, 1)
        mnemonic = parts[0].lower()
        operands = []
        if len(parts) > 1:
            operands = [op.strip() for op in parts[1].split(",")]
            if any(not op for op in operands):
                raise AsmError(lineno, "empty operand")
        program.append(Line(lineno, mnemonic, operands))

    return symbols, program


def expect(line: Line, n: int) -> None:
    if len(line.operands) != n:
        raise AsmError(
            line.lineno,
            f"{line.mnemonic} expects {n} operand(s), got {len(line.operands)}",
        )


def resolve_int(
    tok: str, symbols: dict[str, int], lineno: int, *, fold_case: bool = False
) -> int:
    try:
        return int(tok, 0)
    except ValueError:
        pass
    if fold_case and tok.lower() in symbols:
        return symbols[tok.lower()]
    if tok in symbols:
        return symbols[tok]
    raise AsmError(lineno, f"unknown symbol {tok!r}")


def resolve_reg(tok: str, regs: dict[str, int], lineno: int) -> str:
    reg = tok.lower()
    if reg not in regs:
        raise AsmError(lineno, f"unknown register {tok!r}")
    return reg


def to_binary(words: list[int]) -> bytes:
    return b"".join(w.to_bytes(4, "big") for w in words)


def run_cli(prog_name: str, doc_first_line: str, assemble, argv) -> int:
    """The shared -o CLI both assemblers expose via python -m / scripts."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog=prog_name, description=doc_first_line)
    parser.add_argument("input", type=Path, help="assembly source file")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output binary")
    args = parser.parse_args(argv)

    try:
        binary = assemble(args.input.read_text())
    except AsmError as e:
        print(f"{args.input}:{e}", file=sys.stderr)
        return 1
    args.output.write_bytes(binary)
    return 0
