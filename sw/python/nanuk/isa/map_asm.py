"""Two-pass assembler for the Nanuk MAP ISA v0.

Syntax:
    ; comment
    .equ NAME VALUE          ; named constant, usable as any immediate
    label:                   ; word address of the next instruction
    ld    rd, hdr, off, n    ; hdr base id, signed byte offset, size in bytes
    st    rs, hdr, off, n
    ldmd  rd, field          ; inbound-SMD field id
    movi  rd, imm16
    addi  rd, rs, imm        ; imm may be negative (sign-extended)
    beq   rs, rt, label
    bne   rs, rt, label
    jmp   label
    lookup rd, table, rs, label   ; miss branches to label
    csumupd hdr, off
    send  rs, delta          ; delta may be negative (strip)
    drop

Mnemonics and registers are case-insensitive. Integers are decimal or 0x-hex.
`h_frame` is predefined as symbol 15 (the always-valid frame-start base).
Output: big-endian 32-bit words, loaded at word 0, entry pc = 0.

(Parsing/symbol machinery shared with the parser assembler: _asm_core.py.)
"""

import sys

from . import map_encoding as encoding
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

_PREDEFINED = {"h_frame": encoding.H_FRAME}


def assemble(text: str) -> bytes:
    """Assemble source text into big-endian 32-bit words."""
    return to_binary(_assemble_words(text)[0])


def assemble_with_lines(text: str) -> tuple[bytes, list[int]]:
    """Assemble, also returning the 1-based source line of every word."""
    words, program = _assemble_words(text)
    return to_binary(words), [line.lineno for line in program]


def _assemble_words(text: str):
    symbols, program = parse_lines(text, _PREDEFINED)
    words: list[int] = []

    for line in program:
        ln, ops = line.lineno, line.operands

        def val(tok: str) -> int:
            return resolve_int(tok, symbols, ln, fold_case=True)

        def reg(tok: str) -> str:
            return resolve_reg(tok, encoding.REGS, ln)

        try:
            match line.mnemonic:
                case "ld":
                    expect(line, 4)
                    word = encoding.encode_ld(reg(ops[0]), val(ops[1]), val(ops[2]), val(ops[3]))
                case "st":
                    expect(line, 4)
                    word = encoding.encode_st(reg(ops[0]), val(ops[1]), val(ops[2]), val(ops[3]))
                case "ldmd":
                    expect(line, 2)
                    word = encoding.encode_ldmd(reg(ops[0]), val(ops[1]))
                case "movi":
                    expect(line, 2)
                    word = encoding.encode_movi(reg(ops[0]), val(ops[1]))
                case "addi":
                    expect(line, 3)
                    word = encoding.encode_addi(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "beq":
                    expect(line, 3)
                    word = encoding.encode_beq(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "bne":
                    expect(line, 3)
                    word = encoding.encode_bne(reg(ops[0]), reg(ops[1]), val(ops[2]))
                case "jmp":
                    expect(line, 1)
                    word = encoding.encode_jmp(val(ops[0]))
                case "lookup":
                    expect(line, 4)
                    word = encoding.encode_lookup(
                        reg(ops[0]), val(ops[1]), reg(ops[2]), val(ops[3])
                    )
                case "csumupd":
                    expect(line, 2)
                    word = encoding.encode_csumupd(val(ops[0]), val(ops[1]))
                case "send":
                    expect(line, 2)
                    word = encoding.encode_send(reg(ops[0]), val(ops[1]))
                case "drop":
                    expect(line, 0)
                    word = encoding.encode_drop()
                case _:
                    raise AsmError(ln, f"unknown mnemonic {line.mnemonic!r}")
        except ValueError as e:
            raise AsmError(ln, str(e)) from None

        words.append(word)

    return words, program


def main(argv: list[str] | None = None) -> int:
    return run_cli("nanuk-map-asm", __doc__.splitlines()[0], assemble, argv)


if __name__ == "__main__":
    sys.exit(main())
