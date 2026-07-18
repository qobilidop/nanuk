; SIIT translator, match-action side (MAP). Pairs with parse.asm.
;
; RFC 7915 stateless IP/ICMP translation on the Nanuk core: IPv4 <-> IPv6
; header rewrite in place, RFC 7757 EAMT (exact-match, tables t0/t1/t2)
; with RFC 6052 pool6 (64:ff9b::/96, baked as immediates) as the fallback,
; incremental RFC 1624 L4 checksum patching, and the value half of the
; ingress ledger (the PP already refused everything structural):
;
;   v4 arm: header checksum -> fragment -> zero UDP checksum / ICMP
;           non-echo / unsupported L4 -> TTL <= 1
;   v6 arm: fragment header -> ICMP non-echo / unsupported L4 -> hop <= 1
;           -> untranslatable address (EAMT miss on a non-pool6 address)
;
; every drop is an explicit DROP -- the error paths of the machine are never
; used as a verdict.
;
; Geometry (why the two arms address differently):
;   v4 -> v6 grows the head:  new frame = old start - (40 - IHL).  The v6
;     header must END where L4 begins, so the whole construction is at
;     NEGATIVE offsets from h_l4 (the PP's L4-start alias base) -- one code
;     path serves every IHL, options simply not copied.  SEND delta is
;     40 - IHL, dispatched to an immediate (deltas are compile-time; the
;     full IHL 20..60 chain is at `send46`).
;   v6 -> v4 shrinks the head: new frame = old start + 20, all offsets are
;     frame-absolute (the v6 header is fixed 40B).  SEND -20.
;   The Ethernet relocation is safe in both directions, but not for the same
;   reason.  v6 -> v4 moves by a fixed +20, strictly past the 14-byte header,
;   so source and destination never overlap.  v4 -> v6 moves by 40 - IHL,
;   which is NOT always past 14 -- for IHL 11/12 (44/48-byte v4 header) the
;   destination lands inside [8,12) of the source, i.e. inside the still-
;   unread second half of the source MAC.  That arm is made safe instead by
;   ordering: both loads happen before either store, so no store can ever
;   clobber bytes the other load still needs (contrast srcroute's 2-byte
;   pop, where the copy direction mattered).  Only the EtherType changes;
;   MACs pass through.
;
; Register budget (4 GPRs): r0 = the value being decided on (bitmap, then
; addresses, then checksum accumulator); r1/r2 = immediates and staging;
; r3 = long-lived per-phase value (payload_len at ingress, addr-range csum
; B in the patch phase).  Spills go to the md window and headroom scratch:
;
;   md[1] = PP header bitmap   md[2] = payload length (v6 view)
;   md[3] = TTL / hop limit    md[4] = A = ~sum16(old address bytes)
;   md[5] = IHL bytes (v4 arm) md[6] = TOS (v4 arm)
;
; Headroom scratch (bytes below the OUTGOING frame start, so they never
; reach the wire):
;   v4 arm: only [-32,-24) -- the emitted head starts at -(40-IHL) >= -20,
;           so -32..-24 is the whole safe budget; it stages src4+dst4
;           because for IHL >= 32 the v6-header stores would otherwise
;           clobber them before they are read.
;   v6 arm: emitted head starts at +20; [-32,-28) dst4, [-28,-24) src4,
;           [-24,-22) traffic class (staged via a 2-byte store / 1-byte
;           reload -- the byte-granular right shift this ISA doesn't have).
;
; Checksum algebra (RFC 1624, HC' = ~(~HC + ~m + m')): the pseudo-header
; length and protocol terms are equal on both sides, so UDP/TCP patch only
; the address words.  CSUM over an address range yields exactly ~sum16 of
; it, so A (old) drops in as ~m and ~B (new) as m'; the folds between adds
; are the flagless end-around-carry idiom from examples/icmp_echo.  ICMP
; gains/loses the whole v6 pseudo-header (v4 ICMP has none), so the patch
; also adds/removes sum(addrs) + upper_len + 58 and the type-word delta.
; A computed 0 goes out as 0xFFFF on the v6 side (UDP RFC 768 / ICMPv6
; RFC 4443); the v4 side passes the patched value through untouched.
;
; md[0] (system egress) is deliberately never written: the translator is a
; middlebox function, not a forwarder -- egress stays the packaging's call.

.equ H_IPV4  1
.equ H_IPV6  2
.equ H_UDP   3
.equ H_TCP   4
.equ H_ICMP4 5
.equ H_ICMP6 6
.equ H_L4    7

.equ T_EAMT46_HI 0             ; v4 addr -> v6 addr high 64
.equ T_EAMT46_LO 1             ; v4 addr -> v6 addr low 64
.equ T_EAMT64    2             ; v6 addr low 64 -> v4 addr

entry:
    ldmd    r0, 1              ; PP header bitmap
    andi    r1, r0, 2
    bne     r1, rz, v4arm
    andi    r1, r0, 4
    bne     r1, rz, v6arm
    drop                       ; PP never accepts anything else

; ==========================================================================
; v4 -> v6
; ==========================================================================
v4arm:
    ; --- ledger (b): the v4 header checksum must verify -------------------
    ld      r1, H_IPV4, 0, 1
    andi    r1, r1, 0x0F
    shli    r1, r1, 2          ; IHL in bytes (PP guarantees >= 20)
    stmd    r1, 1, 5           ; md[5] = IHL
    csum    r2, H_IPV4, 0, r1  ; ~fold(sum) == 0  <=>  sum == 0xFFFF
    bne     r2, rz, refuse     ; v4_bad_header_checksum
    ; --- payload length = total length - IHL ------------------------------
    ld      r2, H_IPV4, 2, 2
    sub     r3, r2, r1
    andi    r2, r3, 0x8000
    bne     r2, rz, refuse     ; total length below IHL: absurd, drop
    stmd    r3, 1, 2           ; md[2] = payload length
    ; --- ledger (c): fragment (MF or a nonzero offset) --------------------
    ld      r2, H_IPV4, 6, 2
    andi    r2, r2, 0x3FFF
    bne     r2, rz, refuse     ; fragment
    ; --- ledger (e)/(f)/(g): per-L4 value checks --------------------------
    movi    r1, 0x0B
    beq     r0, r1, chk_udp4
    movi    r1, 0x13
    beq     r0, r1, chk_ttl4   ; tcp: nothing value-shaped to check
    movi    r1, 0x23
    beq     r0, r1, chk_icmp4
    drop                       ; unsupported_l4 (bitmap 0x03)
chk_udp4:
    ld      r1, H_UDP, 6, 2
    beq     r1, rz, refuse     ; zero_udp_checksum
    jmp     chk_ttl4
chk_icmp4:
    ld      r1, H_ICMP4, 0, 2  ; type/code word
    beq     r1, rz, chk_ttl4   ; echo reply  (0x0000)
    movi    r2, 0x0800
    bne     r1, r2, refuse     ; icmp_error: non-echo translation deferred
chk_ttl4:
    ; --- ledger (h): TTL <= 1 --------------------------------------------
    ld      r1, H_IPV4, 8, 1
    andi    r2, r1, 0xFE
    beq     r2, rz, refuse     ; ttl_expired
    stmd    r1, 1, 3           ; md[3] = TTL
    ; --- stash everything the v6-header stores would clobber --------------
    ld      r1, H_IPV4, 1, 1
    stmd    r1, 1, 6           ; md[6] = TOS
    ld      r1, H_IPV4, 12, 8
    st      r1, h_frame, -32, 8    ; scratch: src4+dst4
    movi    r1, 8
    csum    r2, H_IPV4, 12, r1
    stmd    r2, 1, 4           ; md[4] = A = ~sum(old addresses)
    ; --- relocate Ethernet: it must end where the v6 header begins --------
    ; new frame start = H_L4 - 54 = h_frame + IHL - 40; for IHL 11/12 that
    ; lands inside [8,12), i.e. the store below would clobber the second
    ; load's source before it's read.  r0/r1 are both dead here (their last
    ; writes -- the L4 dispatch and the address-checksum immediate -- are
    ; already spilled to md), so both loads land before either store and no
    ; IHL can ever see a stale byte.
    ld      r0, h_frame, 0, 8
    ld      r1, h_frame, 8, 4
    st      r0, H_L4, -54, 8
    st      r1, H_L4, -46, 4
    movi    r1, 0x86DD
    st      r1, H_L4, -42, 2   ; the only L2 edit: the EtherType
    ; --- v6 header, back to front of the fields ---------------------------
    ldmd    r1, 6
    shli    r1, r1, 20
    movi    r2, 0x6000
    shli    r2, r2, 16
    or      r1, r1, r2
    st      r1, H_L4, -40, 4   ; version 6 | TC = TOS | flow label 0
    ldmd    r1, 2
    st      r1, H_L4, -36, 2   ; payload length
    ldmd    r0, 1
    movi    r1, 17
    movi    r2, 0x0B
    beq     r0, r2, nh4
    movi    r1, 6
    movi    r2, 0x13
    beq     r0, r2, nh4
    movi    r1, 58             ; ICMP 1 -> ICMPv6 58
nh4:
    st      r1, H_L4, -34, 1   ; next header
    ldmd    r1, 3
    addi    r1, r1, -1
    st      r1, H_L4, -33, 1   ; hop limit = TTL - 1
    ; --- source address: EAMT first (RFC 7757), else RFC 6052 embed -------
    ld      r0, h_frame, -32, 4
    lookup  r1, T_EAMT46_HI, r0, semb
    lookup  r2, T_EAMT46_LO, r0, semb
    jmp     sst
semb:
    movi    r1, 0x0064
    shli    r1, r1, 16
    movi    r2, 0xFF9B
    or      r1, r1, r2
    shli    r1, r1, 32         ; 64:ff9b::/96 high 64 (bytes 8..11 are zero)
    or      r2, r0, rz         ; low 64 = the v4 address itself
sst:
    st      r1, H_L4, -32, 8
    st      r2, H_L4, -24, 8
    ; --- destination address ---------------------------------------------
    ld      r0, h_frame, -28, 4
    lookup  r1, T_EAMT46_HI, r0, demb
    lookup  r2, T_EAMT46_LO, r0, demb
    jmp     dst
demb:
    movi    r1, 0x0064
    shli    r1, r1, 16
    movi    r2, 0xFF9B
    or      r1, r1, r2
    shli    r1, r1, 32
    or      r2, r0, rz
dst:
    st      r1, H_L4, -16, 8
    st      r2, H_L4, -8, 8
    ; --- L4 checksum patch ------------------------------------------------
    movi    r1, 32
    csum    r3, H_L4, -32, r1  ; B = ~sum(new addresses)
    ldmd    r0, 1
    movi    r1, 0x0B
    beq     r0, r1, pat_udp4
    movi    r1, 0x13
    beq     r0, r1, pat_tcp4
    jmp     pat_icmp4

pat_udp4:                      ; HC' = ~fold(~HC + A + ~B), 0 -> 0xFFFF
    ld      r0, H_UDP, 6, 2
    movi    r1, 0xFFFF
    xor     r0, r0, r1         ; ~HC
    xor     r3, r3, r1         ; ~B = sum(new addresses)
    ldmd    r1, 4
    add     r0, r0, r1         ; + A
    andi    r1, r0, 0xFFFF
    beq     r1, r0, u4a        ; end-around carry, flaglessly
    addi    r1, r1, 1
u4a:
    add     r0, r1, r3
    andi    r1, r0, 0xFFFF
    beq     r1, r0, u4b
    addi    r1, r1, 1
u4b:
    movi    r2, 0xFFFF
    xor     r1, r1, r2         ; HC'
    bne     r1, rz, u4c
    or      r1, r2, rz         ; RFC 768: v6 UDP checksum 0 goes out as all-ones
u4c:
    st      r1, H_UDP, 6, 2
    jmp     send46

pat_tcp4:                      ; same patch, TCP offset, no zero rule
    ld      r0, H_TCP, 16, 2
    movi    r1, 0xFFFF
    xor     r0, r0, r1
    xor     r3, r3, r1
    ldmd    r1, 4
    add     r0, r0, r1
    andi    r1, r0, 0xFFFF
    beq     r1, r0, t4a
    addi    r1, r1, 1
t4a:
    add     r0, r1, r3
    andi    r1, r0, 0xFFFF
    beq     r1, r0, t4b
    addi    r1, r1, 1
t4b:
    movi    r2, 0xFFFF
    xor     r1, r1, r2
    st      r1, H_TCP, 16, 2
    jmp     send46

pat_icmp4:                     ; v4 ICMP has no pseudo-header; v6 does.
    ld      r0, H_ICMP4, 0, 2  ; old type/code word (0x0800 or 0x0000)
    movi    r1, 0x8100         ; reply 0 -> 129
    beq     r0, rz, i4w
    movi    r1, 0x8000         ; request 8 -> 128
i4w:
    st      r1, H_ICMP4, 0, 2
    ; HC' = ~fold(~HC + ~old_word + new_word + sum(addrs) + plen + 58)
    movi    r2, 0xFFFF
    xor     r0, r0, r2         ; ~old_word
    xor     r3, r3, r2         ; sum(new addresses) -- the pseudo-header term
    add     r0, r0, r1         ; + new_word
    andi    r1, r0, 0xFFFF
    beq     r1, r0, i4a
    addi    r1, r1, 1
i4a:
    add     r0, r1, r3
    andi    r1, r0, 0xFFFF
    beq     r1, r0, i4b
    addi    r1, r1, 1
i4b:
    ldmd    r2, 2
    add     r0, r1, r2         ; + upper-layer length
    andi    r1, r0, 0xFFFF
    beq     r1, r0, i4c
    addi    r1, r1, 1
i4c:
    addi    r0, r1, 58         ; + next header
    andi    r1, r0, 0xFFFF
    beq     r1, r0, i4d
    addi    r1, r1, 1
i4d:
    ld      r2, H_ICMP4, 2, 2
    movi    r3, 0xFFFF
    xor     r2, r2, r3         ; ~HC
    add     r0, r1, r2
    andi    r1, r0, 0xFFFF
    beq     r1, r0, i4e
    addi    r1, r1, 1
i4e:
    xor     r1, r1, r3         ; HC'
    bne     r1, rz, i4f
    or      r1, r3, rz         ; RFC 4443 mirrors the RFC 768 idiom
i4f:
    st      r1, H_ICMP4, 2, 2
    jmp     send46

send46:                        ; delta = 40 - IHL, dispatched to an immediate
    ldmd    r0, 5
    movi    r1, 20
    beq     r0, r1, s20
    movi    r1, 24
    beq     r0, r1, s16
    movi    r1, 28
    beq     r0, r1, s12
    movi    r1, 32
    beq     r0, r1, s8
    movi    r1, 36
    beq     r0, r1, s4
    movi    r1, 40
    beq     r0, r1, s0
    movi    r1, 44
    beq     r0, r1, sm4
    movi    r1, 48
    beq     r0, r1, sm8
    movi    r1, 52
    beq     r0, r1, sm12
    movi    r1, 56
    beq     r0, r1, sm16
    send    -20                ; IHL 60: the only value left
s20:
    send    20
s16:
    send    16
s12:
    send    12
s8:
    send    8
s4:
    send    4
s0:
    send    0
sm4:
    send    -4
sm8:
    send    -8
sm12:
    send    -12
sm16:
    send    -16

; ==========================================================================
; v6 -> v4
; ==========================================================================
v6arm:
    ; --- ledger (c): any fragment extension header ------------------------
    ld      r1, H_IPV6, 6, 1
    movi    r2, 44
    beq     r1, r2, refuse     ; fragment
    ld      r1, H_IPV6, 4, 2
    stmd    r1, 1, 2           ; md[2] = payload length
    ; --- ledger (f)/(g): per-L4 value checks ------------------------------
    movi    r1, 0x0D
    beq     r0, r1, chk_ttl6   ; udp: v4 zero checksum is legal, no check
    movi    r1, 0x15
    beq     r0, r1, chk_ttl6   ; tcp
    movi    r1, 0x45
    beq     r0, r1, chk_icmp6
    drop                       ; unsupported_l4 (bitmap 0x05, incl. nh 44 dropped above)
chk_icmp6:
    ld      r1, H_ICMP6, 0, 2
    movi    r2, 0x8000
    beq     r1, r2, chk_ttl6   ; echo request
    movi    r2, 0x8100
    bne     r1, r2, refuse     ; icmp_error
chk_ttl6:
    ; --- ledger (h): hop limit <= 1 ---------------------------------------
    ld      r1, H_IPV6, 7, 1
    andi    r2, r1, 0xFE
    beq     r2, rz, refuse     ; ttl_expired
    stmd    r1, 1, 3           ; md[3] = hop limit
    ; --- traffic class: bits [11:4] of the first two bytes ----------------
    ld      r1, H_IPV6, 0, 2
    shli    r1, r1, 4
    andi    r1, r1, 0xFF00     ; TC << 8
    st      r1, h_frame, -24, 2    ; scratch [-24] = TC (2-byte store,
                                   ; 1-byte reload: a right shift by 8)
    ; --- A = ~sum(old addresses), before anything overwrites them ---------
    movi    r1, 32
    csum    r2, H_IPV6, 8, r1
    stmd    r2, 1, 4           ; md[4] = A
    ; --- addresses: pool6 prefix -> 6052 extract, else EAMT t2 ------------
    movi    r1, 0x0064
    shli    r1, r1, 16
    movi    r2, 0xFF9B
    or      r1, r1, r2
    shli    r1, r1, 32         ; pool6 high 64; low 32 of the prefix is zero
    ld      r0, H_IPV6, 24, 8  ; dst bytes 0..7
    bne     r0, r1, dst_eamt
    ld      r0, H_IPV6, 32, 4  ; dst bytes 8..11
    bne     r0, rz, dst_eamt
    ld      r0, H_IPV6, 36, 4  ; the embedded v4 address
    jmp     dst_done
dst_eamt:
    ld      r0, H_IPV6, 32, 8  ; low 64 bits are the EAMT key
    lookup  r0, T_EAMT64, r0, refuse   ; miss: untranslatable_address
dst_done:
    st      r0, h_frame, -32, 4    ; scratch: dst4
    ld      r0, H_IPV6, 8, 8   ; src bytes 0..7 (r1 still pool6 high)
    bne     r0, r1, src_eamt
    ld      r0, H_IPV6, 16, 4
    bne     r0, rz, src_eamt
    ld      r0, H_IPV6, 20, 4
    jmp     src_done
src_eamt:
    ld      r0, H_IPV6, 16, 8
    lookup  r0, T_EAMT64, r0, refuse
src_done:
    st      r0, h_frame, -28, 4    ; scratch: src4
    ; --- relocate Ethernet +20 (disjoint move, see header comment) --------
    ld      r1, h_frame, 0, 8
    st      r1, h_frame, 20, 8
    ld      r1, h_frame, 8, 4
    st      r1, h_frame, 28, 4
    movi    r1, 0x0800
    st      r1, h_frame, 32, 2
    ; --- v4 header at frame 34..54 ---------------------------------------
    ld      r1, h_frame, -24, 1    ; TC
    movi    r2, 0x4500
    or      r1, r1, r2
    st      r1, h_frame, 34, 2 ; version 4, IHL 5 (options never emitted), TOS
    ldmd    r1, 2
    addi    r1, r1, 20
    st      r1, h_frame, 36, 2 ; total length = payload length + 20
    st      rz, h_frame, 38, 2 ; identification = 0
    movi    r1, 0x4000
    st      r1, h_frame, 40, 2 ; DF=1, MF=0, offset 0 (deterministic policy)
    ldmd    r1, 3
    addi    r1, r1, -1
    shli    r1, r1, 8          ; TTL = hop limit - 1
    ldmd    r0, 1
    movi    r2, 0x0D
    movi    r3, 17
    beq     r0, r2, prot6
    movi    r2, 0x15
    movi    r3, 6
    beq     r0, r2, prot6
    movi    r3, 1              ; ICMPv6 58 -> ICMP 1
prot6:
    or      r1, r1, r3
    st      r1, h_frame, 42, 2 ; TTL, protocol
    st      rz, h_frame, 44, 2 ; checksum field zeroed for the fresh compute
    ld      r1, h_frame, -28, 4
    st      r1, h_frame, 46, 4 ; src
    ld      r1, h_frame, -32, 4
    st      r1, h_frame, 50, 4 ; dst
    movi    r1, 20
    csum    r2, h_frame, 34, r1
    st      r2, h_frame, 44, 2 ; fresh header checksum (nothing to patch from)
    ; --- L4 checksum patch ------------------------------------------------
    movi    r1, 8
    csum    r3, h_frame, 46, r1    ; B = ~sum(new addresses)
    movi    r1, 0x0D
    beq     r0, r1, pat_udp6
    movi    r1, 0x15
    beq     r0, r1, pat_tcp6
    jmp     pat_icmp6

pat_udp6:                      ; HC' = ~fold(~HC + A + ~B); v4 side: no zero rule
    ld      r0, H_UDP, 6, 2
    movi    r1, 0xFFFF
    xor     r0, r0, r1
    xor     r3, r3, r1
    ldmd    r1, 4
    add     r0, r0, r1
    andi    r1, r0, 0xFFFF
    beq     r1, r0, u6a
    addi    r1, r1, 1
u6a:
    add     r0, r1, r3
    andi    r1, r0, 0xFFFF
    beq     r1, r0, u6b
    addi    r1, r1, 1
u6b:
    movi    r2, 0xFFFF
    xor     r1, r1, r2
    st      r1, H_UDP, 6, 2
    send    -20

pat_tcp6:
    ld      r0, H_TCP, 16, 2
    movi    r1, 0xFFFF
    xor     r0, r0, r1
    xor     r3, r3, r1
    ldmd    r1, 4
    add     r0, r0, r1
    andi    r1, r0, 0xFFFF
    beq     r1, r0, t6a
    addi    r1, r1, 1
t6a:
    add     r0, r1, r3
    andi    r1, r0, 0xFFFF
    beq     r1, r0, t6b
    addi    r1, r1, 1
t6b:
    movi    r2, 0xFFFF
    xor     r1, r1, r2
    st      r1, H_TCP, 16, 2
    send    -20

pat_icmp6:                     ; the v6 pseudo-header leaves the sum
    ld      r0, H_ICMP6, 0, 2  ; old word: 0x8000 (request) or 0x8100 (reply)
    movi    r2, 0x0800         ; request 128 -> 8
    movi    r1, 0x8000
    beq     r0, r1, i6w
    movi    r2, 0              ; reply 129 -> 0
i6w:
    st      r2, H_ICMP6, 0, 2
    ; m = fold(old_word + sum(old addrs) + plen + 58); HC' = ~fold(~HC + ~m + new_word)
    movi    r1, 0xFFFF
    ldmd    r3, 4
    xor     r3, r3, r1         ; sum(old addresses)
    add     r0, r0, r3
    andi    r3, r0, 0xFFFF
    beq     r3, r0, i6a
    addi    r3, r3, 1
i6a:
    ldmd    r1, 2
    add     r0, r3, r1         ; + upper-layer length
    andi    r3, r0, 0xFFFF
    beq     r3, r0, i6b
    addi    r3, r3, 1
i6b:
    addi    r0, r3, 58         ; + next header
    andi    r3, r0, 0xFFFF
    beq     r3, r0, i6c
    addi    r3, r3, 1
i6c:
    movi    r1, 0xFFFF
    xor     r3, r3, r1         ; ~m
    ld      r0, H_ICMP6, 2, 2
    xor     r0, r0, r1         ; ~HC
    add     r0, r0, r3
    andi    r3, r0, 0xFFFF
    beq     r3, r0, i6d
    addi    r3, r3, 1
i6d:
    add     r0, r3, r2         ; + new_word
    andi    r3, r0, 0xFFFF
    beq     r3, r0, i6e
    addi    r3, r3, 1
i6e:
    xor     r3, r3, r1         ; HC' (v4 ICMP: no zero adjustment)
    st      r3, H_ICMP6, 2, 2
    send    -20

refuse:
    drop
