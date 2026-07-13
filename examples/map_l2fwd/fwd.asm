; Nanuk MAP ISA v0 demo 1: L2 forward.
;
; Exact-match lookup on the destination MAC -> egress port bitmap into
; md[0]; miss -> flood table lookup keyed by the ingress port id the
; system placed in md[0] (nanuk_switch conventions: slot 0 = ingress in,
; egress bitmap out). Flooding is table content installed by the control
; plane — change the topology, reinstall t3, same program. The DMAC is
; read straight from the frame (h_eth base, first 6 bytes); the parser
; guarantees h_eth is present on every accepted packet.
;
; Tables (control plane): t0 = L2 FDB, key = 48-bit DMAC, action = port
; bitmap. t3 = system flood table, key = ingress port id, action = flood
; bitmap. An unconfigured flood table fails closed (drop).

.equ H_ETH 0
.equ T_L2 0
.equ T_SYS 3

    ld      r0, H_ETH, 0, 6        ; DMAC
    lookup  r1, T_L2, r0, miss     ; hit: r1 = egress port bitmap
    stmd    r1, 1, 0               ; md[0] = egress bitmap
    send    0
miss:
    ldmd    r2, 0                  ; md[0] = ingress port id (system convention)
    lookup  r1, T_SYS, r2, dark    ; flood bitmap from the system table
    stmd    r1, 1, 0
    send    0
dark:
    drop                           ; unconfigured flood table: fail closed
