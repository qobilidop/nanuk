; Benchmark E3 (parser half) — the in-network calculator's custom protocol.
;
; Ethernet (EtherType 0x1234) then a 16-byte header:
;   [0..1] magic 'P4'   [2] version   [3] opcode (ASCII)
;   [4..7] operand a    [8..11] operand b    [12..15] result
;
; The magic and version are checked HERE, in the parser, so the MAP program can
; assume a well-formed header and spend its instructions on arithmetic. That
; split is the point of having two processors.

.equ h_eth  0
.equ h_calc 1

.equ ET_CALC  0x1234
.equ MAGIC_P4 0x5034           ; 'P','4'
.equ VERSION  1

start:
    sethdr  h_eth
    ext     r0, 96, 16         ; EtherType
    advi    14
    movi    r1, ET_CALC
    bne     r0, r1, drop

calc:
    ext     r0, 0, 16          ; magic
    movi    r1, MAGIC_P4
    bne     r0, r1, drop
    ext     r0, 16, 8          ; version
    movi    r1, VERSION
    bne     r0, r1, drop
    sethdr  h_calc
    advi    16
    halt    accept

drop:
    halt    drop
