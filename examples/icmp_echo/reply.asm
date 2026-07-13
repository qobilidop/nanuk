; Benchmark E1 — fixed rewrite. The switch answers a ping itself.
;
; An ICMP echo request is turned into an echo reply IN PLACE: type 8 -> 0,
; MAC addresses swapped, IPv4 addresses swapped, and the frame reflected out
; the port it arrived on. The frame length never changes, no table decides the
; policy, and the payload is never touched -- the whole program is edits.
;
; Three things this benchmark pins down, each a fact about the machine rather
; than about ICMP:
;
; 1. CSUM CANNOT DO THIS. `csum` computes a ones-complement sum over a byte
;    RANGE whose length comes from a register, and the ICMP checksum covers
;    the whole message -- header plus payload -- whose length MAP has no way
;    to read. So the checksum is patched INCREMENTALLY instead: the type byte
;    fell by 8, so the ones-complement checksum rises by 0x0800. That is a
;    property of ones-complement arithmetic, not a trick.
;
; 2. END-AROUND CARRY WITHOUT A CARRY FLAG. MAP is flagless and has no right
;    shift. The carry out of bit 15 is recovered by masking to 16 bits and
;    asking whether anything was lost: `andi` then `beq`. If the masked value
;    differs from the unmasked one, a carry fell off, and it is added back.
;
; 3. SWAPPING IPv4 ADDRESSES DOES NOT DISTURB THE HEADER CHECKSUM. It is a
;    sum, and addition commutes. Nothing to recompute; the program simply
;    doesn't.
;
; And one limitation it makes visible: reflecting a frame back out its ingress
; port needs the bitmap `1 << ingress`, which MAP cannot compute -- `shli`
; takes an immediate, and there is no shift-by-register. The bitmap therefore
; comes from a table the control plane installs. The same wall the flood table
; hit, met from the other side.

.equ h_eth  0
.equ h_ipv4 1
.equ h_icmp 2

.equ t_reflect 0               ; {ingress port -> egress bitmap}

.equ ICMP_ECHO_REQUEST 8

start:
    ld      r0, h_icmp, 0, 1   ; ICMP type
    movi    r1, ICMP_ECHO_REQUEST
    bne     r0, r1, discard    ; not a ping: not ours to answer

echo_reply:
    st      rz, h_icmp, 0, 1   ; type 8 -> 0

checksum:                      ; incremental: the sum fell by 0x0800
    ld      r0, h_icmp, 2, 2
    addi    r0, r0, 0x0800
    andi    r1, r0, 0xffff     ; did a carry fall off bit 15?
    beq     r0, r1, no_carry
    addi    r1, r1, 1          ; end-around carry, flaglessly
no_carry:
    st      r1, h_icmp, 2, 2

swap_mac:
    ld      r0, h_eth, 0, 6    ; dst
    ld      r1, h_eth, 6, 6    ; src
    st      r1, h_eth, 0, 6
    st      r0, h_eth, 6, 6

swap_ip:                       ; the header checksum is a sum; swapping two of
    ld      r0, h_ipv4, 12, 4  ; its terms leaves it unchanged
    ld      r1, h_ipv4, 16, 4
    st      r1, h_ipv4, 12, 4
    st      r0, h_ipv4, 16, 4

reflect:                       ; 1 << ingress is not computable -- table it
    ldmd    r0, 0              ; ingress port
    lookup  r1, t_reflect, r0, discard
    stmd    r1, 1, 0           ; egress bitmap -> md slot 0
    send    0                  ; length unchanged

discard:
    drop
