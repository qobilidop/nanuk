; Benchmark P6 — nesting: the same header type twice.
;
; Gibb's data-center parse graph (ANCS 2013, Fig. 3b):
;   Ethernet -> [VLAN <= 2] -> IPv4 -> { UDP -> VXLAN, GRE -> NVGRE }
;                                        -> inner Ethernet -> inner IPv4
;
; What this benchmark forces: an overlay puts Ethernet inside Ethernet and
; IPv4 inside IPv4. The parser must therefore hold TWO LIVE INSTANCES of the
; same header type at once, which is what makes the header-id space a real
; resource rather than a formality -- outer and inner cannot share a slot,
; and "the IPv4 header" stops being a well-defined phrase. Every downstream
; consumer (MAP's hdr-relative LD/ST, the md conventions) inherits that.
;
; Deviation from Gibb, deliberate: his graphs make the inner Ethernet
; TERMINAL -- they parse into the overlay and stop. We continue into the
; inner IPv4, on the strength of modern overlay practice (a switch that
; cannot see the inner L3 header cannot route on it). This is the one place
; the parser ladder goes beyond its source, and it is the more demanding
; direction.
;
; md: slot 1 = VNI (low 16b), slot 2 = outer L4 tag (17=UDP, 47=GRE),
;     slot 3 = 1 if an inner IPv4 was parsed.

.equ h_eth    0
.equ h_vlan   1
.equ h_ipv4   2
.equ h_udp    3
.equ h_vxlan  4
.equ h_gre    5
.equ h_eth2   6                ; inner Ethernet -- the point of this benchmark
.equ h_ipv4_2 7                ; inner IPv4

.equ ETH_VLAN  0x8100
.equ ETH_IPV4  0x0800
.equ IP_UDP    17
.equ IP_GRE    47
.equ VXLAN_PORT 4789

start:
    sethdr  h_eth
    ext     r0, 96, 16         ; EtherType
    advi    14

l2_dispatch:
    movi    r1, ETH_VLAN
    beq     r0, r1, vlan
    movi    r1, ETH_IPV4
    beq     r0, r1, ipv4
    halt    drop

vlan:                          ; cursor at TCI (TPID already consumed)
    sethdr  h_vlan
    ext     r0, 16, 16         ; inner EtherType
    advi    4
    jmp     l2_dispatch        ; QinQ: bounded by the step budget

ipv4:
    sethdr  h_ipv4
    ext     r2, 72, 8          ; protocol
    stmd    2, r2, 1
    ext     r0, 4, 4           ; IHL
    shl     r0, r0, 2
    advr    r0                 ; computed advance over options
    movi    r1, IP_UDP
    beq     r2, r1, udp
    movi    r1, IP_GRE
    beq     r2, r1, gre
    halt    accept             ; a non-overlay IPv4 packet: done

udp:
    sethdr  h_udp
    ext     r0, 16, 16         ; dst port
    advi    8
    movi    r1, VXLAN_PORT
    bne     r0, r1, halt_ok    ; plain UDP: not an overlay
    ; fall through to VXLAN

vxlan:
    sethdr  h_vxlan
    ext     r0, 32, 24         ; VNI
    stmd    1, r0, 1           ; low 16b of the VNI
    advi    8
    jmp     inner_eth

gre:
    sethdr  h_gre
    ext     r0, 16, 16         ; GRE protocol type
    ext     r1, 0, 1           ; C bit -- checksum-present variants unsupported
    bne     r1, rz, drop
    ext     r2, 2, 1           ; K bit -- NVGRE mandates a key (RFC 7637)
    advi    4
    movi    r1, 0x6558         ; transparent Ethernet bridging (NVGRE)
    bne     r0, r1, drop
    beq     r2, rz, drop       ; TEB without a key is not NVGRE
    ext     r0, 0, 24          ; VSID (NVGRE's key field == VXLAN's VNI)
    stmd    1, r0, 1
    advi    4                  ; NVGRE key field (VSID + FlowID)
    ; fall through to inner_eth

inner_eth:                     ; the same header type, a second time
    sethdr  h_eth2
    ext     r0, 96, 16         ; inner EtherType
    advi    14
    movi    r1, ETH_IPV4
    bne     r0, r1, halt_ok    ; inner non-IPv4: stop here, still a good parse

inner_ipv4:
    sethdr  h_ipv4_2
    ext     r0, 4, 4           ; inner IHL
    shl     r0, r0, 2
    advr    r0
    movi    r1, 1
    stmd    3, r1, 1
    halt    accept

halt_ok:
    halt    accept

drop:
    halt    drop
