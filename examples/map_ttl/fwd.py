"""IPv4 TTL decrement + checksum fix + L2 forward, nanuk-lang edition
(the hand-written ISA copy is fwd.asm).

Table id follows the examples' control-plane layout: t0 is the L2 FDB.
Header ids follow ../l2l3l4/parse.asm: h_eth=0, h_ipv4=2.
"""

from nanuk.lang.headers import eth, ipv4
from nanuk.lang import MD_FLOOD, MapProgram

H_ETH, H_IPV4 = 0, 2


def make_ttl() -> MapProgram:
    mp = MapProgram()
    l2 = mp.table("l2", key_width=48, action_width=8)
    ethh = mp.header(eth, hdr_id=H_ETH)
    ipv4h = mp.header(ipv4, hdr_id=H_IPV4)

    @mp.state(start=True)
    def check(s):
        ttl = s.load(ipv4h.ttl)
        s.dispatch(ttl, {0: expired, 1: expired}, default=dec)

    @mp.state()
    def dec(s):
        ttl = s.load(ipv4h.ttl)
        s.store(s.add(ttl, -1), ipv4h.ttl)
        s.csum_update(ipv4h)
        s.goto(forward)

    @mp.state()
    def forward(s):
        act = s.lookup(l2, s.load(ethh.dst), miss=flood)
        s.send(act)

    @mp.state()
    def flood(s):
        s.send(s.load_md(MD_FLOOD))

    @mp.state()
    def expired(s):
        s.drop()

    return mp


make_map = make_ttl  # each example's eDSL twin exposes make_map
