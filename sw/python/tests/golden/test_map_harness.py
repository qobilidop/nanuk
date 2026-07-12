"""MAP harness tests: the smoke program through run_map, and a trivial
PP-accept-all + MAP-flood program through run_pipeline for every ingress."""

from nanuk.isa import pp_encoding as pe
from nanuk.isa import map_encoding as me
from nanuk.testkit.pp_harness import ParserResult, VERDICT_ACCEPT
from nanuk.testkit.map_harness import (
    MAP_ERR_NONE,
    Table,
    run_map,
    run_pipeline,
)


def _words(ws: list[int]) -> bytes:
    return b"".join(w.to_bytes(4, "big") for w in ws)


def _pp_result(**kw) -> ParserResult:
    base = dict(
        verdict=VERDICT_ACCEPT,
        error=0,
        payload_offset=14,
        steps=1,
        hdr_present=[0] * 16,
        hdr_offset=[0] * 16,
        smd=[0] * 8,
    )
    base.update(kw)
    return ParserResult(**base)


def test_run_map_smoke():
    # MOVI r0, 0xF; SEND r0, 0
    prog = _words([me.encode_movi("r0", 0xF), me.encode_send("r0", 0)])
    packet = bytes.fromhex("deadbeef")
    res = run_map(prog, packet, _pp_result(), [], ingress=1)
    assert res.sent
    assert res.error == MAP_ERR_NONE
    assert res.egress == 0xF
    assert res.delta == 0
    assert res.frame == packet


def test_run_map_uses_pp_headers_and_smd():
    # LD r0 from h2+0 (1 byte); ST it at h_frame+0; SEND flood.
    prog = _words(
        [
            me.encode_ld("r0", 2, 0, 1),
            me.encode_st("r0", me.H_FRAME, 0, 1),
            me.encode_ldmd("r1", 9),
            me.encode_send("r1", 0),
        ]
    )
    packet = bytes(range(32))
    pp = _pp_result(
        hdr_present=[0, 0, 1] + [0] * 13,
        hdr_offset=[0, 0, 14] + [0] * 13,
        smd=[0x1234] + [0] * 7,
    )
    res = run_map(prog, packet, pp, [], ingress=2)
    assert res.sent
    # Byte at frame offset 14 (0x0E) copied to offset 0.
    assert res.frame is not None and res.frame[0] == 0x0E
    assert res.frame[1:] == packet[1:]
    # Flood mask for ingress 2, 4 ports: 0b1011.
    assert res.egress == 0xB


def test_run_map_lookup_table():
    # LOOKUP r1, t0, r0 (key from LD of first 6 bytes), miss -> drop.
    prog = _words(
        [
            me.encode_ld("r0", me.H_FRAME, 0, 6),
            me.encode_lookup("r1", 0, "r0", 3),
            me.encode_send("r1", 0),
            me.encode_drop(),
        ]
    )
    dmac = 0x02DEADBEEF01
    packet = dmac.to_bytes(6, "big") + bytes(58)
    table = Table(key_width=48, action_width=8, entries={dmac: 0x4})
    hit = run_map(prog, packet, _pp_result(), [table], ingress=0)
    assert hit.sent and hit.egress == 0x4
    miss = run_map(prog, bytes(64), _pp_result(), [table], ingress=0)
    assert not miss.sent


def test_run_pipeline_flood_per_ingress():
    # PP: HALT accept (parses nothing). MAP: flood.
    pp_prog = pe.encode_halt(False).to_bytes(4, "big")
    map_prog = _words([me.encode_ldmd("r1", 9), me.encode_send("r1", 0)])
    packet = bytes(range(60))
    for ingress in range(4):
        pp, mp = run_pipeline(pp_prog, map_prog, packet, [], ingress=ingress)
        assert pp.accepted
        assert mp is not None and mp.sent
        assert mp.egress == (0xF & ~(1 << ingress))
        assert mp.frame == packet


def test_run_pipeline_short_circuits_on_pp_drop():
    pp_prog = pe.encode_halt(True).to_bytes(4, "big")
    map_prog = _words([me.encode_ldmd("r1", 9), me.encode_send("r1", 0)])
    pp, mp = run_pipeline(pp_prog, map_prog, bytes(60), [], ingress=0)
    assert not pp.accepted
    assert mp is None
