"""nanukproto parser: the standard L2/L3/L4 program extended with the
invented tunnel protocol (see README.md). Demonstrates that adding a
protocol to the switch is one header declaration and three states."""

from nanuk.lang import Header, Parser
from nanuk.examples.l2l3l4.parse import eth, ipv4, udp, vlan

nk = Header(
    "nanukproto",
    magic=16, version=4, flags=4, tenant_id=24, inner_ethertype=16,
)

H_ETH, H_VLAN, H_IPV4, H_UDP, H_NK = 0, 1, 2, 3, 5
SMD_DMAC, SMD_VLAN_TCI, SMD_L4_DPORT, SMD_TENANT = 0, 3, 4, 5
ETY_VLAN, ETY_IPV4, ETY_NK = 0x8100, 0x0800, 0x88B5
NK_MAGIC, NK_VERSION = 0x4E4B, 1
PROTO_UDP = 17


def make_parser() -> Parser:
    p = Parser()

    def l3_arms():
        return {ETY_VLAN: vlan_tag, ETY_IPV4: ipv4_check, ETY_NK: nk_magic}

    @p.state(start=True)
    def start(s):
        s.mark(eth, hdr_id=H_ETH)
        s.smd(s.extract(eth.dst), slot=SMD_DMAC)
        ety = s.extract(eth.ethertype)
        s.advance(eth.byte_len)
        s.dispatch(ety, l3_arms(), default=s.accept)

    @p.state()
    def vlan_tag(s):
        s.mark(vlan, hdr_id=H_VLAN)
        s.smd(s.extract(vlan.tci), slot=SMD_VLAN_TCI)
        ety = s.extract(vlan.ethertype)
        s.advance(vlan.byte_len)
        s.dispatch(ety, l3_arms(), default=s.accept)

    @p.state()
    def nk_magic(s):
        s.mark(nk, hdr_id=H_NK)
        magic = s.extract(nk.magic)
        s.dispatch(magic, {NK_MAGIC: nk_version}, default=s.drop)

    @p.state()
    def nk_version(s):
        s.mark(nk)  # re-anchor; header already recorded
        version = s.extract(nk.version)
        s.dispatch(version, {NK_VERSION: nk_body}, default=s.drop)

    @p.state()
    def nk_body(s):
        s.mark(nk)  # re-anchor
        s.smd(s.extract(nk.tenant_id), slot=SMD_TENANT)  # 24b -> slots 5-6
        iety = s.extract(nk.inner_ethertype)
        s.advance(nk.byte_len)
        s.dispatch(iety, {ETY_VLAN: vlan_tag, ETY_IPV4: ipv4_check},
                   default=s.accept)

    @p.state()
    def ipv4_check(s):
        s.mark(ipv4, hdr_id=H_IPV4)
        version = s.extract(ipv4.version)
        s.dispatch(version, {4: ipv4_body}, default=s.drop)

    @p.state()
    def ipv4_body(s):
        s.mark(ipv4)
        ihl = s.extract(ipv4.ihl)
        proto = s.extract(ipv4.proto)
        s.advance(ihl << 2)
        s.dispatch(proto, {PROTO_UDP: udp_hdr}, default=s.accept)

    @p.state()
    def udp_hdr(s):
        s.mark(udp, hdr_id=H_UDP)
        s.smd(s.extract(udp.dport), slot=SMD_L4_DPORT)
        s.advance(udp.byte_len)
        s.accept()

    return p


def build_ir():
    """The nanuk.ir.v0 Program (for satellites: interpreter, playground)."""
    return make_parser().build_ir()


def build() -> str:
    return make_parser().compile()


if __name__ == "__main__":
    print(build(), end="")
