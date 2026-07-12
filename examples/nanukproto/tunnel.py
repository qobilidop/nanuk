"""nanukproto tunnel push/pop, nanuk-lang edition (hand-written ISA copies:
tunnel_push.asm / tunnel_pop.asm).

Table ids follow the examples' control-plane layout: the push uses t1 as
the tunnel map (t0 left unused, as the M1 tests do). Header ids follow
l2l3l4/parse.asm: h_eth=0.
"""

from nanuk.lang import MD_FLOOD, Header, MatchActionProgram

eth = Header("eth", dst=48, src=48, ethertype=16)

H_ETH = 0

# nanukproto full-encap outer header: outer Ethernet + nk header, 22 bytes,
# written as 16-bit words at h_frame-relative offsets (matches tunnel_push.asm).
_OUTER_WORDS = [
    (-22, 0x024E), (-20, 0x4B00), (-18, 0x0001),   # outer dst 02:4e:4b:00:00:01
    (-16, 0x024E), (-14, 0x4B00), (-12, 0x0002),   # outer src 02:4e:4b:00:00:02
    (-10, 0x88B5),                                  # nanukproto EtherType
    (-8, 0x4E4B), (-6, 0x1000), (-4, 0x0001),       # magic, ver/flags, tenant
    (-2, 0x6558),                                   # inner: full frame follows
]

NK_MAGIC = 0x4E4B
SMD_TENANT = 5  # parse_tunnel.asm: SMD slot 5 = magic when a tunnel was parsed


def make_tunnel_push() -> MatchActionProgram:
    mp = MatchActionProgram()
    mp.table("l2", key_width=48, action_width=8)  # t0: unused placeholder
    tun = mp.table("tun", key_width=48, action_width=8)  # t1
    ethh = mp.header(eth, hdr_id=H_ETH)

    @mp.state(start=True)
    def encap(s):
        act = s.lookup(tun, s.load(ethh.dst), miss=plain)
        for off, imm in _OUTER_WORDS:
            s.store(s.const(imm), hdr=15, byte_offset=off, nbytes=2)
        s.send(act, delta=22)

    @mp.state()
    def plain(s):
        s.send(s.load_md(MD_FLOOD))

    return mp


def make_tunnel_pop() -> MatchActionProgram:
    mp = MatchActionProgram()

    @mp.state(start=True)
    def check(s):
        tag = s.load_md(SMD_TENANT)
        s.dispatch(tag, {NK_MAGIC: strip}, default=plain)

    @mp.state()
    def strip(s):
        s.send(s.load_md(MD_FLOOD), delta=-22)

    @mp.state()
    def plain(s):
        s.send(s.load_md(MD_FLOOD))

    return mp
