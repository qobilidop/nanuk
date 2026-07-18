"""The shipped example programs compile through the bridge verbatim, and
every preset packet produces the expected verdict on both programs."""

import json
import pathlib

import bridge

WEB = pathlib.Path(__file__).resolve().parents[2]
PROGRAMS = WEB / "src" / "programs"
PRESETS = WEB / "public" / "presets.json"

# name -> (verdict on l2l3l4, verdict on nanukproto); 0=accept 1=drop 2=error
EXPECTED = {
    "plain_ipv4_udp": (0, 0),
    "single_vlan": (0, 0),
    "qinq": (0, 0),
    "ipv4_options": (0, 0),
    "ipv4_tcp": (0, 0),
    "arp": (0, 0),
    "runt_frame": (2, 2),
    "non_v4_version": (1, 1),
    "nk_tunnel": (0, 0),  # l2l3l4: unknown EtherType -> accept; nanukproto: parsed
}


def _compile(path: pathlib.Path) -> None:
    out = json.loads(bridge.compile_source(path.read_text()))
    assert out["ok"], out


def test_presets_expected_verdicts():
    presets = json.loads(PRESETS.read_text())
    # The classic (parser/MAP) corpus is exactly EXPECTED; SIIT presets are
    # scoped to the "siit" program and covered by test_siit_bridge.
    classic = [p for p in presets if "l2l3l4" in p.get("programs", [])]
    assert {p["name"] for p in classic} == set(EXPECTED)
    for program in ("l2l3l4.py", "nanukproto.py"):
        _compile(PROGRAMS / program)
        for preset in classic:
            out = json.loads(bridge.run_packet(preset["hex"]))
            assert out["ok"]
            want = EXPECTED[preset["name"]][0 if program == "l2l3l4.py" else 1]
            assert out["result"]["verdict"] == want, (program, preset["name"])
