"""L2 forward, nanuk-lang edition (the hand-written ISA copy is fwd.asm).

Table ids follow the examples' control-plane layout: t0 is the L2 FDB,
t3 the system flood table ({ingress -> flood bitmap}, installed by the
control plane - flooding is policy, policy lives in tables). md slot
conventions per nanuk_switch: slot 0 = ingress port id in, egress
bitmap out. Header ids follow ../l2l3l4/parse.asm: h_eth=0.
"""

from nanuk.lang import Header, MatchActionProgram

eth = Header("eth", dst=48, src=48, ethertype=16)

H_ETH = 0


def make_l2fwd() -> MatchActionProgram:
    mp = MatchActionProgram()
    l2 = mp.table("l2", key_width=48, action_width=8)
    flood_tbl = mp.table("flood", key_width=16, action_width=16, table_id=3)
    ethh = mp.header(eth, hdr_id=H_ETH)

    @mp.state(start=True)
    def forward(s):
        act = s.lookup(l2, s.load(ethh.dst), miss=flood)
        s.send(egress=act)

    @mp.state()
    def flood(s):
        ing = s.load_md(0)
        fl = s.lookup(flood_tbl, ing, miss=dark)
        s.send(egress=fl)

    @mp.state()
    def dark(s):
        s.drop()

    return mp


make_map = make_l2fwd  # each example's eDSL twin exposes make_map
