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

// The strict v0 core: 12 instructions + ILLEGAL.
// Field conventions follow the M1 plan's encoding table:
//   LD/ST    reg, hdr base id (4b; 15 = h_frame), byte offset (10b two's
//            complement), size-1 (3b -> 1..8 bytes)
//   LDMD     rd, inbound-SMD field id (4b)
//   ADDI     immediate is sign-extended
//   LOOKUP   rd, table id (4b), key rs, miss target (absolute word address)
//   CSUMUPD  hdr base id, byte offset (10b two's complement)
//   SEND     port bitmap rs, head delta (10b two's complement)
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
    CSUMUPD : (bits4, bits10),
    SEND    : (regidx, bits10),
    DROP    : unit,
    ILLEGAL : unit,
}
