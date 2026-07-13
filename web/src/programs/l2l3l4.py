# The Nanuk demo parser: Ethernet -> 802.1Q (incl. QinQ) -> IPv4 (with
# options) -> UDP. Edit me and watch the IR and assembly panes follow.
#
# Header ids: eth=0 vlan=1 ipv4=2 udp=3
# SMD slots:  0-2 DMAC | 3 outermost-last VLAN TCI | 4 UDP dport

from nanuk.lang import Header, Parser

eth = Header("eth", dst=48, src=48, ethertype=16)
vlan = Header("vlan", tci=16, ethertype=16)
ipv4 = Header(
    "ipv4",
    version=4, ihl=4, tos=8, total_len=16, ident=16,
    flags_frag=16, ttl=8, proto=8, csum=16, src=32, dst=32,
)
udp = Header("udp", sport=16, dport=16, length=16, csum=16)

H_ETH, H_VLAN, H_IPV4, H_UDP = 0, 1, 2, 3
SMD_DMAC, SMD_VLAN_TCI, SMD_L4_DPORT = 1, 4, 5  # slot 0 is the system's
ETY_VLAN, ETY_IPV4 = 0x8100, 0x0800
PROTO_UDP = 17


def make_parser() -> Parser:
    """Wire up the demo parser (states + transitions), ready to compile."""
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(eth, hdr_id=H_ETH)
        s.smd(s.extract(eth.dst), slot=SMD_DMAC)
        ety = s.extract(eth.ethertype)
        s.advance(eth.byte_len)
        s.dispatch(ety, {ETY_VLAN: vlan_tag, ETY_IPV4: ipv4_check},
                   default=s.accept)  # unknown L3: accept with what we know

    @p.state()
    def vlan_tag(s):
        # Cursor sits at the TCI (the TPID was consumed as the previous
        # EtherType). For stacked VLANs the loop overwrites offset and TCI:
        # the *last* tag wins — a deliberate v0 simplification.
        s.mark(vlan, hdr_id=H_VLAN)
        s.smd(s.extract(vlan.tci), slot=SMD_VLAN_TCI)
        ety = s.extract(vlan.ethertype)
        s.advance(vlan.byte_len)
        s.dispatch(ety, {ETY_VLAN: vlan_tag, ETY_IPV4: ipv4_check},
                   default=s.accept)  # QinQ loop — bounded by the step budget

    @p.state()
    def ipv4_check(s):
        s.mark(ipv4, hdr_id=H_IPV4)  # recorded before validation, v0 semantics
        version = s.extract(ipv4.version)
        s.dispatch(version, {4: ipv4_body}, default=s.drop)

    @p.state()
    def ipv4_body(s):
        s.mark(ipv4)  # re-anchor only; SETHDR already done in ipv4_check
        ihl = s.extract(ipv4.ihl)
        proto = s.extract(ipv4.proto)
        s.advance(ihl << 2)  # header length in bytes; skips options
        s.dispatch(proto, {PROTO_UDP: udp_hdr},
                   default=s.accept)  # non-UDP L4: accept, payload = L4 start

    @p.state()
    def udp_hdr(s):
        s.mark(udp, hdr_id=H_UDP)
        s.smd(s.extract(udp.dport), slot=SMD_L4_DPORT)
        s.advance(udp.byte_len)
        s.accept()

    return p


def build_ir():
    """The demo as a nanuk.ir.v0 Program proto."""
    return make_parser().build_ir()
