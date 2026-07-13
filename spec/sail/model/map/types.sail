// Instruction set types: register indices and the instruction union.

enum regidx = { R0, R1, R2, R3, RZ }

// Equality for regidx (needed for assertions in tests).
val eq_regidx : (regidx, regidx) -> bool
function eq_regidx(a, b) = match (a, b) {
    (R0, R0) => true,
    (R1, R1) => true,
    (R2, R2) => true,
    (R3, R3) => true,
    (RZ, RZ) => true,
    (_, _) => false,
}

overload operator == = {eq_regidx}

// The strict v0 core: 15 instructions + ILLEGAL.
// Field conventions (see the core-interface redesign spec):
//   LD/ST    reg, hdr base id (4b; 15 = h_frame), byte offset (10b two's
//            complement), size-1 (3b -> 1..8 bytes)
//   LDMD     rd, md slot id (4b; slots >= md_slots are illegal at execute)
//   STMD     rs, nunits-1 (2b), md slot (4b) — 16-bit units, MSB-first
//   ADDI     immediate is sign-extended
//   ANDI     immediate is zero-extended
//   SHLI     shift amount (6b), 64-bit truncating
//   LOOKUP   rd, table id (4b), key rs, miss target (absolute word address)
//   CSUM     rd, hdr base id, byte offset (10b two's complement), length reg
//   SEND     head delta (10b two's complement) — egress meaning lives in md
//   ADD/SUB/AND/OR/XOR
//            rd, rs, rt — register-register, [16:0] reserved. The immediate-only
//            ALU could not compute on two operands that both came off the wire;
//            the calculator benchmark (p4 `calc`, xISA `network-calculator`)
//            demands it. No MUL: shift-and-add is expressible, and 64-bit
//            registers make xISA's carry-propagation dance unnecessary.
union instr = {
    LD      : (regidx, bits4, bits10, bits3),
    ST      : (regidx, bits4, bits10, bits3),
    LDMD    : (regidx, bits4),
    MOVI    : (regidx, bits16),
    ADDI    : (regidx, regidx, bits16),
    BEQ     : (regidx, regidx, bits16),
    BNE     : (regidx, regidx, bits16),
    JMP     : bits16,
    LOOKUP  : (regidx, bits4, regidx, bits16),
    CSUM    : (regidx, bits4, bits10, regidx),
    SEND    : bits10,
    DROP    : unit,
    STMD    : (regidx, bits2, bits4),
    ANDI    : (regidx, regidx, bits16),
    SHLI    : (regidx, regidx, bits6),
    ADD     : (regidx, regidx, regidx),
    SUB     : (regidx, regidx, regidx),
    AND     : (regidx, regidx, regidx),
    OR      : (regidx, regidx, regidx),
    XOR     : (regidx, regidx, regidx),
    ILLEGAL : unit,
}
