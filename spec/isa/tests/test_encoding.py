"""Golden-word tests. These words are identical to the ones pinned in
spec/parser-test/test_decode.sail — the two encoders must never drift."""

import pytest

from nanuk_isa import encoding


def test_golden_words_match_sail():
    assert encoding.encode_ext("r0", 96, 16) == 0x040603C0
    assert encoding.encode_advi(14) == 0x0800000E
    assert encoding.encode_advr("r1") == 0x0C800000
    assert encoding.encode_movi("r0", 7) == 0x10000007
    assert encoding.encode_movi("r1", 0x8100) == 0x10808100
    assert encoding.encode_shl("r1", "r1", 2) == 0x14908000
    assert encoding.encode_beq("r0", "r1", 9) == 0x18100009
    assert encoding.encode_bne("r1", "r2", 21) == 0x1CA00015
    assert encoding.encode_jmp(5) == 0x20000005
    assert encoding.encode_sethdr(3) == 0x24000003
    assert encoding.encode_stmd(0, "r0", 3) == 0x28400000
    assert encoding.encode_halt(drop=False) == 0x2C000000
    assert encoding.encode_halt(drop=True) == 0x2C000001


def test_rz_encodes_as_4():
    assert encoding.encode_advr("rz") == (0x03 << 26) | (4 << 23)


@pytest.mark.parametrize(
    "call",
    [
        lambda: encoding.encode_ext("r0", 2048, 8),   # boff > 11 bits
        lambda: encoding.encode_ext("r0", 0, 0),      # size 0
        lambda: encoding.encode_ext("r0", 0, 65),     # size > 64
        lambda: encoding.encode_advi(1 << 16),        # imm > 16 bits
        lambda: encoding.encode_movi("r0", -1),       # negative
        lambda: encoding.encode_movi("r7", 0),        # bad register
        lambda: encoding.encode_shl("r0", "r0", 64),  # shamt > 6 bits
        lambda: encoding.encode_sethdr(16),           # hdr > 4 bits
        lambda: encoding.encode_stmd(7, "r0", 2),     # slots 7..8 out of range
        lambda: encoding.encode_stmd(0, "r0", 5),     # too many units
        lambda: encoding.encode_jmp(1 << 16),         # target > 16 bits
    ],
)
def test_field_range_validation(call):
    with pytest.raises(ValueError):
        call()
