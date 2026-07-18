; SIIT translator, parse side (PP). Pairs with translate.asm.
;
; Accepts exactly {IPv4, IPv6} and records which L4 rides inside; refuses
; what parsing itself can see (the structural half of the ingress ledger):
;
;   runt / truncated headers  -> header-violation refusal (EXT/ADV past the
;                                frame end is a defined error halt -- the
;                                window IS the truncation check)
;   non-IP EtherType          -> halt drop
;   IHL < 5                   -> halt drop (not an IPv4 header at all)
;   L4 header physically cut  -> refusal via the post-SETHDR advance
;
; Everything value-shaped (v4 header checksum, fragments, TTL, zero UDP
; checksum, ICMP type, unsupported L4) is deliberately left to the MAP: an
; unsupported or fragment-carrying L4 is *accepted* here with only the L3
; bit set, so the MAP can keep the reference ledger's order (fragment
; outranks unsupported_l4). The IPv4 version field is not checked -- the
; reference translator never reads it, and this program mirrors the
; reference, not folk wisdom.
;
; Header ids (frozen in the part-A plan), plus one program-pair convention:
;   h_l4 = 7 is an alias base set at the start of whichever L4 was found.
;   The MAP builds the outgoing v6 header at negative offsets from it, which
;   is what makes one construction path serve every IHL (the v6 header must
;   END where L4 begins).
;
; md convention (slot 0 stays the system's): slot 1 = header-present bitmap,
; bit i = header id i. PP-possible values: 0x03/0x0B/0x13/0x23 (v4 alone /
; +udp / +tcp / +icmp4) and 0x05/0x0D/0x15/0x45 (v6 alone / +udp / +tcp /
; +icmp6). Slots 2..7 are MAP scratch (see translate.asm).
;
; The post-SETHDR ADVI doubles as the physical-presence check for the
; minimal L4 header the MAP will read: UDP 8, TCP 20, ICMP 4 (type/code +
; checksum -- the reference translates echoes with truncated id/seq, so 4,
; not 8).

.equ h_eth   0
.equ h_ipv4  1
.equ h_ipv6  2
.equ h_udp   3
.equ h_tcp   4
.equ h_icmp4 5
.equ h_icmp6 6
.equ h_l4    7

start:
    sethdr  h_eth
    ext     r0, 96, 16         ; EtherType (a runt frame dies right here)
    advi    14
    movi    r1, 0x0800
    beq     r0, r1, ipv4
    movi    r1, 0x86DD
    beq     r0, r1, ipv6
    halt    drop               ; non_ip_ethertype

ipv4:
    sethdr  h_ipv4
    ext     r1, 4, 4           ; IHL (pre-masked by bit-granular ext)
    beq     r1, rz, bad        ; IHL 0..4: shorter than the fixed header --
    movi    r2, 1              ; structurally not IPv4 (v4_truncated)
    beq     r1, r2, bad
    movi    r2, 2
    beq     r1, r2, bad
    movi    r2, 3
    beq     r1, r2, bad
    movi    r2, 4
    beq     r1, r2, bad
    ext     r2, 72, 8          ; protocol
    shl     r1, r1, 2          ; header length in bytes
    advr    r1                 ; skip header incl. options (truncation check)
    movi    r3, 17
    beq     r2, r3, udp4
    movi    r3, 6
    beq     r2, r3, tcp4
    movi    r3, 1
    beq     r2, r3, icmp4
    movi    r3, 0x03           ; v4 + unclaimed L4: MAP's ledger decides
    jmp     out

udp4:
    sethdr  h_l4
    sethdr  h_udp
    advi    8
    movi    r3, 0x0B
    jmp     out
tcp4:
    sethdr  h_l4
    sethdr  h_tcp
    advi    20
    movi    r3, 0x13
    jmp     out
icmp4:
    sethdr  h_l4
    sethdr  h_icmp4
    advi    4
    movi    r3, 0x23
    jmp     out

ipv6:
    sethdr  h_ipv6
    ext     r2, 48, 8          ; next header
    advi    40                 ; fixed header (truncation check)
    movi    r3, 17
    beq     r2, r3, udp6
    movi    r3, 6
    beq     r2, r3, tcp6
    movi    r3, 58
    beq     r2, r3, icmp6
    movi    r3, 0x05           ; v6 + unclaimed nh (incl. fragment 44)
    jmp     out

udp6:
    sethdr  h_l4
    sethdr  h_udp
    advi    8
    movi    r3, 0x0D
    jmp     out
tcp6:
    sethdr  h_l4
    sethdr  h_tcp
    advi    20
    movi    r3, 0x15
    jmp     out
icmp6:
    sethdr  h_l4
    sethdr  h_icmp6
    advi    4
    movi    r3, 0x45
    jmp     out

out:
    stmd    1, r3, 1           ; md[1] = header-present bitmap
    halt    accept

bad:
    halt    drop
