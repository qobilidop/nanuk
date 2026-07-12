"""MAP assembler tests: label resolution (including LOOKUP miss targets),
negative offsets/deltas, h_frame predefined symbol, and a full assembly of
the L2-forward demo against hand-encoded words."""

import pytest

from nanuk.isa import map_encoding as e
from nanuk.isa.map_asm import AsmError, assemble, assemble_with_lines


def words(binary: bytes) -> list[int]:
    assert len(binary) % 4 == 0
    return [int.from_bytes(binary[i : i + 4], "big") for i in range(0, len(binary), 4)]


def test_l2fwd_demo_assembles_to_golden_words():
    src = """
    ; L2 forward: exact-match on DMAC -> port bitmap; miss -> flood table.
    .equ H_ETH 0
    .equ T_L2 0
    .equ T_SYS 3
        ld      r0, H_ETH, 0, 6
        lookup  r1, T_L2, r0, miss
        stmd    r1, 1, 0
        send    0
    miss:
        ldmd    r2, 0
        lookup  r1, T_SYS, r2, dark
        stmd    r1, 1, 0
        send    0
    dark:
        drop
    """
    assert words(assemble(src)) == [
        e.encode_ld("r0", 0, 0, 6),
        e.encode_lookup("r1", 0, "r0", 4),
        e.encode_stmd("r1", 1, 0),
        e.encode_send(0),
        e.encode_ldmd("r2", 0),
        e.encode_lookup("r1", 3, "r2", 8),
        e.encode_stmd("r1", 1, 0),
        e.encode_send(0),
        e.encode_drop(),
    ]


def test_csum_sequence_assembles():
    src = """
        ld      r2, 1, 0, 1
        andi    r2, r2, 0x000F
        shli    r2, r2, 2
        csum    r3, 1, 0, r2
        st      r3, 1, 10, 2
    """
    assert words(assemble(src)) == [
        e.encode_ld("r2", 1, 0, 1),
        e.encode_andi("r2", "r2", 0x000F),
        e.encode_shli("r2", "r2", 2),
        e.encode_csum("r3", 1, 0, "r2"),
        e.encode_st("r3", 1, 10, 2),
    ]


def test_negative_offsets_and_deltas():
    src = """
        st      r0, h_frame, -22, 6
        send    -22
    """
    assert words(assemble(src)) == [
        e.encode_st("r0", 15, -22, 6),
        e.encode_send(-22),
    ]


def test_h_frame_predefined():
    assert words(assemble("ld r0, h_frame, 8, 1")) == [e.encode_ld("r0", 15, 8, 1)]


def test_addi_negative_immediate():
    assert words(assemble("addi r0, r0, -1")) == [e.encode_addi("r0", "r0", -1)]


def test_backward_and_forward_labels():
    src = """
    top:
        bne r0, r1, done
        jmp top
    done:
        drop
    """
    assert words(assemble(src)) == [
        e.encode_bne("r0", "r1", 2),
        e.encode_jmp(0),
        e.encode_drop(),
    ]


def test_errors():
    with pytest.raises(AsmError):
        assemble("ld r0, H_MISSING, 0, 1")  # unknown symbol
    with pytest.raises(AsmError):
        assemble("drop 1")  # operand count
    with pytest.raises(AsmError):
        assemble("frobnicate r0")  # unknown mnemonic
    with pytest.raises(AsmError):
        assemble("send 1000")  # delta out of signed range
    with pytest.raises(AsmError):
        assemble("csumupd 2, 0")  # retired mnemonic
    with pytest.raises(AsmError):
        assemble("stmd r0, 5, 0")  # nunits out of range


def test_assemble_with_lines():
    src = "top:\n    movi r0, 1\n    drop\n"
    binary, lines = assemble_with_lines(src)
    assert binary == assemble(src)
    assert lines == [2, 3]
