; Benchmark E3 — compute on packet-carried operands. The network does the math.
;
; This is the benchmark that changed the ISA. It is the only program that
; appears in TWO independent corpora -- p4lang's `calc` exercise and xISA's own
; `network-calculator` walkthrough, the latter written by the architecture
; Nanuk is descended from -- and until v0.1 it was the only one Nanuk could not
; run at all.
;
; The reason is worth stating plainly. The entire MAP ALU was `addi`/`andi`/
; `shli`: every arithmetic instruction took one register and one IMMEDIATE. A
; machine like that can decrement a TTL, mask a field, and shift a bitmap --
; and it cannot compute `a - b` when both a and b arrived on the wire. No
; amount of cleverness works around it; there is nowhere to put the second
; operand. `add reg,reg` had been dismissed as YAGNI during the core redesign.
; One program, in two corpora, said otherwise.
;
; Send it `7 + 5` and 12 comes back, computed by the switch.
;
; DISPATCH, and a deliberate deviation from xISA: five opcodes, five `beq`s,
; ten instructions. xISA does it in three -- it looks the opcode up in a table
; whose VALUES ARE JUMP ADDRESSES and branches through a register. That is
; strictly better, and we did not take it, because no corpus program *forces*
; a register-indirect branch. What is lost is not the behaviour but the lesson:
; on xISA the control plane can add an operator without recompiling, because it
; owns the data plane's control flow. Nanuk cannot do that at all. It is the
; one place in the whole audit where we land on "impossible" rather than
; "merely verbose", and it is recorded rather than papered over.

.equ h_eth  0
.equ h_calc 1

.equ t_reflect 0               ; {ingress port -> egress bitmap}

; ASCII opcodes, exactly as in the p4 exercise.
.equ OP_ADD 0x2b               ; '+'
.equ OP_SUB 0x2d               ; '-'
.equ OP_AND 0x26               ; '&'
.equ OP_OR  0x7c               ; '|'
.equ OP_XOR 0x5e               ; '^'

start:
    ld      r0, h_calc, 4, 4   ; operand a
    ld      r1, h_calc, 8, 4   ; operand b
    ld      r2, h_calc, 3, 1   ; opcode

dispatch:
    movi    r3, OP_ADD
    beq     r2, r3, do_add
    movi    r3, OP_SUB
    beq     r2, r3, do_sub
    movi    r3, OP_AND
    beq     r2, r3, do_and
    movi    r3, OP_OR
    beq     r2, r3, do_or
    movi    r3, OP_XOR
    beq     r2, r3, do_xor
    drop                       ; an opcode we do not implement

do_add:
    add     r0, r0, r1
    jmp     reply
do_sub:
    sub     r0, r0, r1         ; 64-bit wraparound; the store truncates to 32
    jmp     reply
do_and:
    and     r0, r0, r1
    jmp     reply
do_or:
    or      r0, r0, r1
    jmp     reply
do_xor:
    xor     r0, r0, r1

reply:
    st      r0, h_calc, 12, 4  ; result -- low 4 bytes, so 32-bit two's
                               ; complement falls out for free
    ; Store the result BEFORE reusing r0: the register file is four deep and
    ; the result is living in it.
    ld      r0, h_eth, 0, 6    ; swap MACs: answer the sender
    ld      r1, h_eth, 6, 6
    st      r1, h_eth, 0, 6
    st      r0, h_eth, 6, 6

    ldmd    r0, 0              ; 1 << ingress is not computable -- table it
    lookup  r1, t_reflect, r0, discard
    stmd    r1, 1, 0
    send    0                  ; the frame is the same length it arrived

discard:
    drop
