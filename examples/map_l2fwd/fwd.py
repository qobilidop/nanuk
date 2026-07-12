"""L2 forward, nanuk-lang edition (the hand-written ISA copy is fwd.asm).

Table id follows the examples' control-plane layout: t0 is the L2 FDB.
Header ids follow ../l2l3l4/parse.asm: h_eth=0.
"""

from nanuk.lang import MD_FLOOD, Header, MapProgram

eth = Header("eth", dst=48, src=48, ethertype=16)

H_ETH = 0


def make_l2fwd() -> MapProgram:
    mp = MapProgram()
    l2 = mp.table("l2", key_width=48, action_width=8)
    ethh = mp.header(eth, hdr_id=H_ETH)

    @mp.state(start=True)
    def forward(s):
        act = s.lookup(l2, s.load(ethh.dst), miss=flood)
        s.send(act)

    @mp.state()
    def flood(s):
        s.send(s.load_md(MD_FLOOD))

    return mp


make_map = make_l2fwd  # each example's eDSL twin exposes make_map
