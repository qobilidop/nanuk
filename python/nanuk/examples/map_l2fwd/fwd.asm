; nanuk MAP ISA v0 demo 1: L2 forward.
;
; Exact-match lookup on the destination MAC -> egress port bitmap; miss ->
; flood (all ports except ingress, hardware-computed in inbound-SMD field 9).
; The DMAC is read straight from the frame (h_eth base, first 6 bytes); the
; parser guarantees h_eth is present on every accepted packet.
;
; Tables (control plane): t0 = L2 FDB, key = 48-bit DMAC, action = port bitmap.

.equ H_ETH 0
.equ T_L2 0
.equ MD_FLOOD 9

    ld      r0, H_ETH, 0, 6        ; DMAC
    lookup  r1, T_L2, r0, miss     ; hit: r1 = egress port bitmap
    send    r1, 0
miss:
    ldmd    r1, MD_FLOOD           ; all_ports & ~ingress
    send    r1, 0
