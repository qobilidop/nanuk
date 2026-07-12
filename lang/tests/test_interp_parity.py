"""The IR interpreter agrees with the golden model on the REAL programs:
l2l3l4 and nanukproto build_ir() over the full demo corpus (and a couple
of tunnel packets nanukproto alone can reach). Together with test_parity
(eDSL == hand asm) this closes the triangle: interp == emu == hand.

Gated behind NANUK_COSIM=1 (needs the built nanuk-emu golden model)."""

import importlib.util
import os
import struct
from pathlib import Path

import pytest
from scapy.layers.inet import IP, UDP

from nanuk_ir.interp import interp
from nanuk_ir.lower import to_asm
from nanuk_lang.programs.l2l3l4 import build_ir as l2l3l4_ir
from nanuk_isa.asm import assemble
from nanuk_spec.harness import run_program

from test_parity import CORPUS

pytestmark = pytest.mark.skipif(
    os.environ.get("NANUK_COSIM") != "1",
    reason="interp parity needs NANUK_COSIM=1 and a built nanuk-emu",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "nanukproto_parse", REPO_ROOT / "examples" / "nanukproto" / "parse.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
nanukproto_ir = _mod.build_ir

FIELDS = ("verdict", "error", "payload_offset", "steps",
          "hdr_present", "hdr_offset", "smd")


def nk_tunnel(magic=0x4E4B, version=1) -> bytes:
    """An Ethernet frame carrying the invented tunnel around IPv4/UDP."""
    nk_hdr = (struct.pack(">H", magic)
              + bytes([(version << 4)])
              + (0x0ABCDE).to_bytes(3, "big")
              + struct.pack(">H", 0x0800))
    inner = bytes(IP(dst="10.0.0.2") / UDP(dport=4242) / b"hi")
    eth = bytes.fromhex("aabbccddee01") + bytes(6) + struct.pack(">H", 0x88B5)
    return eth + nk_hdr + inner


EXTRA = [
    ("nk_tunnel_good", nk_tunnel()),
    ("nk_bad_magic", nk_tunnel(magic=0x1234)),
    ("nk_bad_version", nk_tunnel(version=7)),
]

PACKETS = [(name, bytes(pkt)) for name, pkt in CORPUS] + EXTRA


@pytest.fixture(scope="module", params=["l2l3l4", "nanukproto"])
def program(request):
    return (l2l3l4_ir if request.param == "l2l3l4" else nanukproto_ir)()


@pytest.mark.parametrize("pkt", [p for _, p in PACKETS], ids=[n for n, _ in PACKETS])
def test_interp_matches_golden_model(program, pkt):
    ir_result = interp(program, pkt)
    emu_result = run_program(assemble(to_asm(program)), pkt)
    for field in FIELDS:
        assert getattr(ir_result, field) == getattr(emu_result, field), (
            f"field {field!r}: interp={getattr(ir_result, field)!r} "
            f"emu={getattr(emu_result, field)!r}"
        )
