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
// Field conventions follow the stage-1 plan's encoding table:
//   EXT    rd, bit offset from cursor (11b), size-1 (6b -> 1..64 bits)
//   STMD   rs, nunits-1 (2b -> 1..4 slots), slot (4b)
//   LDMD   rd, md slot id (4b; slots >= md_slots are illegal at execute)
//   HALT   drop flag (false = accept)
union instr = {
    EXT     : (regidx, bits11, bits(6)),
    ADVI    : bits16,
    ADVR    : regidx,
    MOVI    : (regidx, bits16),
    SHL     : (regidx, regidx, bits(6)),
    BEQ     : (regidx, regidx, bits16),
    BNE     : (regidx, regidx, bits16),
    JMP     : bits16,
    SETHDR  : bits(4),
    STMD    : (regidx, bits(2), bits(4)),
    HALT    : bool,
    LDMD    : (regidx, bits(4)),
    ILLEGAL : unit,
}
