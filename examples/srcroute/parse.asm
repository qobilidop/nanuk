; Benchmark T0 (parser half) — source routing: the packet carries its route.
;
; Ethernet (EtherType 0x1234) followed by a stack of 2-byte hops:
;   bit 15 = bottom-of-stack, bits 14..0 = egress port bitmap
;
; The PP reads only the HEAD entry -- one hop's worth. Each switch pops it
; (see fwd.asm), so the next switch's head entry is the next hop.
;
; Why a bitmap and not a port number: MAP has no shift-by-register (`shli`
; takes an immediate), so it cannot compute `1 << port`. A program that wants
; "the port named in the packet" must either carry the bitmap directly, as
; here, or go through a {port -> bitmap} table. Carrying it is what makes this
; benchmark genuinely table-free.
;
; md: slot 1 = egress bitmap, slot 2 = bottom-of-stack flag.

.equ h_eth 0
.equ h_sr  1

.equ ET_SRCROUTE 0x88b5   ; IEEE 802 local-experimental EtherType

start:
    sethdr  h_eth
    ext     r0, 96, 16         ; EtherType
    advi    14
    movi    r1, ET_SRCROUTE
    bne     r0, r1, drop

srcroute:                      ; cursor on the head hop
    sethdr  h_sr
    ext     r0, 0, 1           ; bottom-of-stack
    stmd    2, r0, 1
    ext     r0, 1, 15          ; egress bitmap
    stmd    1, r0, 1
    advi    2
    halt    accept

drop:
    halt    drop
