"""L2 forward - a MAP (match-action) program, the parser's sibling engine.

The playground composes your MAP program behind the standard l2l3l4
parser: packet -> parser -> (on accept) -> MAP. The demo control plane
preloads the FDB (table "l2") with two entries:

    aa:bb:cc:dd:ee:01 -> port 2 (bitmap 0b0100)
    aa:bb:cc:dd:ee:02 -> port 3 (bitmap 0b1000)

and the system flood table ("flood", table 3 by nanuk_switch convention)
with {ingress port -> every port but ingress}. Metadata slot 0 carries
the ingress port in and your egress bitmap out - send(egress=...) is
sugar for writing it. Known DMACs go out exactly one port; anything else
floods. Try the presets, then rewrite the miss path to drop instead.
"""

from nanuk.lang import Header, MatchActionProgram

eth = Header("eth", dst=48, src=48, ethertype=16)

mp = MatchActionProgram()
l2 = mp.table("l2", key_width=48, action_width=8)
flood_tbl = mp.table("flood", key_width=16, action_width=16, table_id=3)
ethh = mp.header(eth, hdr_id=0)  # h_eth, as marked by the l2l3l4 parser


@mp.state(start=True)
def forward(s):
    dmac = s.load(ethh.dst)
    act = s.lookup(l2, dmac, miss=flood)  # hit: act = egress port bitmap
    s.send(egress=act)


@mp.state()
def flood(s):
    ing = s.load_md(0)                    # slot 0: ingress port id
    fl = s.lookup(flood_tbl, ing, miss=dark)
    s.send(egress=fl)                     # all ports except ingress


@mp.state()
def dark(s):
    s.drop()                              # unconfigured flood table: fail closed


def build_map_ir():
    return mp.build_ir()
