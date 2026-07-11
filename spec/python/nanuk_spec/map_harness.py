"""Harness around the nanuk-map-emu golden model, and the composed
PP -> MAP pipeline.

run_map feeds a MAP program, the frame bytes, the PP's outputs (a
ParseResult), and table config through the emulator CLI's ctx.txt contract.
run_pipeline chains the two golden models with the same gating the SimBricks
glue applies: a PP verdict other than accept short-circuits (the MAP never
runs; the packet is dropped/flooded per policy at the glue layer — here we
just report None).
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .harness import VERDICT_ACCEPT, ParseResult, run_program

# Verdicts (mirror spec/map-model/state.sail)
VERDICT_SENT = 0
VERDICT_DROP = 1
VERDICT_ERROR = 2

# Error codes (mirror spec/map-model/state.sail)
MAP_ERR_NONE = 0
MAP_ERR_WINDOW_VIOLATION = 1
MAP_ERR_STEP_BUDGET = 2
MAP_ERR_ILLEGAL = 3
MAP_ERR_PC_RANGE = 4
MAP_ERR_HDR_ABSENT = 5
MAP_ERR_SEND_RANGE = 6

# Window geometry (mirror spec/map-model/params.sail)
BUF_BYTES = 256

_DEFAULT_MAP_EMU = Path(__file__).resolve().parents[3] / "build" / "nanuk-map-emu"


@dataclass(frozen=True)
class Table:
    """One exact-match table: control-plane configuration + entries."""

    key_width: int
    action_width: int
    entries: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class MapResult:
    verdict: int
    error: int
    egress: int
    delta: int
    steps: int
    frame: bytes | None

    @property
    def sent(self) -> bool:
        return self.verdict == VERDICT_SENT


def map_emulator_path() -> Path:
    """Path to nanuk-map-emu: $NANUK_MAP_EMU overrides the default build location."""
    return Path(os.environ.get("NANUK_MAP_EMU", _DEFAULT_MAP_EMU))


def _ctx_text(pp: ParseResult, tables: list[Table], ingress: int) -> str:
    lines = [f"ingress {ingress}"]
    for slot, value in enumerate(pp.smd):
        if value:
            lines.append(f"smd {slot} {value}")
    for hdr_id in range(len(pp.hdr_present)):
        if pp.hdr_present[hdr_id]:
            lines.append(f"hdr {hdr_id} 1 {pp.hdr_offset[hdr_id]}")
    for tid, table in enumerate(tables):
        lines.append(f"table {tid} {table.key_width} {table.action_width}")
        for key, action in table.entries.items():
            lines.append(f"entry {tid} {key:#x} {action:#x}")
    return "\n".join(lines) + "\n"


def run_map(
    prog: bytes,
    packet: bytes,
    pp: ParseResult,
    tables: list[Table],
    ingress: int,
    emu: Path | None = None,
) -> MapResult:
    """Run one already-parsed frame through the MAP golden model."""
    emu = emu or map_emulator_path()
    if not emu.exists():
        raise FileNotFoundError(
            f"MAP emulator not found at {emu}; build it with: cmake --build build"
        )
    with tempfile.TemporaryDirectory() as tmp:
        prog_path = Path(tmp) / "prog.bin"
        pkt_path = Path(tmp) / "pkt.bin"
        ctx_path = Path(tmp) / "ctx.txt"
        prog_path.write_bytes(prog)
        pkt_path.write_bytes(packet)
        ctx_path.write_text(_ctx_text(pp, tables, ingress))
        out = subprocess.run(
            [str(emu), str(prog_path), str(pkt_path), str(ctx_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    raw = json.loads(out.stdout)
    frame = None
    if raw["verdict"] == VERDICT_SENT:
        frame = bytes.fromhex(raw["frame"])
        # The MAP's window holds the first BUF_BYTES of the frame; any tail
        # beyond it never entered the engine's custody and passes through.
        if len(packet) > BUF_BYTES:
            frame += packet[BUF_BYTES:]
    return MapResult(
        verdict=raw["verdict"],
        error=raw["error"],
        egress=raw["egress"],
        delta=raw["delta"],
        steps=raw["steps"],
        frame=frame,
    )


def run_pipeline(
    pp_prog: bytes,
    map_prog: bytes,
    packet: bytes,
    tables: list[Table],
    ingress: int,
) -> tuple[ParseResult, MapResult | None]:
    """Run one packet through PP then MAP (the composed golden model).

    Short-circuits when the PP verdict is not accept — the same gating the
    SimBricks glue applies before its forwarding stage.
    """
    pp = run_program(pp_prog, packet)
    if pp.verdict != VERDICT_ACCEPT:
        return pp, None
    return pp, run_map(map_prog, packet, pp, tables, ingress)
