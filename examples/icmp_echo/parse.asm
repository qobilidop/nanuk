; Benchmark E1 (parser half) — Ethernet / IPv4 / ICMP.
;
; Nothing new on the parser ladder (P1 + P2 + P3); it exists to give the MAP
; program its header bases.

.equ h_eth  0
.equ h_ipv4 1
.equ h_icmp 2

.equ ET_IPV4 0x0800
.equ IP_ICMP 1

start:
    sethdr  h_eth
    ext     r0, 96, 16         ; EtherType
    advi    14
    movi    r1, ET_IPV4
    bne     r0, r1, drop

ipv4:
    sethdr  h_ipv4
    ext     r1, 72, 8          ; protocol
    ext     r0, 4, 4           ; IHL
    shl     r0, r0, 2
    advr    r0                 ; computed advance over options
    movi    r2, IP_ICMP
    bne     r1, r2, drop

icmp:
    sethdr  h_icmp
    halt    accept

drop:
    halt    drop
