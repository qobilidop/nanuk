; Nanuk MAP ISA v0 demo 3b: nanukproto tunnel pop (decap).
;
; The PP (parse_tunnel.asm) writes md slot 5 = nk magic when it parsed a
; valid tunnel header; the pop strips the 22-byte outer header with a
; negative send delta. Non-tunnel traffic floods unchanged - the metadata
; window's PP -> MAP pass-through doing its job (slots 4-7 are the
; program pair's private range under the nanuk_switch conventions).
;
; Tables (control plane): t3 = system flood table (see map_l2fwd).

.equ MD_TUN 5
.equ T_SYS 3

    ldmd    r0, MD_TUN
    movi    r1, 0x4E4B
    bne     r0, r1, plain
    ldmd    r2, 0                  ; ingress port id (system convention)
    lookup  r1, T_SYS, r2, dark
    stmd    r1, 1, 0
    send    -22                    ; strip outer Ethernet + nanukproto
plain:
    ldmd    r2, 0
    lookup  r1, T_SYS, r2, dark
    stmd    r1, 1, 0
    send    0
dark:
    drop
