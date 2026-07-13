; Nanuk MAP ISA v0 demo 2: IPv4 TTL decrement + checksum fix, then L2 forward.
;
; Router rule: drop when TTL <= 1 (a decrement would forward TTL 0), else
; decrement in place and recompute the header checksum with the generic
; CSUM range primitive: derive the header length from the IHL nibble
; (andi/shli), zero the checksum field, sum, store. No protocol knowledge
; in the ISA — the program owns the IPv4 layout. Non-IPv4 traffic never
; reaches the ST: the first LD from an absent h_ipv4 base is a *defined*
; error halt (err_hdr_absent) which the egress glue drops — totality is
; the guard, no hdr_present test needed.
;
; Note the 64-bit registers: decrement-then-test-zero would wrap on TTL 0
; (0 - 1 = 2^64 - 1), so the expired test runs before the ADDI.
;
; Tables (control plane): t0 = L2 FDB, key = 48-bit DMAC, action = port
; bitmap. t3 = system flood table (see map_l2fwd).

.equ H_ETH 0
.equ H_IPV4 2
.equ T_L2 0
.equ T_SYS 3

    ld      r0, H_IPV4, 8, 1       ; TTL (absent IPv4 -> defined error drop)
    beq     r0, rz, expired        ; TTL 0
    movi    r1, 1
    beq     r0, r1, expired        ; TTL 1 would forward as 0
    addi    r0, r0, -1
    st      r0, H_IPV4, 8, 1
    ld      r2, H_IPV4, 0, 1       ; version|IHL byte
    andi    r2, r2, 0x000F         ; IHL
    shli    r2, r2, 2              ; header length = IHL * 4
    st      rz, H_IPV4, 10, 2      ; zero the checksum field first
    csum    r3, H_IPV4, 0, r2      ; ones-complement sum over the header
    st      r3, H_IPV4, 10, 2      ; store the new checksum
    ld      r0, H_ETH, 0, 6        ; then forward as in demo 1
    lookup  r1, T_L2, r0, miss
    stmd    r1, 1, 0
    send    0
miss:
    ldmd    r2, 0                  ; ingress port id (system convention)
    lookup  r1, T_SYS, r2, expired ; flood bitmap; unconfigured -> drop
    stmd    r1, 1, 0
    send    0
expired:
    drop
