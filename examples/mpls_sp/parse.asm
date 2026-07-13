; Benchmark P5 — incomplete information (lookahead).
;
; Gibb's service-provider parse graph (ANCS 2013, Fig. 3d):
;   Ethernet -> MPLS label stack (<= 5) -> { IPv4, IPv6, EoMPLS -> Ethernet }
;
; What this benchmark forces, and nothing below it does: MPLS carries NO
; next-protocol field. `bos` says the label stack ended -- it does not say
; what you landed on. The successor type lives in the 4 bits PAST the label
; you are standing on (the IP version nibble; 0 = an EoMPLS control word).
; Gibb calls this out as a first-order parser challenge, and it is what
; distinguishes packet parsing from instruction decoding: headers, unlike
; instructions, do not encode their own type.
;
; The PP reads it with a NON-CONSUMING `ext` at bit offset 32 while the
; cursor still sits on the label -- no speculation, no rewind, no peek
; instruction. An `ext` past the buffered window is a defined error, so the
; lookahead traps rather than reading garbage.
;
; Bounded repetition without an increment: the PP has no ADD. The label
; counter is ONE-HOT, shifted left once per label; reaching 1<<5 means a
; sixth label, which the program refuses. Termination is therefore visible
; in the program text, not merely in the step budget.
;
; md: slots 1-2 = last label seen (20b), slot 3 = payload tag (4/6/0).

.equ h_eth   0
.equ h_mpls  1
.equ h_ipv4  2
.equ h_ipv6  3
.equ h_eth2  4

.equ ETH_MPLS_UC     0x8847
.equ MPLS_MAX_ONEHOT 0x20      ; 1<<5 -- a sixth label

start:
    sethdr  h_eth
    ext     r0, 96, 16         ; EtherType
    advi    14
    movi    r1, ETH_MPLS_UC
    bne     r0, r1, drop
    movi    r3, 1              ; one-hot label counter

mpls:                          ; cursor sits on a 4-byte MPLS label
    movi    r1, MPLS_MAX_ONEHOT
    beq     r3, r1, drop       ; sixth label: refuse
    sethdr  h_mpls             ; last label wins (offsets overwritten per pass)
    ext     r0, 0, 20          ; label
    stmd    1, r0, 2           ; -> md slots 1..2
    ext     r1, 23, 1          ; bos
    ext     r2, 32, 4          ; LOOKAHEAD: 4 bits past this header
    advi    4
    shl     r3, r3, 1
    beq     r1, rz, mpls       ; bos == 0: another label

payload:                       ; r2 = first nibble of what follows the stack
    stmd    3, r2, 1
    movi    r1, 4
    beq     r2, r1, ipv4
    movi    r1, 6
    beq     r2, r1, ipv6
    beq     r2, rz, eompls     ; 0 -> EoMPLS control word
    halt    drop               ; unknown payload

ipv4:
    sethdr  h_ipv4
    ext     r0, 4, 4           ; IHL
    shl     r0, r0, 2          ; * 4 bytes
    advr    r0
    halt    accept

ipv6:
    sethdr  h_ipv6
    advi    40
    halt    accept

eompls:                        ; 4-byte control word, then an inner Ethernet
    advi    4
    sethdr  h_eth2
    advi    14
    halt    accept

drop:
    halt    drop
