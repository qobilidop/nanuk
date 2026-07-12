"""L2 forward — a MAP (match-action) program, the parser's sibling engine.

The playground composes your MAP program behind the standard l2l3l4
parser: packet -> parser -> (on accept) -> MAP. The demo control plane
preloads the FDB (table "l2") with two entries:

    aa:bb:cc:dd:ee:01 -> port 2 (bitmap 0b0100)
    aa:bb:cc:dd:ee:02 -> port 3 (bitmap 0b1000)

Known DMACs go out exactly one port; anything else floods every port but
the ingress. Try the presets, then rewrite the miss path to drop instead.
"""

from nanuk.lang import Header, MapProgram, MD_FLOOD

eth = Header("eth", dst=48, src=48, ethertype=16)

mp = MapProgram()
l2 = mp.table("l2", key_width=48, action_width=8)
ethh = mp.header(eth, hdr_id=0)  # h_eth, as marked by the l2l3l4 parser


@mp.state(start=True)
def forward(s):
    dmac = s.load(ethh.dst)
    act = s.lookup(l2, dmac, miss=flood)  # hit: act = egress port bitmap
    s.send(act)


@mp.state()
def flood(s):
    s.send(s.load_md(MD_FLOOD))  # all ports except ingress


def build_map_ir():
    return mp.build_ir()
