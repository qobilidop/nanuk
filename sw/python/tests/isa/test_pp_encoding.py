"""Golden-word tests. These words are identical to the ones pinned in
spec/parser-test/test_decode.sail — the two encoders must never drift."""

import pytest

from nanuk.isa import pp_encoding


def test_golden_words_match_sail():
    assert pp_encoding.encode_ext("r0", 96, 16) == 0x040603C0
    assert pp_encoding.encode_advi(14) == 0x0800000E
    assert pp_encoding.encode_advr("r1") == 0x0C800000
    assert pp_encoding.encode_movi("r0", 7) == 0x10000007
    assert pp_encoding.encode_movi("r1", 0x8100) == 0x10808100
    assert pp_encoding.encode_shl("r1", "r1", 2) == 0x14908000
    assert pp_encoding.encode_beq("r0", "r1", 9) == 0x18100009
    assert pp_encoding.encode_bne("r1", "r2", 21) == 0x1CA00015
    assert pp_encoding.encode_jmp(5) == 0x20000005
    assert pp_encoding.encode_sethdr(3) == 0x24000003
    assert pp_encoding.encode_stmd(0, "r0", 3) == 0x28400000
    assert pp_encoding.encode_halt(drop=False) == 0x2C000000
    assert pp_encoding.encode_halt(drop=True) == 0x2C000001
    assert pp_encoding.encode_ldmd("r1", 3) == 0x30980000


def test_rz_encodes_as_4():
    assert pp_encoding.encode_advr("rz") == (0x03 << 26) | (4 << 23)


@pytest.mark.parametrize(
    "call",
    [
        lambda: pp_encoding.encode_ext("r0", 2048, 8),   # boff > 11 bits
        lambda: pp_encoding.encode_ext("r0", 0, 0),      # size 0
        lambda: pp_encoding.encode_ext("r0", 0, 65),     # size > 64
        lambda: pp_encoding.encode_advi(1 << 16),        # imm > 16 bits
        lambda: pp_encoding.encode_movi("r0", -1),       # negative
        lambda: pp_encoding.encode_movi("r7", 0),        # bad register
        lambda: pp_encoding.encode_shl("r0", "r0", 64),  # shamt > 6 bits
        lambda: pp_encoding.encode_sethdr(16),           # hdr > 4 bits
        lambda: pp_encoding.encode_stmd(7, "r0", 2),     # slots 7..8 out of range
        lambda: pp_encoding.encode_stmd(0, "r0", 5),     # too many units
        lambda: pp_encoding.encode_jmp(1 << 16),         # target > 16 bits
    ],
)
def test_field_range_validation(call):
    with pytest.raises(ValueError):
        call()
