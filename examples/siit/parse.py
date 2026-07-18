"""SIIT translator, parse side (PP), nanuk-lang edition. The hand-written ISA
copy is parse.asm next to this file; this twin is behaviorally identical
(verdict / error / md / recorded headers) over every committed conformance
vector, and pairs with translate.py.

Accepts exactly {IPv4, IPv6} and records which L4 rides inside; refuses what
parsing can *see* (the structural half of the ingress ledger): runt/truncated
frames die on the EXT/ADVANCE windows (a defined header-violation halt),
non-IP EtherType and IHL < 5 halt-drop. Everything value-shaped (checksums,
fragments, TTL, ICMP type, unsupported L4) is left to the MAP, so an
unsupported or fragment-carrying L4 is *accepted* here with only the L3 bit
set and the MAP keeps the reference ledger's order.

md convention (slot 0 stays the system's): slot 1 = header-present bitmap,
bit i = header id i. The bitmap literal is materialized with ``s.const`` and
emitted with ``s.smd`` — exactly what the hand asm does with MOVI + STMD.

Header ids are frozen; h_l4 = 7 is a program-pair alias base (a second SETHDR
at the L4 cursor) the MAP builds the outgoing v6 header backwards from. This
file is standalone: every constant is declared inline, nothing imported from
sibling examples.
"""

from nanuk.lang import Header, Parser

# --- headers (only the fields this PP reads need declaring) -----------------
eth = Header("eth", dst=48, src=48, ethertype=16)
ipv4 = Header("ipv4", version=4, ihl=4, tos=8, total_len=16, ident=16,
              flags_frag=16, ttl=8, proto=8, csum=16, src=32, dst=32)
ipv6 = Header("ipv6", version=4, tclass=8, flow=20, plen=16, nexthdr=8)
l4 = Header("l4", first=8)  # placeholder: only ever SETHDR'd (h_l4 / the L4 hdr)

# --- header ids (frozen part-A plan; h_l4 = 7 is the program-pair alias) -----
H_ETH, H_IPV4, H_IPV6, H_UDP, H_TCP, H_ICMP4, H_ICMP6, H_L4 = 0, 1, 2, 3, 4, 5, 6, 7

# --- EtherTypes / L4 protocol numbers ---------------------------------------
ETY_IPV4, ETY_IPV6 = 0x0800, 0x86DD
PROTO_UDP, PROTO_TCP, PROTO_ICMP4 = 17, 6, 1
NH_ICMP6 = 58

# --- md[1] header-present bitmaps (bit i = header id i present) --------------
BM_V4, BM_V4_UDP, BM_V4_TCP, BM_V4_ICMP = 0x03, 0x0B, 0x13, 0x23
BM_V6, BM_V6_UDP, BM_V6_TCP, BM_V6_ICMP = 0x05, 0x0D, 0x15, 0x45

SMD_BITMAP = 1


def make_parser() -> Parser:
    p = Parser()

    @p.state(start=True)
    def start(s):
        s.mark(eth, hdr_id=H_ETH)
        ety = s.extract(eth.ethertype)  # a runt frame dies on this EXT
        s.advance(14)
        s.dispatch(ety, {ETY_IPV4: ipv4_check, ETY_IPV6: ipv6_hdr},
                   default=s.drop)  # non_ip_ethertype

    # -- IPv4 ---------------------------------------------------------------
    @p.state()
    def ipv4_check(s):
        s.mark(ipv4, hdr_id=H_IPV4)  # recorded before validation (mirrors asm)
        ihl = s.extract(ipv4.ihl)
        # IHL 0..4: shorter than the fixed header — structurally not IPv4.
        s.dispatch(ihl, {0: bad, 1: bad, 2: bad, 3: bad, 4: bad},
                   default=ipv4_body)

    @p.state()
    def ipv4_body(s):
        s.mark(ipv4)  # re-anchor at the IPv4 base (cursor unmoved)
        ihl = s.extract(ipv4.ihl)
        proto = s.extract(ipv4.proto)
        s.advance(ihl << 2)  # skip header incl. options — the truncation check
        s.dispatch(proto, {PROTO_UDP: udp4, PROTO_TCP: tcp4, PROTO_ICMP4: icmp4},
                   default=v4_bare)  # unclaimed L4: MAP's ledger decides

    @p.state()
    def v4_bare(s):
        s.smd(s.const(BM_V4), slot=SMD_BITMAP)
        s.accept()

    @p.state()
    def udp4(s):
        s.mark(l4, hdr_id=H_L4)    # alias base for the MAP
        s.mark(l4, hdr_id=H_UDP)
        s.advance(8)               # minimal UDP header — the presence check
        s.smd(s.const(BM_V4_UDP), slot=SMD_BITMAP)
        s.accept()

    @p.state()
    def tcp4(s):
        s.mark(l4, hdr_id=H_L4)
        s.mark(l4, hdr_id=H_TCP)
        s.advance(20)
        s.smd(s.const(BM_V4_TCP), slot=SMD_BITMAP)
        s.accept()

    @p.state()
    def icmp4(s):
        s.mark(l4, hdr_id=H_L4)
        s.mark(l4, hdr_id=H_ICMP4)
        s.advance(4)               # type/code + checksum (echoes carry on)
        s.smd(s.const(BM_V4_ICMP), slot=SMD_BITMAP)
        s.accept()

    # -- IPv6 ---------------------------------------------------------------
    @p.state()
    def ipv6_hdr(s):
        s.mark(ipv6, hdr_id=H_IPV6)
        nh = s.extract(ipv6.nexthdr)
        s.advance(40)              # fixed header — the truncation check
        s.dispatch(nh, {PROTO_UDP: udp6, PROTO_TCP: tcp6, NH_ICMP6: icmp6},
                   default=v6_bare)  # unclaimed nh (incl. fragment 44)

    @p.state()
    def v6_bare(s):
        s.smd(s.const(BM_V6), slot=SMD_BITMAP)
        s.accept()

    @p.state()
    def udp6(s):
        s.mark(l4, hdr_id=H_L4)
        s.mark(l4, hdr_id=H_UDP)
        s.advance(8)
        s.smd(s.const(BM_V6_UDP), slot=SMD_BITMAP)
        s.accept()

    @p.state()
    def tcp6(s):
        s.mark(l4, hdr_id=H_L4)
        s.mark(l4, hdr_id=H_TCP)
        s.advance(20)
        s.smd(s.const(BM_V6_TCP), slot=SMD_BITMAP)
        s.accept()

    @p.state()
    def icmp6(s):
        s.mark(l4, hdr_id=H_L4)
        s.mark(l4, hdr_id=H_ICMP6)
        s.advance(4)
        s.smd(s.const(BM_V6_ICMP), slot=SMD_BITMAP)
        s.accept()

    @p.state()
    def bad(s):
        s.drop()  # IHL < 5: v4_truncated

    return p


def build_ir():
    """The nanuk.ir.v0 ParserProgram (for the interpreter/ISS/symex satellites)."""
    return make_parser().build_ir()


def build() -> str:
    return make_parser().compile()


if __name__ == "__main__":
    print(build(), end="")
