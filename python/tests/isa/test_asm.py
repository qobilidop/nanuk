import pytest

from nanuk.isa import encoding
from nanuk.isa.asm import AsmError, assemble, assemble_with_lines


def words(binary: bytes) -> list[int]:
    assert len(binary) % 4 == 0
    return [int.from_bytes(binary[i : i + 4], "big") for i in range(0, len(binary), 4)]


def test_simple_program():
    binary = assemble("movi r0, 7\nhalt accept\n")
    assert words(binary) == [0x10000007, 0x2C000000]


def test_comments_case_and_hex():
    binary = assemble("; a comment\n  MOVI R1, 0x8100  ; trailing\n\nHALT DROP\n")
    assert words(binary) == [0x10808100, 0x2C000001]


def test_labels_forward_and_backward():
    src = """
loop:
    beq r0, r1, done
    jmp loop
done:
    halt accept
"""
    binary = assemble(src)
    assert words(binary) == [
        encoding.encode_beq("r0", "r1", 2),  # done = word 2
        encoding.encode_jmp(0),              # loop = word 0
        encoding.encode_halt(drop=False),
    ]


def test_label_on_instruction_line():
    binary = assemble("start: jmp start\n")
    assert words(binary) == [encoding.encode_jmp(0)]


def test_equ_constants():
    src = """
.equ h_eth 0
.equ ethertype_offset 96
    sethdr h_eth
    ext r0, ethertype_offset, 16
    halt accept
"""
    binary = assemble(src)
    assert words(binary)[0] == encoding.encode_sethdr(0)
    assert words(binary)[1] == encoding.encode_ext("r0", 96, 16)


def test_all_instructions_assemble():
    src = """
    ext r0, 96, 16
    advi 14
    advr r1
    movi r2, 0x86DD
    shl r1, r1, 2
    beq r0, r2, 7
    bne r0, r2, 7
    jmp 7
    sethdr 3
    stmd 0, r0, 3
    halt drop
"""
    assert len(words(assemble(src))) == 11


@pytest.mark.parametrize(
    ("src", "fragment"),
    [
        ("bogus r0", "unknown mnemonic"),
        ("jmp nowhere", "unknown symbol"),
        ("movi r9, 1", "unknown register"),
        ("movi r0", "expects 2 operand"),
        ("halt maybe", "accept or drop"),
        (".equ x", ".equ expects"),
        ("x: x: halt accept", "duplicate symbol"),
        ("movi r0, 0x10000", "does not fit"),
    ],
)
def test_errors_carry_line_numbers(src, fragment):
    with pytest.raises(AsmError) as exc:
        assemble(src)
    assert fragment in str(exc.value)
    assert "line 1" in str(exc.value)


def test_assemble_with_lines():
    src = "; comment\n.equ N 2\nstart:\n    advi N\n    halt accept\n"
    binary, lines = assemble_with_lines(src)
    assert binary == assemble(src)
    assert lines == [4, 5]
