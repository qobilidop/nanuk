"""SIIT translator - a MAP (match-action) program: translate a packet across
address families in your browser.

This is the match-action side of the Nanuk SIIT translator (RFC 7915 stateless
IP/ICMP translation). The playground composes it behind the SIIT parse-side
parser, exactly as a CLAT would sit on the wire:

    packet -> SIIT parser -> (on accept) -> this SIIT translator

The parser refuses everything *structural* (runt / truncated frames, non-IP
EtherType, IHL < 5) and records which L4 rides inside; this MAP owns every
*value* decision (checksums, fragments, TTL, ICMP type, unsupported L4) and
does the RFC 7915 header rewrite in place: IPv4 <-> IPv6, RFC 7757 EAMT
(exact-match tables t0/t1/t2) with RFC 6052 pool6 (64:ff9b::/96) as the
fallback, and RFC 1624 incremental L4 checksum patching. Every drop is an
explicit DROP - the translator is a middlebox function, not a forwarder, so
md[0] (system egress) is never written.

The demo control plane preloads the EAMT with one exact host pair:

    192.0.2.1  <->  2001:db8:1::c001

Everything else translates through the /96 Well-Known Prefix. Try the presets:
a v4->v6 UDP packet (6052), a v6->v4 UDP packet, a v4 packet hitting the EAMT,
a v4 ICMP echo, and a TTL=1 packet (dropped, ttl_expired). Then read the panes:
each nanuk-lang state lowers to Nanuk IR, then Nanuk asm, and the debugger walks
one run across both engines.

Two nanuk-lang idioms carry the arithmetic the raw ISA spells by hand:

  * The ISA has no register-register compare, only compare-to-immediate, so a
    128/64-bit equality (``dst high 64 == pool6 prefix?``) becomes
    ``dispatch(s.bin_op("sub", a, b), {0: equal}, default=...)`` - the BEQ
    against zero.
  * The RFC 1624 end-around-carry fold is done by writing the wide accumulator
    to headroom scratch and running CSUM over it: CSUM returns ~fold16 of a
    byte range, which is precisely HC' = ~fold(~HC + ~m + m'). Scratch lives in
    the 32-byte headroom below the outgoing frame, so it never reaches the wire.

md convention: md[1] = PP header bitmap (read, never rewritten); md[2] =
payload length (v6 view); md[3] = TTL/hop; md[4] = A = ~sum16(old addr bytes);
md[5] = IHL bytes (v4 arm); md[6] = TOS (v4 arm).
"""

from nanuk.lang import MatchActionProgram

# --- header ids (frozen; h_l4 = 7 is the PP's L4-start alias base) -----------
H_IPV4, H_IPV6, H_UDP, H_TCP, H_ICMP4, H_ICMP6, H_L4 = 1, 2, 3, 4, 5, 6, 7
H_FRAME = 15  # the always-valid frame-start base

# --- EAMT table ids (frozen part-A table plane) -----------------------------
T_EAMT46_HI, T_EAMT46_LO, T_EAMT64 = 0, 1, 2

# --- md slots ---------------------------------------------------------------
MD_BITMAP, MD_PLEN, MD_TTL, MD_A, MD_IHL, MD_TOS = 1, 2, 3, 4, 5, 6

# --- PP header bitmaps (md[1]) ----------------------------------------------
BM_V4_UDP, BM_V4_TCP, BM_V4_ICMP = 0x0B, 0x13, 0x23
BM_V6_UDP, BM_V6_TCP, BM_V6_ICMP = 0x0D, 0x15, 0x45

# --- protocol / next-header numbers -----------------------------------------
PROTO_UDP, PROTO_TCP, PROTO_ICMP4, NH_ICMP6, NH_FRAGMENT = 17, 6, 1, 58, 44

# --- EtherTypes -------------------------------------------------------------
ETY_IPV4, ETY_IPV6 = 0x0800, 0x86DD

# --- constants ---------------------------------------------------------------
MASK16 = 0xFFFF
POOL6_HI16A, POOL6_HI16B = 0x0064, 0xFF9B  # 64:ff9b::/96 high 64 = 0x0064FF9B_00000000
SCRATCH = -32           # headroom fold-scratch (8 bytes, below the egress frame)

