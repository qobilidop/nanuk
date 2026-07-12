"""MAP assembler tests: label resolution (including LOOKUP miss targets),
negative offsets/deltas, h_frame predefined symbol, and a full assembly of
the L2-forward demo against hand-encoded words."""

import pytest

from nanuk_isa import map_encoding as e
from nanuk_isa.map_asm import AsmError, assemble


def words(binary: bytes) -> list[int]:
    assert len(binary) % 4 == 0
    return [int.from_bytes(binary[i : i + 4], "big") for i in range(0, len(binary), 4)]


def test_l2fwd_demo_assembles_to_golden_words():
    src = """
    ; L2 forward: exact-match on DMAC -> port bitmap; miss -> flood.
    .equ H_ETH 0
    .equ T_L2 0
    .equ MD_FLOOD 9
        ld      r0, H_ETH, 0, 6
        lookup  r1, T_L2, r0, miss
        send    r1, 0
    miss:
        ldmd    r1, MD_FLOOD
        send    r1, 0
    """
    assert words(assemble(src)) == [
        e.encode_ld("r0", 0, 0, 6),
        e.encode_lookup("r1", 0, "r0", 3),
        e.encode_send("r1", 0),
        e.encode_ldmd("r1", 9),
        e.encode_send("r1", 0),
    ]


def test_negative_offsets_and_deltas():
    src = """
        st      r0, h_frame, -22, 6
        send    r1, -22
    """
    assert words(assemble(src)) == [
        e.encode_st("r0", 15, -22, 6),
        e.encode_send("r1", -22),
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
        assemble("send r0, 1000")  # delta out of signed range
