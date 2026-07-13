; Benchmark P7 — scale: Gibb's "big union" parse graph.
;
; The union of his four use-case graphs (ANCS 2013, Fig. 3): 21 header types,
; 28 nodes, ~677 paths. This is the only benchmark that measures CAPACITY
; rather than capability -- it forces no instruction the ladder below has not
; already forced. What it measures is whether the PP's *sizes* hold: imem
; words, the step budget, and above all the header-id space.
;
; THE HEADER-SLOT SQUEEZE, which is the interesting thing this graph does to
; this machine: `sethdr` takes a 4-bit id, so there are 16 slots -- and the
; union has 21 header TYPES. One slot per type is arithmetically impossible.
; The program must therefore ALIAS mutually-exclusive types onto shared slots
; and record which one landed there in the metadata window. That is a real
; design decision forced by a real graph, and it is the reason a "scale"
; benchmark earns its place despite forcing no new instruction.
;
; Slot plan (16 slots, 21 types):
;   0  eth        7  ipsec   (esp | ah          -- aliased)
;   1  vlan       8  gre
;   2  mpls       9  vxlan
;   3  ipv4      10  eompls
;   4  ipv6      11  eth2    (inner Ethernet)
;   5  arp       12  ipv4_2  (inner)
;   6  l4        13  ipv6_2  (inner)
;      (tcp | udp | sctp | icmp | icmpv6 -- aliased)
;                14  l4_2    (inner L4, aliased)
;                15  --      (spare)
;
; md: slot 1 = outer L4 kind (IP proto), slot 2 = tunnel kind
;     (1=vxlan 2=nvgre 3=eompls), slot 3 = inner L4 kind, slot 4 = VNI/VSID.
;
; Repetition bounds (one-hot counters -- the PP has no increment):
;   VLAN <= 2 (802.1Q/802.1ad share the counter, per Gibb's `max_var`)
;   MPLS <= 5
; GRE's max=3 in Gibb's graph is NOT exercised: his own file gives no
; next-header encoding for GRE-in-GRE that we could implement faithfully, so
; we parse a single GRE and say so rather than invent one.

.equ h_eth    0
.equ h_vlan   1
.equ h_mpls   2
.equ h_ipv4   3
.equ h_ipv6   4
.equ h_arp    5
.equ h_l4     6
.equ h_ipsec  7
.equ h_gre    8
.equ h_vxlan  9
.equ h_eompls 10
.equ h_eth2   11
.equ h_ipv4_2 12
.equ h_ipv6_2 13
.equ h_l4_2   14

; EtherTypes
.equ ET_VLAN   0x8100
.equ ET_QINQ   0x88a8
.equ ET_IPV4   0x0800
.equ ET_IPV6   0x86dd
.equ ET_ARP    0x0806
.equ ET_RARP   0x8035
.equ ET_MPLS   0x8847
.equ ET_TEB    0x6558          ; transparent Ethernet bridging (NVGRE)

; IP protocols
.equ IP_ICMP   1
.equ IP_TCP    6
.equ IP_UDP    17
.equ IP_GRE    47
.equ IP_ESP    50
.equ IP_AH     51
.equ IP_ICMPV6 58
.equ IP_SCTP   132

.equ VXLAN_PORT 4789
.equ VLAN_MAX_ONEHOT 0x04      ; 1<<2 -- a third tag
.equ MPLS_MAX_ONEHOT 0x20      ; 1<<5 -- a sixth label

; ---------------------------------------------------------------- L2

start:
    sethdr  h_eth
    ext     r0, 96, 16         ; EtherType
    advi    14
    movi    r3, 1              ; one-hot VLAN counter

l2_dispatch:                   ; r0 = current EtherType
    movi    r1, ET_VLAN
    beq     r0, r1, vlan
    movi    r1, ET_QINQ
    beq     r0, r1, vlan
    movi    r1, ET_IPV4
    beq     r0, r1, ipv4
    movi    r1, ET_IPV6
    beq     r0, r1, ipv6
    movi    r1, ET_ARP
    beq     r0, r1, arp
    movi    r1, ET_RARP
    beq     r0, r1, arp
    movi    r1, ET_MPLS
    beq     r0, r1, mpls_init
    halt    accept             ; unknown L3: keep what we have

vlan:                          ; cursor at TCI (TPID consumed by the caller)
    movi    r1, VLAN_MAX_ONEHOT
    beq     r3, r1, drop       ; third tag: refuse
    sethdr  h_vlan             ; last tag wins
    ext     r0, 16, 16         ; inner EtherType
    advi    4
    shl     r3, r3, 1
    jmp     l2_dispatch

arp:
    sethdr  h_arp
    advi    28
    halt    accept

; ---------------------------------------------------------------- MPLS

mpls_init:
    movi    r3, 1              ; one-hot label counter (VLAN's is done with)

mpls:                          ; cursor on a 4-byte label
    movi    r1, MPLS_MAX_ONEHOT
    beq     r3, r1, drop       ; sixth label: refuse
    sethdr  h_mpls             ; last label wins
    ext     r1, 23, 1          ; bos
    ext     r2, 32, 4          ; LOOKAHEAD -- MPLS names no successor
    advi    4
    shl     r3, r3, 1
    beq     r1, rz, mpls       ; bos == 0: another label

mpls_payload:                  ; r2 = first nibble past the stack
    movi    r1, 4
    beq     r2, r1, ipv4
    movi    r1, 6
    beq     r2, r1, ipv6
    beq     r2, rz, eompls     ; 0 -> EoMPLS control word
    halt    drop

eompls:
    sethdr  h_eompls
    advi    4                  ; control word
    movi    r1, 3
    stmd    2, r1, 1           ; tunnel kind = eompls
    jmp     inner_eth

; ---------------------------------------------------------------- L3

ipv4:
    sethdr  h_ipv4
    ext     r2, 72, 8          ; protocol
    ext     r0, 4, 4           ; IHL
    shl     r0, r0, 2
    advr    r0                 ; computed advance over options
    jmp     l3_dispatch

ipv6:
    sethdr  h_ipv6
    ext     r2, 48, 8          ; next header
    advi    40
    ; fall through

l3_dispatch:                   ; r2 = IP protocol / next header
    stmd    1, r2, 1           ; outer L4 kind
    movi    r1, IP_TCP
    beq     r2, r1, tcp
    movi    r1, IP_UDP
    beq     r2, r1, udp
    movi    r1, IP_SCTP
    beq     r2, r1, sctp
    movi    r1, IP_ICMP
    beq     r2, r1, icmp
    movi    r1, IP_ICMPV6
    beq     r2, r1, icmp
    movi    r1, IP_GRE
    beq     r2, r1, gre
    movi    r1, IP_ESP
    beq     r2, r1, esp
    movi    r1, IP_AH
    beq     r2, r1, ah
    halt    accept             ; unknown L4

; ---------------------------------------------------------------- IPsec

esp:                           ; ESP: payload is encrypted -- parsing stops
    sethdr  h_ipsec
    advi    8
    halt    accept

ah:                            ; AH length = (payload_len + 2) * 4 bytes
    sethdr  h_ipsec
    ext     r2, 0, 8           ; next header
    ext     r0, 8, 8           ; payload length
    shl     r0, r0, 2          ; * 4
    advr    r0
    advi    8                  ; the "+ 2" words, folded into an immediate
    jmp     l3_dispatch        ; AH names its own successor: keep going

; ---------------------------------------------------------------- L4

tcp:
    sethdr  h_l4
    ext     r0, 96, 4          ; data offset
    shl     r0, r0, 2
    advr    r0
    halt    accept

sctp:
    sethdr  h_l4
    advi    12
    halt    accept

icmp:
    sethdr  h_l4
    advi    8
    halt    accept

udp:
    sethdr  h_l4
    ext     r0, 16, 16         ; dst port
    advi    8
    movi    r1, VXLAN_PORT
    bne     r0, r1, halt_ok
    ; fall through: UDP/4789 is VXLAN

vxlan:
    sethdr  h_vxlan
    ext     r0, 32, 24         ; VNI
    stmd    4, r0, 1
    movi    r1, 1
    stmd    2, r1, 1           ; tunnel kind = vxlan
    advi    8
    jmp     inner_eth

; ---------------------------------------------------------------- GRE

gre:
    sethdr  h_gre
    ext     r0, 16, 16         ; protocol type
    ext     r1, 0, 1           ; C bit: checksum-present variants unsupported
    bne     r1, rz, drop
    ext     r3, 2, 1           ; K bit: a key field follows the base header
    advi    4
    movi    r1, ET_TEB
    beq     r0, r1, nvgre
    bne     r3, rz, drop       ; a keyed non-TEB GRE: out of scope, be total
    movi    r1, ET_IPV4
    beq     r0, r1, ipv4       ; GRE-encapsulated IPv4
    movi    r1, ET_IPV6
    beq     r0, r1, ipv6
    halt    accept

nvgre:
    beq     r3, rz, drop       ; TEB without a key is not NVGRE (RFC 7637)
    ext     r0, 0, 24          ; VSID (NVGRE's key field == VXLAN's VNI)
    stmd    4, r0, 1
    movi    r1, 2
    stmd    2, r1, 1           ; tunnel kind = nvgre
    advi    4                  ; key field
    ; fall through

; ---------------------------------------------------------------- overlay

inner_eth:                     ; the same header type, a second time
    sethdr  h_eth2
    ext     r0, 96, 16         ; inner EtherType
    advi    14
    movi    r1, ET_IPV4
    beq     r0, r1, inner_ipv4
    movi    r1, ET_IPV6
    beq     r0, r1, inner_ipv6
    halt    accept             ; inner non-IP: a good parse, just shallower

inner_ipv4:
    sethdr  h_ipv4_2
    ext     r2, 72, 8
    ext     r0, 4, 4
    shl     r0, r0, 2
    advr    r0
    jmp     inner_l4

inner_ipv6:
    sethdr  h_ipv6_2
    ext     r2, 48, 8
    advi    40
    ; fall through

inner_l4:                      ; r2 = inner IP protocol
    stmd    3, r2, 1           ; inner L4 kind
    sethdr  h_l4_2
    movi    r1, IP_TCP
    beq     r2, r1, inner_tcp
    halt    accept             ; inner UDP/ICMP/...: header base recorded

inner_tcp:
    ext     r0, 96, 4          ; data offset
    shl     r0, r0, 2
    advr    r0
    halt    accept

halt_ok:
    halt    accept

drop:
    halt    drop
