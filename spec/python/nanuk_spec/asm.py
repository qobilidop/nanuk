"""Two-pass assembler for the nanuk parser ISA v0.

Syntax:
    ; comment
    .equ NAME VALUE          ; named constant, usable as any immediate
    label:                   ; word address of the next instruction
    ext   rd, boff, bsize    ; bit offset from cursor, size in bits (1..64)
    advi  imm                ; advance cursor by imm bytes
    advr  rs                 ; advance cursor by rs[15:0] bytes
    movi  rd, imm16
    shl   rd, rs, shamt
    beq   rs, rt, label
    bne   rs, rt, label
    jmp   label
    sethdr id
    stmd  slot, rs, nunits   ; write low nunits*16 bits of rs, MSB-first
    halt  accept|drop

Mnemonics and registers are case-insensitive. Integers are decimal or 0x-hex.
Output: big-endian 32-bit words, loaded at word 0, entry pc = 0.
"""

import argparse
import re
import sys
from pathlib import Path

from . import encoding

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AsmError(Exception):
    def __init__(self, lineno: int, message: str):
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno


class _Line:
    def __init__(self, lineno: int, mnemonic: str, operands: list[str]):
        self.lineno = lineno
        self.mnemonic = mnemonic
        self.operands = operands


def _parse_lines(text: str):
    """First pass: strip comments, collect .equ constants and labels,
    and assign a word address to every instruction."""
    symbols: dict[str, int] = {}
    program: list[_Line] = []

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
        program.append(_Line(lineno, mnemonic, operands))

    return symbols, program


def _expect(line: _Line, n: int):
    if len(line.operands) != n:
        raise AsmError(
            line.lineno,
            f"{line.mnemonic} expects {n} operand(s), got {len(line.operands)}",
        )


def _resolve_int(tok: str, symbols: dict[str, int], lineno: int) -> int:
    try:
        return int(tok, 0)
    except ValueError:
        pass
    if tok in symbols:
        return symbols[tok]
    raise AsmError(lineno, f"unknown symbol {tok!r}")


def _resolve_reg(tok: str, lineno: int) -> str:
    reg = tok.lower()
    if reg not in encoding.REGS:
        raise AsmError(lineno, f"unknown register {tok!r}")
    return reg


def assemble(text: str) -> bytes:
    """Assemble source text into big-endian 32-bit words."""
    symbols, program = _parse_lines(text)
    words: list[int] = []

    for line in program:
        ln, ops = line.lineno, line.operands

        def val(tok: str) -> int:
            return _resolve_int(tok, symbols, ln)

        def reg(tok: str) -> str:
            return _resolve_reg(tok, ln)

        try:
            match line.mnemonic:
                case "ext":
                    _expect(line, 3)
                    word = encoding.encode_ext(reg(ops[0]), val(ops[1]), val(ops[2]))
                case "advi":
                    _expect(line, 1)
                    word = encoding.encode_advi(val(ops[0]))
                case "advr":
                    _expect(line, 1)
                    word = encoding.encode_advr(reg(ops[0]))
                case "movi":
                    _expect(line, 2)
                    word = encoding.encode_movi(reg(ops[0]), val(ops[1]))
                case "shl":
                    _expect(line, 3)
                    word = encoding.encode_shl(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "beq":
                    _expect(line, 3)
                    word = encoding.encode_beq(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "bne":
                    _expect(line, 3)
                    word = encoding.encode_bne(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "jmp":
                    _expect(line, 1)
                    word = encoding.encode_jmp(val(ops[0]))
                case "sethdr":
                    _expect(line, 1)
                    word = encoding.encode_sethdr(val(ops[0]))
                case "stmd":
                    _expect(line, 3)
                    word = encoding.encode_stmd(val(ops[0]), reg(ops[1]), val(ops[2]))
                case "halt":
                    _expect(line, 1)
                    mode = ops[0].lower()
                    if mode not in ("accept", "drop"):
                        raise AsmError(ln, f"halt expects accept or drop, got {ops[0]!r}")
                    word = encoding.encode_halt(mode == "drop")
                case _:
                    raise AsmError(ln, f"unknown mnemonic {line.mnemonic!r}")
        except ValueError as e:
            raise AsmError(ln, str(e)) from None

        words.append(word)

    return b"".join(w.to_bytes(4, "big") for w in words)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nanuk-asm", description=__doc__.splitlines()[0])
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


if __name__ == "__main__":
    sys.exit(main())
