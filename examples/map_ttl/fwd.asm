; Nanuk MAP ISA v0 demo 2: IPv4 TTL decrement + checksum fix, then L2 forward.
;
; Router rule: drop when TTL <= 1 (a decrement would forward TTL 0), else
; decrement in place and let the CSUMUPD accelerator repair the header
; checksum. Non-IPv4 traffic never reaches the ST: the first LD from an
; absent h_ipv4 base is a *defined* error halt (err_hdr_absent) which the
; egress glue drops - totality is the guard, no hdr_present test needed.
;
; Note the 64-bit registers: decrement-then-test-zero would wrap on TTL 0
; (0 - 1 = 2^64 - 1), so the expired test runs before the ADDI.
;
; Tables (control plane): t0 = L2 FDB, key = 48-bit DMAC, action = port bitmap.

.equ H_ETH 0
.equ H_IPV4 2
.equ T_L2 0
.equ MD_FLOOD 9

    ld      r0, H_IPV4, 8, 1       ; TTL (absent IPv4 -> defined error drop)
    beq     r0, rz, expired        ; TTL 0
    movi    r1, 1
    beq     r0, r1, expired        ; TTL 1 would forward as 0
    addi    r0, r0, -1
    st      r0, H_IPV4, 8, 1
    csumupd H_IPV4, 0              ; accelerator repairs the header checksum
    ld      r0, H_ETH, 0, 6        ; then forward as in demo 1
    lookup  r1, T_L2, r0, miss
    send    r1, 0
miss:
    ldmd    r1, MD_FLOOD
    send    r1, 0
expired:
    drop
