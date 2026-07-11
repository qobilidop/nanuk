"""Beat 3: the invented nanukproto tunnel parses on the golden model, bad
magic/version drop, and untunneled traffic is untouched."""

import importlib.util
import os
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "nanukproto_parse", REPO_ROOT / "examples" / "nanukproto" / "parse.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build = _mod.build


def nk_hdr(magic=0x4E4B, version=1, flags=0, tenant=0x0ABCDE, inner=0x0800):
    return (
        struct.pack(">H", magic)
        + bytes([(version << 4) | flags])
        + tenant.to_bytes(3, "big")
        + struct.pack(">H", inner)
    )


def test_program_compiles_and_assembles():
    from nanuk_spec.asm import assemble

    binary = assemble(build())
    assert len(binary) % 4 == 0 and len(binary) > 0


needs_emu = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1", reason="needs nanuk-emu (devcontainer)"
)


@needs_emu
class TestOnGoldenModel:
    @pytest.fixture(scope="class")
    def prog(self):
        from nanuk_spec.asm import assemble

        return assemble(build())

    def _run(self, prog, packet):
        from nanuk_spec.harness import run_program

        return run_program(prog, packet)

    def _eth(self, ethertype):
        # dst aa:bb:cc:dd:ee:01, src 02:..:02, type
        return bytes.fromhex("aabbccddee01") + bytes(6) + struct.pack(">H", ethertype)

    def _inner_ipv4_udp(self, dport=4242):
        from scapy.layers.inet import IP, UDP

        return bytes(IP(dst="10.0.0.9") / UDP(dport=dport) / b"hi")

    def test_tunneled_ipv4_udp(self, prog):
        pkt = self._eth(0x88B5) + nk_hdr() + self._inner_ipv4_udp()
        r = self._run(prog, pkt)
        assert r.verdict == 0
        assert r.hdr(5) == 14                     # nanukproto
        assert r.hdr(2) == 22                     # inner ipv4 = 14 + 8
        assert r.hdr(3) == 42                     # inner udp = 22 + 20
        assert r.payload_offset == 50
        assert r.smd[5] == 0x000A                 # tenant 0x0ABCDE, MSB-first
        assert r.smd[6] == 0xBCDE
        assert r.smd[4] == 4242                   # inner dport
        assert r.smd[0:3] == [0xAABB, 0xCCDD, 0xEE01]

    def test_bad_magic_drops(self, prog):
        pkt = self._eth(0x88B5) + nk_hdr(magic=0xDEAD) + self._inner_ipv4_udp()
        r = self._run(prog, pkt)
        assert r.verdict == 1
        assert r.hdr(5) == 14                     # recorded before validation
        assert r.hdr(2) is None

    def test_bad_version_drops(self, prog):
        pkt = self._eth(0x88B5) + nk_hdr(version=2) + self._inner_ipv4_udp()
        assert self._run(prog, pkt).verdict == 1

    def test_untunneled_traffic_unchanged(self, prog):
        pkt = self._eth(0x0800) + self._inner_ipv4_udp(dport=53)
        r = self._run(prog, pkt)
        assert r.verdict == 0
        assert r.hdr(5) is None
        assert r.hdr(2) == 14
        assert r.hdr(3) == 34
        assert r.smd[4] == 53

    def test_vlan_inside_tunnel(self, prog):
        from scapy.layers.inet import IP, UDP
        from scapy.layers.l2 import Dot1Q

        inner = bytes(Dot1Q(vlan=7) / IP() / UDP(dport=9))
        pkt = self._eth(0x88B5) + nk_hdr(inner=0x8100) + inner
        r = self._run(prog, pkt)
        assert r.verdict == 0
        assert r.hdr(5) == 14
        assert r.hdr(1) == 22                     # vlan tag inside the tunnel
        assert r.hdr(2) == 26
        assert r.smd[3] == 7
