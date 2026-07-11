"""Header layout computation: offsets, widths, byte_len, error cases."""

import pytest

from nanuk_lang import CompileError, Header


def test_field_offsets_and_widths():
    eth = Header("eth", dst=48, src=48, ethertype=16)
    assert (eth.dst.bit_offset, eth.dst.width) == (0, 48)
    assert (eth.src.bit_offset, eth.src.width) == (48, 48)
    assert (eth.ethertype.bit_offset, eth.ethertype.width) == (96, 16)


def test_sub_byte_fields():
    ipv4 = Header("ipv4", version=4, ihl=4, tos=8, total_len=16, ident=16,
                  flags_frag=16, ttl=8, proto=8, csum=16, src=32, dst=32)
    assert (ipv4.version.bit_offset, ipv4.version.width) == (0, 4)
    assert (ipv4.ihl.bit_offset, ipv4.ihl.width) == (4, 4)
    assert (ipv4.proto.bit_offset, ipv4.proto.width) == (72, 8)
    assert ipv4.byte_len == 20


def test_byte_len():
    assert Header("eth", dst=48, src=48, ethertype=16).byte_len == 14
    assert Header("vlan", tci=16, ethertype=16).byte_len == 4
    assert Header("udp", sport=16, dport=16, length=16, csum=16).byte_len == 8


def test_field_qualname():
    vlan = Header("vlan", tci=16, ethertype=16)
    assert vlan.tci.qualname == "vlan.tci"


def test_non_byte_aligned_header_rejected():
    with pytest.raises(CompileError, match="whole bytes"):
        Header("bad", flag=1, rest=3)


def test_field_wider_than_64_bits_rejected():
    with pytest.raises(CompileError, match="at most 64"):
        Header("bad", huge=65, pad=7)


def test_non_positive_field_width_rejected():
    with pytest.raises(CompileError, match="positive"):
        Header("bad", empty=0)
    with pytest.raises(CompileError, match="positive"):
        Header("bad", negative=-8)


def test_empty_header_rejected():
    with pytest.raises(CompileError, match="no fields"):
        Header("bad")


def test_unknown_field_raises_attribute_error():
    eth = Header("eth", dst=48, src=48, ethertype=16)
    with pytest.raises(AttributeError, match="no field"):
        eth.vlan_id


def test_fields_are_immutable():
    eth = Header("eth", dst=48, src=48, ethertype=16)
    with pytest.raises(AttributeError):
        eth.dst.width = 32
