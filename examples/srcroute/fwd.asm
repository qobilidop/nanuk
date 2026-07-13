; Benchmark T0 — table-free forwarding. The forwarding decision comes out of
; the packet, not out of a table: this program performs NO lookup at all.
;
; It proves the machine is not table-dependent -- worth pinning down, because
; every other MAP benchmark leans on LOOKUP and it would be easy to mistake
; "the table is the policy" for "a table is mandatory".
;
; Also benchmark E5 (shrink head): each hop POPS its own 2-byte entry. That
; entry sits *after* the Ethernet header, so this is a mid-frame splice, which
; the ISA has no instruction for. The zero-copy idiom for it, and the one
; every push/pop program uses:
;
;   1. relocate the prefix (the 14-byte Ethernet header) forward by 2 bytes,
;   2. send with a head delta of -2, so the drain starts 2 bytes later.
;
; The bytes vacated at the front are simply never transmitted. Nothing is
; spliced, nothing shifts the payload, and the cost is O(prefix), not O(frame).

.equ h_eth 0

.equ ET_IPV4 0x0800
; One source-route hop is 2 bytes. (Spelled literally at `send`: the
; assembler resolves symbols but does not negate them.)

start:
    ldmd    r0, 1              ; egress bitmap, straight from the packet
    ldmd    r1, 2              ; bottom-of-stack?

relocate:                      ; move the Ethernet header forward by 2 bytes
    ; The source and destination overlap, so the copy runs TOP-DOWN. Doing the
    ; low bytes first would store over bytes 8..9 before the second load reads
    ; them, and the source MAC would come out mangled -- memmove's hazard,
    ; reproduced faithfully in 4 instructions. The window is memory, not a
    ; register file, and it does not forgive an aliasing copy.
    ld      r2, h_eth, 8, 6    ; rest of src MAC + EtherType  (bytes 8..13)
    st      r2, h_eth, 10, 6   ;                           -> bytes 10..15
    ld      r2, h_eth, 0, 8    ; dst MAC + first 2 of src     (bytes 0..7)
    st      r2, h_eth, 2, 8    ;                           -> bytes 2..9

    beq     r1, rz, forward    ; more hops: leave the EtherType as source-route

bottom:                        ; last hop: hand the payload back its EtherType
    movi    r2, ET_IPV4
    st      r2, h_eth, 14, 2   ; EtherType of the RELOCATED header (12 + 2)

forward:
    stmd    r0, 1, 0           ; egress bitmap -> md slot 0
    send    -2                 ; pop the 2-byte hop we just consumed
