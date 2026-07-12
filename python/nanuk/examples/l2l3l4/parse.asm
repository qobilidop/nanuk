; nanuk parser ISA v0 demo program:
;   Ethernet -> 802.1Q (incl. QinQ via backward branch) -> IPv4 (with
;   options) -> UDP, feeding the SMD with DMAC, VLAN TCI, and UDP dst port.
;
; Header ids and SMD layout (consumed by spec/python/tests/test_demo.py):
;   h_eth=0  h_vlan=1  h_ipv4=2  h_udp=3
;   SMD slots 0-2: DMAC   slot 3: outermost-last VLAN TCI   slot 4: UDP dport
;
; For stacked VLANs, sethdr/stmd record the *last* tag seen (offsets and TCI
; are overwritten on each loop pass) - a deliberate v0 simplification.

.equ h_eth  0
.equ h_vlan 1
.equ h_ipv4 2
.equ h_udp  3

start:
    sethdr  h_eth
    ext     r0, 0, 48          ; DMAC
    stmd    0, r0, 3           ; -> SMD slots 0..2
    ext     r0, 96, 16         ; EtherType
    advi    14                 ; skip Ethernet header

dispatch:                      ; r0 = current EtherType
    movi    r1, 0x8100
    beq     r0, r1, vlan
    movi    r1, 0x0800
    beq     r0, r1, ipv4
    halt    accept             ; unknown L3: accept with what we know

vlan:                          ; cursor sits at TCI (TPID consumed above)
    sethdr  h_vlan
    ext     r2, 0, 16          ; TCI
    stmd    3, r2, 1           ; -> SMD slot 3
    ext     r0, 16, 16         ; inner EtherType
    advi    4
    jmp     dispatch           ; QinQ loop - bounded by the step budget

ipv4:
    sethdr  h_ipv4
    ext     r1, 0, 4           ; version (pre-masked by bit-granular ext)
    movi    r2, 4
    bne     r1, r2, drop
    ext     r1, 4, 4           ; IHL
    ext     r2, 72, 8          ; protocol
    shl     r1, r1, 2          ; header length in bytes
    advr    r1                 ; skip header incl. options
    movi    r1, 17
    beq     r2, r1, udp
    halt    accept             ; non-UDP L4: accept, payload = L4 start

udp:
    sethdr  h_udp
    ext     r1, 16, 16         ; dst port
    stmd    4, r1, 1           ; -> SMD slot 4
    advi    8
    halt    accept

drop:
    halt    drop
