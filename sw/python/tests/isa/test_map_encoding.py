"""Golden-word tests for the MAP encoding mirror.

The constants here are the SAME words pinned in
spec/map-test/test_map_decode.sail — duplicated by intent: this pair of
tests is the Sail<->Python drift tripwire.
"""

import pytest

from nanuk.isa import map_encoding as e


def test_golden_words():
    assert e.encode_ld("r0", e.H_FRAME, 8, 1) == 0x04781000
    assert e.encode_st("r1", 2, -22, 6) == 0x0897D540
    assert e.encode_ldmd("r2", 9) == 0x0D480000
    assert e.encode_movi("r3", 0x8100) == 0x11808100
    assert e.encode_addi("r0", "r0", -1) == 0x1400FFFF
    assert e.encode_beq("r0", "r1", 7) == 0x18100007
    assert e.encode_bne("r2", "rz", 3) == 0x1D400003
    assert e.encode_jmp(2) == 0x20000002
    assert e.encode_lookup("r1", 0, "r0", 5) == 0x24800005
    assert e.encode_lookup("r1", 3, "r2", 4) == 0x249A0004
    assert e.encode_csumupd(2, 0) == 0x28800000
    assert e.encode_csumupd(e.H_FRAME, -4) == 0x2BFFC000
    assert e.encode_send("r1", 22) == 0x2C82C000
    assert e.encode_send("r0", -22) == 0x2C7D4000
    assert e.encode_drop() == 0x30000000


def test_addi_signed_range():
    assert e.encode_addi("r1", "r2", 0xFFFF) == e.encode_addi("r1", "r2", -1)
    with pytest.raises(ValueError):
        e.encode_addi("r0", "r0", -32769)
    with pytest.raises(ValueError):
        e.encode_addi("r0", "r0", 0x10000)


def test_field_range_rejections():
    with pytest.raises(ValueError):
        e.encode_ld("r0", 0, 512, 1)  # off > 511
    with pytest.raises(ValueError):
        e.encode_ld("r0", 0, -513, 1)  # off < -512
    with pytest.raises(ValueError):
        e.encode_ld("r0", 0, 0, 9)  # nbytes > 8
    with pytest.raises(ValueError):
        e.encode_ld("r0", 0, 0, 0)  # nbytes < 1
    with pytest.raises(ValueError):
        e.encode_ld("r0", 16, 0, 1)  # header id > 15
    with pytest.raises(ValueError):
        e.encode_send("r0", -513)  # below the 10-bit signed range
    with pytest.raises(ValueError):
        e.encode_lookup("r1", 16, "r0", 0)  # table id > 15
    with pytest.raises(ValueError):
        e.encode_ldmd("r5", 0)  # unknown register


def test_signed_encoding_is_twos_complement():
    # -22 -> 1002 in 10 bits.
    word = e.encode_send("r0", -22)
    assert (word >> 13) & 0x3FF == 1002
