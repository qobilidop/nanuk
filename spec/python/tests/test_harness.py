"""Harness tests, including the Python<->Sail encoding differential guard:
programs assembled with asm.py run on the Sail-generated emulator."""

from pathlib import Path

from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether
from scapy.utils import wrpcap

from nanuk_isa.asm import assemble
from nanuk_spec.harness import (
    ERR_HDR_VIOLATION,
    VERDICT_ACCEPT,
    VERDICT_DROP,
    VERDICT_ERROR,
    run_pcap,
    run_program,
)

TRIVIAL_ACCEPT = assemble("movi r0, 7\nhalt accept\n")


def test_run_program_trivial_accept():
    result = run_program(TRIVIAL_ACCEPT, b"")
    assert result.verdict == VERDICT_ACCEPT
    assert result.error == 0
    assert result.steps == 2
    assert result.accepted


def test_differential_assembled_program_runs_on_sail_model():
    """Drift tripwire: a program using every instruction, assembled by the
    Python encoder, must execute with the intended semantics on the Sail
    emulator. Extracts 16 bits at bit 16 (0xABCD), advances, records header 2,
    stores to SMD slot 4, and accepts."""
    src = """
.equ h_two 2
    ext r0, 16, 16        ; extract 0xABCD at bit 16
    movi r1, 0xABCD
    bne r0, r1, fail
    movi r2, 2
    advr r2               ; cursor = 2
    sethdr h_two
    ext r3, 0, 8          ; byte at cursor 2 = 0xAB
    shl r3, r3, 8         ; 0xAB00
    stmd 4, r3, 1
    advi 1
    halt accept
fail:
    halt drop
"""
    prog = assemble(src)
    result = run_program(prog, bytes([0x00, 0x11, 0xAB, 0xCD]))
    assert result.verdict == VERDICT_ACCEPT, f"error={result.error}"
    assert result.hdr(2) == 2
    assert result.smd[4] == 0xAB00
    assert result.payload_offset == 3
    assert result.steps == 11


def test_run_program_error_result():
    prog = assemble("ext r0, 0, 16\nhalt accept\n")
    result = run_program(prog, b"\x01")  # 1-byte packet, 16-bit read
    assert result.verdict == VERDICT_ERROR
    assert result.error == ERR_HDR_VIOLATION


def test_run_pcap_roundtrip(tmp_path: Path):
    drop_all = assemble("halt drop\n")
    pcap = tmp_path / "two.pcap"
    wrpcap(
        str(pcap),
        [
            Ether(dst="02:00:00:00:00:01") / IP(dst="10.0.0.1") / UDP(dport=53),
            Ether(dst="02:00:00:00:00:02") / IP(dst="10.0.0.2") / UDP(dport=443),
        ],
    )
    results = run_pcap(drop_all, pcap)
    assert len(results) == 2
    assert all(r.verdict == VERDICT_DROP for r in results)
    assert all(r.steps == 1 for r in results)
