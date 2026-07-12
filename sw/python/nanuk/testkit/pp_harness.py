"""Packet I/O harness around the nanuk-pp-emu golden model.

Feeds a program binary and raw packet bytes through the emulator CLI and
parses its JSON output contract into a ParserResult. run_pcap drives a whole
pcap file, one emulator run per packet.
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scapy.utils import rdpcap

# Verdicts (mirror spec/sail/model/pp/state.sail)
VERDICT_ACCEPT = 0
VERDICT_DROP = 1
VERDICT_ERROR = 2

# Error codes (mirror spec/sail/model/pp/state.sail)
ERR_NONE = 0
ERR_HDR_VIOLATION = 1
ERR_STEP_BUDGET = 2
ERR_ILLEGAL = 3
ERR_PC_RANGE = 4
ERR_MD_RANGE = 5

_DEFAULT_EMU = Path(__file__).resolve().parents[4] / "spec" / "sail" / "build" / "nanuk-pp-emu"


@dataclass(frozen=True)
class ParserResult:
    verdict: int
    error: int
    payload_offset: int
    steps: int
    hdr_present: list[int]
    hdr_offset: list[int]
    md: list[int]

    @property
    def accepted(self) -> bool:
        return self.verdict == VERDICT_ACCEPT

    def hdr(self, hdr_id: int) -> int | None:
        """Offset of a recorded header, or None if not present."""
        return self.hdr_offset[hdr_id] if self.hdr_present[hdr_id] else None


def emulator_path() -> Path:
    """Path to nanuk-pp-emu: $NANUK_PP_EMU overrides the default build location."""
    return Path(os.environ.get("NANUK_PP_EMU", _DEFAULT_EMU))


def run_program(
    prog: bytes, packet: bytes, md_in=(), emu: Path | None = None
) -> ParserResult:
    """Run one packet through the golden model.

    md_in: up to 8 16-bit slots seeding the PP's metadata window."""
    emu = emu or emulator_path()
    if not emu.exists():
        raise FileNotFoundError(
            f"emulator not found at {emu}; build it with: cmake --build spec/sail/build"
        )
    with tempfile.TemporaryDirectory() as tmp:
        prog_path = Path(tmp) / "prog.bin"
        pkt_path = Path(tmp) / "pkt.bin"
        prog_path.write_bytes(prog)
        pkt_path.write_bytes(packet)
        argv = [str(emu), str(prog_path), str(pkt_path)]
        if any(md_in):
            ctx_path = Path(tmp) / "ctx.txt"
            ctx_path.write_text(
                "".join(f"md {i} {v}\n" for i, v in enumerate(md_in) if v)
            )
            argv.append(str(ctx_path))
        out = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=True,
        )
    return ParserResult(**json.loads(out.stdout))


def run_pcap(prog: bytes, pcap_path: Path, emu: Path | None = None) -> list[ParserResult]:
    """Run every packet of a pcap file through the golden model."""
    return [run_program(prog, bytes(pkt), emu=emu) for pkt in rdpcap(str(pcap_path))]
