; nanuk MAP ISA v0 demo 3a: nanukproto tunnel push (encap).
;
; DMACs found in the tunnel table get the full L2-in-L2 encap: a 22-byte
; outer header (outer Ethernet + nanukproto) written into headroom, sent
; with delta +22 toward the tunnel port. Everything else floods unmodified.
;
; Outer header (static single-tenant config, constants in program text):
;   dst 02:4e:4b:00:00:01  src 02:4e:4b:00:00:02  type 0x88B5
;   nk: magic 0x4E4B, version 1, flags 0, tenant 1, inner_ethertype 0x6558
;
; Tables (control plane): t1 = tunnel map, key = 48-bit DMAC,
; action = egress port bitmap toward the tunnel.

.equ H_ETH 0
.equ T_TUN 1
.equ MD_FLOOD 9

    ld      r0, H_ETH, 0, 6
    lookup  r1, T_TUN, r0, plain   ; hit: r1 = tunnel egress bitmap
    movi    r2, 0x024E             ; outer Ethernet dst/src/type
    st      r2, h_frame, -22, 2
    movi    r2, 0x4B00
    st      r2, h_frame, -20, 2
    movi    r2, 0x0001
    st      r2, h_frame, -18, 2
    movi    r2, 0x024E
    st      r2, h_frame, -16, 2
    movi    r2, 0x4B00
    st      r2, h_frame, -14, 2
    movi    r2, 0x0002
    st      r2, h_frame, -12, 2
    movi    r2, 0x88B5
    st      r2, h_frame, -10, 2
    movi    r2, 0x4E4B             ; nanukproto header
    st      r2, h_frame, -8, 2
    movi    r2, 0x1000             ; version 1, flags 0, tenant[23:16]
    st      r2, h_frame, -6, 2
    movi    r2, 0x0001             ; tenant[15:0]
    st      r2, h_frame, -4, 2
    movi    r2, 0x6558             ; inner ethertype: full frame follows
    st      r2, h_frame, -2, 2
    send    r1, 22
plain:
    ldmd    r1, MD_FLOOD
    send    r1, 0
