"""IPv4 TTL decrement + checksum fix + L2 forward, nanuk-lang edition
(the hand-written ISA copy is fwd.asm).

Table ids follow the examples' control-plane layout: t0 is the L2 FDB,
t3 the system flood table (see ../map_l2fwd/fwd.py). The checksum
recompute is the generic CSUM sequence: header length from the IHL
nibble (and_imm/shift), zero the field, sum the range, store.
Header ids follow ../l2l3l4/parse.asm: h_eth=0, h_ipv4=2.
"""

from nanuk.lang import Header, MatchActionProgram

eth = Header("eth", dst=48, src=48, ethertype=16)
ipv4 = Header(
    "ipv4",
    version=4, ihl=4, tos=8, total_len=16, ident=16,
    flags_frag=16, ttl=8, proto=8, csum=16, src=32, dst=32,
)

H_ETH, H_IPV4 = 0, 2


def make_ttl() -> MatchActionProgram:
    mp = MatchActionProgram()
    l2 = mp.table("l2", key_width=48, action_width=8)
    flood_tbl = mp.table("flood", key_width=16, action_width=16, table_id=3)
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
        vihl = s.load(None, hdr=H_IPV4, byte_offset=0, nbytes=1)
        hlen = s.shift(s.and_imm(vihl, 0x000F), 2)
        s.store(s.const(0), ipv4h.csum)
        s.store(s.csum(hlen, ipv4h), ipv4h.csum)
        s.goto(forward)

    @mp.state()
    def forward(s):
        act = s.lookup(l2, s.load(ethh.dst), miss=flood)
        s.send(egress=act)

    @mp.state()
    def flood(s):
        ing = s.load_md(0)
        fl = s.lookup(flood_tbl, ing, miss=expired)
        s.send(egress=fl)

    @mp.state()
    def expired(s):
        s.drop()

    return mp


make_map = make_ttl  # each example's eDSL twin exposes make_map
