; Nanuk MAP ISA v0 demo 3b: nanukproto tunnel pop (decap).
;
; The PP (parse_tunnel.asm) writes SMD slot 5 = nk magic when it parsed a
; valid tunnel header; the pop strips the 22-byte outer header with a
; negative send delta. Non-tunnel traffic floods unchanged - the SMD flag
; is the PP -> MAP metadata pass-through doing its job.

.equ MD_TUN 5
.equ MD_FLOOD 9

    ldmd    r0, MD_TUN
    movi    r1, 0x4E4B
    bne     r0, r1, plain
    ldmd    r1, MD_FLOOD
    send    r1, -22                ; strip outer Ethernet + nanukproto
plain:
    ldmd    r1, MD_FLOOD
    send    r1, 0
