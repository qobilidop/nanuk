"""Loader for the golden conformance vectors at spec/vectors/.

The vectors are executable spec: committed, language-neutral JSON files,
generated from the Sail emulators (spec/vectors/gen.py), that every
implementation of the ISA answers to. This module is the Python consumer;
it reconstructs harness-shaped inputs and expectations from the files.
Scapy-free by design — RTL and future ports consume vectors, not packets.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .map_harness import Table

VECTORS_DIR = Path(__file__).resolve().parents[4] / "spec" / "vectors"


@dataclass(frozen=True)
class PPExpect:
    verdict: int
    error: int
    payload_offset: int
    steps: int
    hdr_present: list[int]
    hdr_offset: list[int]
    md: list[int]


@dataclass(frozen=True)
class MapExpect:
    verdict: int
    error: int
    md: list[int]
    delta: int
    steps: int
    frame: bytes | None


@dataclass(frozen=True)
class Vector:
    name: str
    frame: bytes
    md_in: list[int]
    pp: PPExpect
    map: MapExpect | None  # None in pp suites and on PP short-circuit


@dataclass(frozen=True)
class VectorFile:
    path: Path
    suite: str  # "pp" | "pipeline"
    programs: dict[str, str]  # role ("pp"/"map") -> repo-relative asm path
    tables: list[Table]
    vectors: list[Vector]


def _tables(raw) -> list[Table]:
    return [
        Table(
            key_width=t["key_width"],
            action_width=t["action_width"],
            entries={int(k, 16): int(v, 16) for k, v in t["entries"].items()},
        )
        for t in raw
    ]


def _vector(raw) -> Vector:
    mp = raw.get("map")
    return Vector(
        name=raw["name"],
        frame=bytes.fromhex(raw["frame"]),
        md_in=raw["md_in"],
        pp=PPExpect(**raw["pp"]),
        map=None
        if mp is None
        else MapExpect(
            verdict=mp["verdict"],
            error=mp["error"],
            md=mp["md"],
            delta=mp["delta"],
            steps=mp["steps"],
            frame=None if mp["frame"] is None else bytes.fromhex(mp["frame"]),
        ),
    )


def load_vector_file(path: Path) -> VectorFile:
    raw = json.loads(path.read_text())
    return VectorFile(
        path=path,
        suite=raw["suite"],
        programs=raw["programs"],
        tables=_tables(raw.get("tables", [])),
        vectors=[_vector(v) for v in raw["vectors"]],
    )


def load_all(root: Path = VECTORS_DIR) -> list[VectorFile]:
    files = sorted(root.glob("*/*.json"))
    if not files:
        raise FileNotFoundError(f"no conformance vectors under {root}")
    return [load_vector_file(p) for p in files]
