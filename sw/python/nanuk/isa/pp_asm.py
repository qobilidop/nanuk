"""Two-pass assembler for the Nanuk parser ISA v0.

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
    ldmd  rd, slot           ; metadata window slot (0..7)
    halt  accept|drop

Mnemonics and registers are case-insensitive. Integers are decimal or 0x-hex.
Output: big-endian 32-bit words, loaded at word 0, entry pc = 0.

(Parsing/symbol machinery shared with the MAP assembler: _asm_core.py.)
"""

import sys

from . import pp_encoding as encoding
from ._asm_core import (
    AsmError,
    expect,
    parse_lines,
    resolve_int,
    resolve_reg,
    run_cli,
    to_binary,
)

__all__ = ["AsmError", "assemble", "assemble_with_lines", "main"]


def assemble(text: str) -> bytes:
    """Assemble source text into big-endian 32-bit words."""
    return to_binary(_assemble_words(text)[0])


def assemble_with_lines(text: str) -> tuple[bytes, list[int]]:
    """Assemble, also returning the 1-based source line of every word."""
    words, program = _assemble_words(text)
    return to_binary(words), [line.lineno for line in program]


def _assemble_words(text: str):
    symbols, program = parse_lines(text)
    words: list[int] = []

    for line in program:
        ln, ops = line.lineno, line.operands

        def val(tok: str) -> int:
            return resolve_int(tok, symbols, ln)

        def reg(tok: str) -> str:
            return resolve_reg(tok, encoding.REGS, ln)

        try:
            match line.mnemonic:
                case "ext":
                    expect(line, 3)
                    word = encoding.encode_ext(reg(ops[0]), val(ops[1]), val(ops[2]))
                case "advi":
                    expect(line, 1)
                    word = encoding.encode_advi(val(ops[0]))
                case "advr":
                    expect(line, 1)
                    word = encoding.encode_advr(reg(ops[0]))
                case "movi":
                    expect(line, 2)
                    word = encoding.encode_movi(reg(ops[0]), val(ops[1]))
                case "shl":
                    expect(line, 3)
                    word = encoding.encode_shl(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "beq":
                    expect(line, 3)
                    word = encoding.encode_beq(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "bne":
                    expect(line, 3)
                    word = encoding.encode_bne(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "jmp":
                    expect(line, 1)
                    word = encoding.encode_jmp(val(ops[0]))
                case "sethdr":
                    expect(line, 1)
                    word = encoding.encode_sethdr(val(ops[0]))
                case "ldmd":
                    expect(line, 2)
                    word = encoding.encode_ldmd(reg(ops[0]), val(ops[1]))
                case "stmd":
                    expect(line, 3)
                    word = encoding.encode_stmd(val(ops[0]), reg(ops[1]), val(ops[2]))
                case "halt":
                    expect(line, 1)
                    mode = ops[0].lower()
                    if mode not in ("accept", "drop"):
                        raise AsmError(ln, f"halt expects accept or drop, got {ops[0]!r}")
                    word = encoding.encode_halt(mode == "drop")
                case _:
                    raise AsmError(ln, f"unknown mnemonic {line.mnemonic!r}")
        except ValueError as e:
            raise AsmError(ln, str(e)) from None

        words.append(word)

    return words, program


def main(argv: list[str] | None = None) -> int:
    return run_cli("nanuk-pp-asm", __doc__.splitlines()[0], assemble, argv)


if __name__ == "__main__":
    sys.exit(main())