# ICMP type/code words
ICMP4_ECHO_REQ, ICMP4_ECHO_REPLY = 0x0800, 0x0000
ICMP6_ECHO_REQ, ICMP6_ECHO_REPLY = 0x8000, 0x8100


def _pool6_hi(s):
    """The 64:ff9b::/96 high 64 bits, 0x0064FF9B00000000, from immediates."""
    p = s.shift(s.const(POOL6_HI16A), 16)     # 0x00640000
    p = s.bin_op("or", p, s.const(POOL6_HI16B))  # 0x0064FF9B
    return s.shift(p, 32)                      # 0x0064FF9B00000000


def _fold_complement(s, acc):
    """HC' = ~fold16(acc): spill the wide accumulator to headroom scratch and
    let CSUM do the RFC 1071 end-around-carry fold (returning the complement)."""
    s.store(acc, hdr=H_FRAME, byte_offset=SCRATCH, nbytes=8)
    return s.csum(s.const(8), hdr=H_FRAME, byte_offset=SCRATCH)


def build_map() -> MatchActionProgram:
    mp = MatchActionProgram()
    t46hi = mp.table("eamt46_hi", key_width=32, action_width=64, table_id=T_EAMT46_HI)
    t46lo = mp.table("eamt46_lo", key_width=32, action_width=64, table_id=T_EAMT46_LO)
    t64 = mp.table("eamt64", key_width=64, action_width=32, table_id=T_EAMT64)

    # ===================================================================
    # entry
    # ===================================================================
    @mp.state(start=True)
    def entry(s):
        bm = s.load_md(MD_BITMAP)
        s.dispatch(s.and_imm(bm, 2), {2: v4arm}, default=entry_v6)

    @mp.state()
    def entry_v6(s):
        bm = s.load_md(MD_BITMAP)
        s.dispatch(s.and_imm(bm, 4), {4: v6arm}, default=refuse)

    # ===================================================================
    # v4 -> v6
    # ===================================================================
    @mp.state()
    def v4arm(s):
        # ledger (b): the v4 header checksum must verify (fold == 0xFFFF).
        vihl = s.load(None, hdr=H_IPV4, byte_offset=0, nbytes=1)
        ihl = s.shift(s.and_imm(vihl, 0x0F), 2)   # IHL in bytes (PP >= 20)
        s.store_md(ihl, MD_IHL)
        csv = s.csum(ihl, hdr=H_IPV4, byte_offset=0)
        s.dispatch(csv, {0: v4_len}, default=refuse)  # nonzero fold -> bad csum

    @mp.state()
    def v4_len(s):
        total = s.load(None, hdr=H_IPV4, byte_offset=2, nbytes=2)
        plen = s.bin_op("sub", total, s.load_md(MD_IHL))
        s.store_md(plen, MD_PLEN)                  # md[2] = payload length
        s.dispatch(s.and_imm(plen, 0x8000), {0: v4_frag}, default=refuse)

    @mp.state()
    def v4_frag(s):
        ff = s.load(None, hdr=H_IPV4, byte_offset=6, nbytes=2)
        s.dispatch(s.and_imm(ff, 0x3FFF), {0: v4_l4}, default=refuse)  # MF/offset

    @mp.state()
    def v4_l4(s):
        bm = s.load_md(MD_BITMAP)
        s.dispatch(bm, {BM_V4_UDP: chk_udp4, BM_V4_TCP: chk_ttl4,
                        BM_V4_ICMP: chk_icmp4}, default=refuse)  # unsupported_l4

    @mp.state()
    def chk_udp4(s):
        uc = s.load(None, hdr=H_UDP, byte_offset=6, nbytes=2)
        s.dispatch(uc, {0: refuse}, default=chk_ttl4)  # zero_udp_checksum

    @mp.state()
    def chk_icmp4(s):
        w = s.load(None, hdr=H_ICMP4, byte_offset=0, nbytes=2)   # type/code word
        # echo reply (0x0000) or request (0x0800) translate; anything else defers
        s.dispatch(w, {ICMP4_ECHO_REPLY: chk_ttl4, ICMP4_ECHO_REQ: chk_ttl4},
                   default=refuse)

    @mp.state()
    def chk_ttl4(s):
        # md[3] is written only *after* this refuse (hand asm order), so a
        # ttl_expired drop leaves md[3] = 0.
        ttl = s.load(None, hdr=H_IPV4, byte_offset=8, nbytes=1)
        s.dispatch(s.and_imm(ttl, 0xFE), {0: refuse}, default=v4_stash)  # TTL <= 1

    @mp.state()
    def v4_stash(s):
        s.store_md(s.load(None, hdr=H_IPV4, byte_offset=8, nbytes=1), MD_TTL)  # md[3]=TTL
        s.store_md(s.load(None, hdr=H_IPV4, byte_offset=1, nbytes=1), MD_TOS)  # md[6] = TOS
        # stash src4+dst4 into headroom before the v6-header stores clobber them
        s.store(s.load(None, hdr=H_IPV4, byte_offset=12, nbytes=8),
                hdr=H_FRAME, byte_offset=-32, nbytes=8)
        s.store_md(s.csum(s.const(8), hdr=H_IPV4, byte_offset=12), MD_A)  # md[4]=A
        s.goto(v4_reloc)

    @mp.state()
    def v4_reloc(s):
        # Ethernet must end where the v6 header begins. Both loads before either
        # store (IHL 11/12 overlaps), then only the EtherType changes.
        mac_hi = s.load(None, hdr=H_FRAME, byte_offset=0, nbytes=8)
        mac_lo = s.load(None, hdr=H_FRAME, byte_offset=8, nbytes=4)
        s.store(mac_hi, hdr=H_L4, byte_offset=-54, nbytes=8)
        s.store(mac_lo, hdr=H_L4, byte_offset=-46, nbytes=4)
        s.store(s.const(ETY_IPV6), hdr=H_L4, byte_offset=-42, nbytes=2)
        s.goto(v4_vhdr)

    @mp.state()
    def v4_vhdr(s):
        # version 6 | traffic class = TOS | flow label 0
        vtc = s.shift(s.load_md(MD_TOS), 20)
        word = s.bin_op("or", vtc, s.shift(s.const(0x6000), 16))
        s.store(word, hdr=H_L4, byte_offset=-40, nbytes=4)
        s.store(s.load_md(MD_PLEN), hdr=H_L4, byte_offset=-36, nbytes=2)
        bm = s.load_md(MD_BITMAP)
        s.dispatch(bm, {BM_V4_UDP: v4_nh_udp, BM_V4_TCP: v4_nh_tcp},
                   default=v4_nh_icmp)

    @mp.state()
    def v4_nh_udp(s):
        s.store(s.const(PROTO_UDP), hdr=H_L4, byte_offset=-34, nbytes=1)
        s.goto(v4_hop)

    @mp.state()
    def v4_nh_tcp(s):
        s.store(s.const(PROTO_TCP), hdr=H_L4, byte_offset=-34, nbytes=1)
        s.goto(v4_hop)

    @mp.state()
    def v4_nh_icmp(s):
        s.store(s.const(NH_ICMP6), hdr=H_L4, byte_offset=-34, nbytes=1)  # 1 -> 58
        s.goto(v4_hop)

    @mp.state()
    def v4_hop(s):
        s.store(s.add(s.load_md(MD_TTL), -1), hdr=H_L4, byte_offset=-33, nbytes=1)
        s.goto(v4_src)

    @mp.state()
    def v4_src(s):
        src4 = s.load(None, hdr=H_FRAME, byte_offset=-32, nbytes=4)
        hi = s.lookup(t46hi, src4, miss=v4_src_embed)
        lo = s.lookup(t46lo, src4, miss=v4_src_embed)
        s.store(hi, hdr=H_L4, byte_offset=-32, nbytes=8)
        s.store(lo, hdr=H_L4, byte_offset=-24, nbytes=8)
        s.goto(v4_dst)

    @mp.state()
    def v4_src_embed(s):
        src4 = s.load(None, hdr=H_FRAME, byte_offset=-32, nbytes=4)
        s.store(_pool6_hi(s), hdr=H_L4, byte_offset=-32, nbytes=8)
        s.store(src4, hdr=H_L4, byte_offset=-24, nbytes=8)  # low 64 = v4 addr
        s.goto(v4_dst)

    @mp.state()
    def v4_dst(s):
        dst4 = s.load(None, hdr=H_FRAME, byte_offset=-28, nbytes=4)
        hi = s.lookup(t46hi, dst4, miss=v4_dst_embed)
        lo = s.lookup(t46lo, dst4, miss=v4_dst_embed)
        s.store(hi, hdr=H_L4, byte_offset=-16, nbytes=8)
        s.store(lo, hdr=H_L4, byte_offset=-8, nbytes=8)
        s.goto(v4_patch)

    @mp.state()
    def v4_dst_embed(s):
        dst4 = s.load(None, hdr=H_FRAME, byte_offset=-28, nbytes=4)
        s.store(_pool6_hi(s), hdr=H_L4, byte_offset=-16, nbytes=8)
        s.store(dst4, hdr=H_L4, byte_offset=-8, nbytes=8)
        s.goto(v4_patch)

    @mp.state()
    def v4_patch(s):
        bm = s.load_md(MD_BITMAP)
        s.dispatch(bm, {BM_V4_UDP: pat_udp4, BM_V4_TCP: pat_tcp4},
                   default=pat_icmp4)

    # --- v4 L4 checksum patches: HC' = ~fold(~HC + A + ~B) ----------------
    def _addr_patch4(s, hdr, off):
        b = s.csum(s.const(32), hdr=H_L4, byte_offset=-32)  # B = ~sum(new addrs)
        not_b = s.bin_op("xor", b, s.const(MASK16))          # ~B = sum(new)
        not_hc = s.bin_op("xor", s.load(None, hdr=hdr, byte_offset=off, nbytes=2),
                          s.const(MASK16))
        acc = s.bin_op("add", s.bin_op("add", not_hc, s.load_md(MD_A)), not_b)
        hcp = _fold_complement(s, acc)
        s.store(hcp, hdr=hdr, byte_offset=off, nbytes=2)
        return hcp

    @mp.state()
    def pat_udp4(s):
        hcp = _addr_patch4(s, H_UDP, 6)
        s.dispatch(hcp, {0: udp4_zero}, default=send46)  # RFC 768: 0 -> 0xFFFF

    @mp.state()
    def udp4_zero(s):
        s.store(s.const(MASK16), hdr=H_UDP, byte_offset=6, nbytes=2)
        s.goto(send46)

    @mp.state()
    def pat_tcp4(s):
        _addr_patch4(s, H_TCP, 16)
        s.goto(send46)

    @mp.state()
    def pat_icmp4(s):
        w = s.load(None, hdr=H_ICMP4, byte_offset=0, nbytes=2)
        s.dispatch(w, {ICMP4_ECHO_REPLY: picmp4_reply}, default=picmp4_req)

    # ICMPv4 -> ICMPv6: HC' = ~fold(~HC + ~old + new + sum(newaddrs) + plen + 58)
    @mp.state()
    def picmp4_reply(s):  # old 0x0000 -> new 0x8100 (129); ~old = 0xFFFF
        s.store(s.const(ICMP6_ECHO_REPLY), hdr=H_ICMP4, byte_offset=0, nbytes=2)
        sum_new = s.bin_op("xor", s.csum(s.const(32), hdr=H_L4, byte_offset=-32),
                           s.const(MASK16))
        not_hc = s.bin_op("xor", s.load(None, hdr=H_ICMP4, byte_offset=2, nbytes=2),
                          s.const(MASK16))
        acc = s.bin_op("add", not_hc, sum_new)
        acc = s.bin_op("add", acc, s.load_md(MD_PLEN))
        acc = s.bin_op("add", acc, s.const(MASK16))                # + ~old (0xFFFF)
        acc = s.bin_op("add", acc, s.const(ICMP6_ECHO_REPLY + 58))  # + new + nexthdr
        hcp = _fold_complement(s, acc)
        s.store(hcp, hdr=H_ICMP4, byte_offset=2, nbytes=2)
        s.dispatch(hcp, {0: picmp4_reply_zero}, default=send46)

    @mp.state()
    def picmp4_reply_zero(s):
        s.store(s.const(MASK16), hdr=H_ICMP4, byte_offset=2, nbytes=2)
        s.goto(send46)

    @mp.state()
    def picmp4_req(s):  # old 0x0800 -> new 0x8000 (128); ~old = 0xF7FF
        s.store(s.const(ICMP6_ECHO_REQ), hdr=H_ICMP4, byte_offset=0, nbytes=2)
        sum_new = s.bin_op("xor", s.csum(s.const(32), hdr=H_L4, byte_offset=-32),
                           s.const(MASK16))
        not_hc = s.bin_op("xor", s.load(None, hdr=H_ICMP4, byte_offset=2, nbytes=2),
                          s.const(MASK16))
        acc = s.bin_op("add", not_hc, sum_new)
        acc = s.bin_op("add", acc, s.load_md(MD_PLEN))
        acc = s.bin_op("add", acc, s.const(MASK16 ^ ICMP4_ECHO_REQ))  # ~old = 0xF7FF
        acc = s.bin_op("add", acc, s.const(ICMP6_ECHO_REQ + 58))      # + new + nexthdr
        hcp = _fold_complement(s, acc)
        s.store(hcp, hdr=H_ICMP4, byte_offset=2, nbytes=2)
        s.dispatch(hcp, {0: picmp4_req_zero}, default=send46)

    @mp.state()
    def picmp4_req_zero(s):
        s.store(s.const(MASK16), hdr=H_ICMP4, byte_offset=2, nbytes=2)
        s.goto(send46)

    # --- send: delta = 40 - IHL, dispatched on IHL bytes (md[5]) ----------
    @mp.state()
    def send46(s):
        ihl = s.load_md(MD_IHL)
        s.dispatch(ihl, {20: s20, 24: s16, 28: s12, 32: s8, 36: s4, 40: s0,
                         44: sm4, 48: sm8, 52: sm12, 56: sm16}, default=sm20)

    @mp.state()
    def s20(s):
        s.send(delta=20)

    @mp.state()
    def s16(s):
        s.send(delta=16)

    @mp.state()
    def s12(s):
        s.send(delta=12)

    @mp.state()
    def s8(s):
        s.send(delta=8)

    @mp.state()
    def s4(s):
        s.send(delta=4)

    @mp.state()
    def s0(s):
        s.send(delta=0)

    @mp.state()
    def sm4(s):
        s.send(delta=-4)

    @mp.state()
    def sm8(s):
        s.send(delta=-8)

    @mp.state()
    def sm12(s):
        s.send(delta=-12)

    @mp.state()
    def sm16(s):
        s.send(delta=-16)

    @mp.state()
    def sm20(s):
        s.send(delta=-20)  # IHL 60

    # ===================================================================
    # v6 -> v4
    # ===================================================================
    @mp.state()
    def v6arm(s):
        nh = s.load(None, hdr=H_IPV6, byte_offset=6, nbytes=1)   # next header
        s.dispatch(nh, {NH_FRAGMENT: refuse}, default=v6_len)  # fragment

    @mp.state()
    def v6_len(s):
        s.store_md(s.load(None, hdr=H_IPV6, byte_offset=4, nbytes=2), MD_PLEN)  # md[2]
        bm = s.load_md(MD_BITMAP)
        s.dispatch(bm, {BM_V6_UDP: chk_ttl6, BM_V6_TCP: chk_ttl6,
                        BM_V6_ICMP: chk_icmp6}, default=refuse)  # unsupported_l4

    @mp.state()
    def chk_icmp6(s):
        w = s.load(None, hdr=H_ICMP6, byte_offset=0, nbytes=2)
        s.dispatch(w, {ICMP6_ECHO_REQ: chk_ttl6, ICMP6_ECHO_REPLY: chk_ttl6},
                   default=refuse)  # icmp_error

    @mp.state()
    def chk_ttl6(s):
        # md[3] is written only after this refuse (hand asm order).
        hop = s.load(None, hdr=H_IPV6, byte_offset=7, nbytes=1)
        s.dispatch(s.and_imm(hop, 0xFE), {0: refuse}, default=v6_tc)  # hop <= 1

    @mp.state()
    def v6_tc(s):
        s.store_md(s.load(None, hdr=H_IPV6, byte_offset=7, nbytes=1), MD_TTL)  # md[3]=hop
        # traffic class = bits [11:4] of the first two bytes; store-2 / reload-1
        # is the byte-granular right-shift-by-8 the ISA lacks.
        tc = s.and_imm(s.shift(s.load(None, hdr=H_IPV6, byte_offset=0, nbytes=2), 4),
                       0xFF00)
        s.store(tc, hdr=H_FRAME, byte_offset=-24, nbytes=2)
        s.store_md(s.csum(s.const(32), hdr=H_IPV6, byte_offset=8), MD_A)  # md[4]=A
        s.goto(v6_dst)

    @mp.state()
    def v6_dst(s):
        d0 = s.load(None, hdr=H_IPV6, byte_offset=24, nbytes=8)   # dst bytes 0..7
        s.dispatch(s.bin_op("sub", d0, _pool6_hi(s)), {0: v6_dst_chk2},
                   default=v6_dst_eamt)

    @mp.state()
    def v6_dst_chk2(s):
        mid = s.load(None, hdr=H_IPV6, byte_offset=32, nbytes=4)  # bytes 8..11
        s.dispatch(mid, {0: v6_dst_embed}, default=v6_dst_eamt)

    @mp.state()
    def v6_dst_embed(s):
        s.store(s.load(None, hdr=H_IPV6, byte_offset=36, nbytes=4),
                hdr=H_FRAME, byte_offset=-32, nbytes=4)  # scratch dst4
        s.goto(v6_src)

    @mp.state()
    def v6_dst_eamt(s):
        key = s.load(None, hdr=H_IPV6, byte_offset=32, nbytes=8)  # low 64
        v4 = s.lookup(t64, key, miss=refuse)                # untranslatable_address
        s.store(v4, hdr=H_FRAME, byte_offset=-32, nbytes=4)
        s.goto(v6_src)

    @mp.state()
    def v6_src(s):
        s0v = s.load(None, hdr=H_IPV6, byte_offset=8, nbytes=8)   # src bytes 0..7
        s.dispatch(s.bin_op("sub", s0v, _pool6_hi(s)), {0: v6_src_chk2},
                   default=v6_src_eamt)

    @mp.state()
    def v6_src_chk2(s):
        mid = s.load(None, hdr=H_IPV6, byte_offset=16, nbytes=4)
        s.dispatch(mid, {0: v6_src_embed}, default=v6_src_eamt)

    @mp.state()
    def v6_src_embed(s):
        s.store(s.load(None, hdr=H_IPV6, byte_offset=20, nbytes=4),
                hdr=H_FRAME, byte_offset=-28, nbytes=4)  # scratch src4
        s.goto(v6_build)

    @mp.state()
    def v6_src_eamt(s):
        key = s.load(None, hdr=H_IPV6, byte_offset=16, nbytes=8)
        v4 = s.lookup(t64, key, miss=refuse)
        s.store(v4, hdr=H_FRAME, byte_offset=-28, nbytes=4)
        s.goto(v6_build)

    @mp.state()
    def v6_build(s):
        # relocate Ethernet +20 (disjoint move; only the EtherType changes)
        s.store(s.load(None, hdr=H_FRAME, byte_offset=0, nbytes=8),
                hdr=H_FRAME, byte_offset=20, nbytes=8)
        s.store(s.load(None, hdr=H_FRAME, byte_offset=8, nbytes=4),
                hdr=H_FRAME, byte_offset=28, nbytes=4)
        s.store(s.const(ETY_IPV4), hdr=H_FRAME, byte_offset=32, nbytes=2)
        # v4 header at frame 34..54
        tc = s.load(None, hdr=H_FRAME, byte_offset=-24, nbytes=1)     # TC (reload-1)
        s.store(s.bin_op("or", tc, s.const(0x4500)),
                hdr=H_FRAME, byte_offset=34, nbytes=2)          # v4/IHL5/TOS
        s.store(s.add(s.load_md(MD_PLEN), 20),
                hdr=H_FRAME, byte_offset=36, nbytes=2)          # total length
        s.store(s.const(0), hdr=H_FRAME, byte_offset=38, nbytes=2)   # identification
        s.store(s.const(0x4000), hdr=H_FRAME, byte_offset=40, nbytes=2)  # DF
        s.store(s.const(0), hdr=H_FRAME, byte_offset=44, nbytes=2)   # csum field = 0
        s.store(s.load(None, hdr=H_FRAME, byte_offset=-28, nbytes=4),
                hdr=H_FRAME, byte_offset=46, nbytes=4)          # src
        s.store(s.load(None, hdr=H_FRAME, byte_offset=-32, nbytes=4),
                hdr=H_FRAME, byte_offset=50, nbytes=4)          # dst
        bm = s.load_md(MD_BITMAP)
        s.dispatch(bm, {BM_V6_UDP: v6_prot_udp, BM_V6_TCP: v6_prot_tcp},
                   default=v6_prot_icmp)

    def _v6_prot(s, proto):
        ttl = s.shift(s.add(s.load_md(MD_TTL), -1), 8)   # TTL = hop - 1, << 8
        s.store(s.bin_op("or", ttl, s.const(proto)),
                hdr=H_FRAME, byte_offset=42, nbytes=2)   # TTL, protocol
        s.goto(v6_csum)

    @mp.state()
    def v6_prot_udp(s):
        _v6_prot(s, PROTO_UDP)

    @mp.state()
    def v6_prot_tcp(s):
        _v6_prot(s, PROTO_TCP)

    @mp.state()
    def v6_prot_icmp(s):
        _v6_prot(s, PROTO_ICMP4)   # 58 -> 1

    @mp.state()
    def v6_csum(s):
        s.store(s.csum(s.const(20), hdr=H_FRAME, byte_offset=34),
                hdr=H_FRAME, byte_offset=44, nbytes=2)   # fresh header checksum
        bm = s.load_md(MD_BITMAP)
        s.dispatch(bm, {BM_V6_UDP: pat_udp6, BM_V6_TCP: pat_tcp6},
                   default=pat_icmp6)

    # --- v6 L4 checksum patches: HC' = ~fold(~HC + A + ~B); no v4 zero rule -
    def _addr_patch6(s, hdr, off):
        b = s.csum(s.const(8), hdr=H_FRAME, byte_offset=46)  # B = ~sum(new v4 addrs)
        not_b = s.bin_op("xor", b, s.const(MASK16))
        not_hc = s.bin_op("xor", s.load(None, hdr=hdr, byte_offset=off, nbytes=2),
                          s.const(MASK16))
        acc = s.bin_op("add", s.bin_op("add", not_hc, s.load_md(MD_A)), not_b)
        s.store(_fold_complement(s, acc), hdr=hdr, byte_offset=off, nbytes=2)

    @mp.state()
    def pat_udp6(s):
        _addr_patch6(s, H_UDP, 6)
        s.send(delta=-20)

    @mp.state()
    def pat_tcp6(s):
        _addr_patch6(s, H_TCP, 16)
        s.send(delta=-20)

    @mp.state()
    def pat_icmp6(s):
        w = s.load(None, hdr=H_ICMP6, byte_offset=0, nbytes=2)
        s.dispatch(w, {ICMP6_ECHO_REQ: picmp6_req}, default=picmp6_reply)

    # ICMPv6 -> ICMPv4 (removes the v6 pseudo-header):
    #   ~m = ~fold(old + sum(oldaddrs) + plen + 58);  HC' = ~fold(~HC + ~m + new)
    def _icmp6_patch(s, old_plus_58, new_word):
        s.store(s.const(new_word), hdr=H_ICMP6, byte_offset=0, nbytes=2)
        sum_old = s.bin_op("xor", s.load_md(MD_A), s.const(MASK16))  # sum(old addrs)
        accm = s.bin_op("add", sum_old, s.load_md(MD_PLEN))
        accm = s.bin_op("add", accm, s.const(old_plus_58))
        not_m = _fold_complement(s, accm)
        not_hc = s.bin_op("xor", s.load(None, hdr=H_ICMP6, byte_offset=2, nbytes=2),
                          s.const(MASK16))
        acc = s.bin_op("add", s.bin_op("add", not_hc, not_m), s.const(new_word))
        s.store(_fold_complement(s, acc), hdr=H_ICMP6, byte_offset=2, nbytes=2)
        s.send(delta=-20)

    @mp.state()
    def picmp6_req(s):   # old 0x8000 (128) -> new 0x0800 (8)
        _icmp6_patch(s, ICMP6_ECHO_REQ + 58, ICMP4_ECHO_REQ)

    @mp.state()
    def picmp6_reply(s):  # old 0x8100 (129) -> new 0x0000 (0)
        _icmp6_patch(s, ICMP6_ECHO_REPLY + 58, ICMP4_ECHO_REPLY)

    # ===================================================================
    @mp.state()
    def refuse(s):
        s.drop()

    return mp


def build_map_ir():
    """The MatchActionProgram IR the playground bridge compiles and runs."""
    return build_map().build_ir()
