"""Generate the golden conformance vectors from the Sail emulators.

Runs the demo example programs over the shared corpora and records the
complete result contract per packet. The output is committed; the emulator
leg of sw/python/tests/golden/test_conformance_vectors.py holds the files
byte-stable (regeneration must be a no-op).

Run from the sw/python env inside the devcontainer (needs scapy and the
built emulators):

    cd sw/python && uv run python ../../spec/vectors/gen.py
"""

import json
from pathlib import Path

from nanuk.isa.map_asm import assemble as map_assemble
from nanuk.isa.pp_asm import assemble as pp_assemble
from nanuk.testkit.map_harness import run_pipeline
from nanuk.testkit.pp_harness import run_program
from nanuk.testkit.testkit import (
    DMAC,
    NO_TABLE,
    demo_flood_table,
    demo_l2_table,
    demo_tun_table,
    l2l3l4_packets,
    map_packets,
)
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

# The demo tunnel frame (mirrors tests/golden/test_map_demo_tunnel.py).
TUNNEL_OUTER = bytes.fromhex(
    "024e4b000001" "024e4b000002" "88b5"  # outer Ethernet: dst, src, type
    "4e4b" "10" "000001" "6558"  # nk: magic, ver/flags, tenant, inner
)
TUNNEL_INNER = bytes(Ether(dst=DMAC) / IP(dst="10.1.0.9") / UDP(dport=4242))

L2FWD_TABLES = [demo_l2_table(both=True), NO_TABLE, NO_TABLE, demo_flood_table()]
PUSH_TABLES = [NO_TABLE, demo_tun_table(), NO_TABLE, demo_flood_table()]
POP_TABLES = [NO_TABLE, NO_TABLE, NO_TABLE, demo_flood_table()]


def ser_pp(r) -> dict:
    return {
        "verdict": r.verdict,
        "error": r.error,
        "payload_offset": r.payload_offset,
        "steps": r.steps,
        "hdr_present": list(r.hdr_present),
        "hdr_offset": list(r.hdr_offset),
        "md": list(r.md),
    }


def ser_map(r) -> dict:
    return {
        "verdict": r.verdict,
        "error": r.error,
        "md": list(r.md),
        "delta": r.delta,
        "steps": r.steps,
        "frame": None if r.frame is None else r.frame.hex(),
    }


def ser_table(t) -> dict:
    return {
        "key_width": t.key_width,
        "action_width": t.action_width,
        "entries": {f"{k:#x}": f"{v:#x}" for k, v in sorted(t.entries.items())},
    }


def md8(ingress: int = 0) -> list[int]:
    return [ingress] + [0] * 7


def pp_suite(pp_asm: str, packets) -> dict:
    prog = pp_assemble((REPO / pp_asm).read_text())
    vectors = []
    for name, pkt, md_in in packets:
        r = run_program(prog, pkt, md_in)
        vectors.append(
            {"name": name, "frame": pkt.hex(), "md_in": md_in, "pp": ser_pp(r)}
        )
    return {"suite": "pp", "programs": {"pp": pp_asm}, "vectors": vectors}


def pipeline_suite(pp_asm: str, map_asm: str, tables, packets) -> dict:
    pp_prog = pp_assemble((REPO / pp_asm).read_text())
    map_prog = map_assemble((REPO / map_asm).read_text())
    vectors = []
    for name, pkt, md_in in packets:
        pp, mp = run_pipeline(pp_prog, map_prog, pkt, tables, md_in)
        vectors.append(
            {
                "name": name,
                "frame": pkt.hex(),
                "md_in": md_in,
                "pp": ser_pp(pp),
                "map": None if mp is None else ser_map(mp),
            }
        )
    return {
        "suite": "pipeline",
        "programs": {"pp": pp_asm, "map": map_asm},
        "tables": [ser_table(t) for t in tables],
        "vectors": vectors,
    }


def corpus(packets, ingress: int = 0):
    return [(name, pkt, md8(ingress)) for name, pkt in packets]


def build() -> dict[str, dict]:
    l2l3l4 = "examples/l2l3l4/parse.asm"
    parse_tunnel = "examples/nanukproto/parse_tunnel.asm"

    unknown = dict(map_packets())["unknown_dmac"]
    ingress_sweep = [
        (f"unknown_dmac_ig{i}", unknown, md8(i)) for i in (1, 2, 3)
    ]

    tunnel_frame = TUNNEL_OUTER + TUNNEL_INNER
    push_prog = map_assemble((REPO / "examples/nanukproto/tunnel_push.asm").read_text())
    pp_prog = pp_assemble((REPO / l2l3l4).read_text())
    _, pushed = run_pipeline(pp_prog, push_prog, TUNNEL_INNER, PUSH_TABLES, md8(0))
    assert pushed is not None and pushed.frame is not None

    drop_all_corpus = [c for c in l2l3l4_packets() if c[0] in ("plain_ipv4_udp", "arp", "runt")]

    return {
        "pp/l2l3l4.json": pp_suite(l2l3l4, corpus(l2l3l4_packets())),
        "pp/nanukproto.json": pp_suite(
            parse_tunnel,
            corpus(l2l3l4_packets()) + [("nk_tunnel", tunnel_frame, md8(0))],
        ),
        "pp/drop_all.json": pp_suite(
            "examples/drop_all/parse.asm", corpus(drop_all_corpus)
        ),
        "pipeline/map_l2fwd.json": pipeline_suite(
            l2l3l4,
            "examples/map_l2fwd/fwd.asm",
            L2FWD_TABLES,
            corpus(map_packets()) + ingress_sweep,
        ),
        "pipeline/map_ttl.json": pipeline_suite(
            l2l3l4,
            "examples/map_ttl/fwd.asm",
            L2FWD_TABLES,
            corpus(map_packets()),
        ),
        "pipeline/tunnel_push.json": pipeline_suite(
            l2l3l4,
            "examples/nanukproto/tunnel_push.asm",
            PUSH_TABLES,
            [
                ("inner_known", TUNNEL_INNER, md8(0)),
                ("inner_known_ig3", TUNNEL_INNER, md8(3)),
                ("inner_unknown", unknown, md8(0)),
                ("pp_drop_short_circuit", dict(l2l3l4_packets())["non_v4_version"], md8(0)),
            ],
        ),
        "pipeline/tunnel_pop.json": pipeline_suite(
            parse_tunnel,
            "examples/nanukproto/tunnel_pop.asm",
            POP_TABLES,
            [
                ("pushed_roundtrip", pushed.frame, md8(1)),
                ("plain_not_tunnel", dict(l2l3l4_packets())["plain_ipv4_udp"], md8(2)),
                ("runt", dict(l2l3l4_packets())["runt"], md8(0)),
            ],
        ),
    }


def main():
    for rel, suite in build().items():
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(suite, indent=2) + "\n")
        print(f"wrote {path.relative_to(REPO)} ({len(suite['vectors'])} vectors)")


if __name__ == "__main__":
    main()
