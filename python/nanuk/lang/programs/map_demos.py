"""The three M1/M2 demo MAP programs, in the eDSL — the M3 parity gate.

Each factory mirrors one hand-written example under examples/: l2fwd
(examples/map_l2fwd/fwd.asm), ttl (examples/map_ttl/fwd.asm), tunnel push
and pop (examples/nanukproto/tunnel_*.asm). The hand-written .asm files
stay: they are the ISA-level teaching copies; these are the language-level
ones, and the parity tests hold the two behaviorally identical.

Table ids follow the examples' control-plane layout: l2fwd/ttl use t0 as
the L2 FDB; the tunnel push uses t1 (t0 left unused, as the M1 tests do).
Header ids follow examples/l2l3l4/parse.asm: h_eth=0, h_ipv4=2.
"""

from nanuk.lang import MD_FLOOD, MapProgram

from .l2l3l4 import eth, ipv4

H_ETH, H_IPV4 = 0, 2

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


def make_tunnel_push() -> MapProgram:
    mp = MapProgram()
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


def make_tunnel_pop() -> MapProgram:
    mp = MapProgram()

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
