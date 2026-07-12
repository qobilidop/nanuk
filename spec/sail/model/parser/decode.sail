// Instruction encoding/decoding: instr <-> bits(32).
// Opcode at [31:26]; fields packed MSB-first; reserved bits are zero and
// enforced (nonzero reserved bits do not match -> ILLEGAL). The all-zeros
// word has opcode 0x00, which is unassigned -> ILLEGAL by design.

// Register index <-> 3 bits. Codes 0b101-0b111 have no mapping -> ILLEGAL.
mapping encdec_reg : regidx <-> bits(3) = {
    R0 <-> 0b000,
    R1 <-> 0b001,
    R2 <-> 0b010,
    R3 <-> 0b011,
    RZ <-> 0b100,
}

mapping encdec_bool : bool <-> bits(1) = {
    false <-> 0b0,
    true  <-> 0b1,
}

val encdec : instr <-> bits32
scattered mapping encdec

// Opcode 0x01: EXT rd, boff(11), bsize-1(6)
mapping clause encdec = EXT(rd, boff, szm1)
    <-> 0b000001 @ encdec_reg(rd) @ boff @ szm1 @ 0b000000 : bits(6)

// Opcode 0x02: ADVI imm16
mapping clause encdec = ADVI(imm)
    <-> 0b000010 @ 0b0000000000 : bits(10) @ imm

// Opcode 0x03: ADVR rs
mapping clause encdec = ADVR(rs)
    <-> 0b000011 @ encdec_reg(rs) @ 0b00000000000000000000000 : bits(23)

// Opcode 0x04: MOVI rd, imm16
mapping clause encdec = MOVI(rd, imm)
    <-> 0b000100 @ encdec_reg(rd) @ 0b0000000 : bits(7) @ imm

// Opcode 0x05: SHL rd, rs, shamt(6)
mapping clause encdec = SHL(rd, rs, sh)
    <-> 0b000101 @ encdec_reg(rd) @ encdec_reg(rs) @ sh @ 0b00000000000000 : bits(14)

// Opcode 0x06: BEQ rs, rt, target16 (absolute word address)
mapping clause encdec = BEQ(rs, rt, tgt)
    <-> 0b000110 @ encdec_reg(rs) @ encdec_reg(rt) @ 0b0000 : bits(4) @ tgt

// Opcode 0x07: BNE rs, rt, target16
mapping clause encdec = BNE(rs, rt, tgt)
    <-> 0b000111 @ encdec_reg(rs) @ encdec_reg(rt) @ 0b0000 : bits(4) @ tgt

// Opcode 0x08: JMP target16
mapping clause encdec = JMP(tgt)
    <-> 0b001000 @ 0b0000000000 : bits(10) @ tgt

// Opcode 0x09: SETHDR hdr(4)
mapping clause encdec = SETHDR(h)
    <-> 0b001001 @ 0b0000000000000000000000 : bits(22) @ h

// Opcode 0x0A: STMD rs, nunits-1(2), slot(4)
mapping clause encdec = STMD(rs, nm1, slot)
    <-> 0b001010 @ encdec_reg(rs) @ nm1 @ slot @ 0b00000000000000000 : bits(17)

// Opcode 0x0B: HALT drop(1)
mapping clause encdec = HALT(drop)
    <-> 0b001011 @ 0b0000000000000000000000000 : bits(25) @ encdec_bool(drop)

end encdec

// Decode with total fallthrough: anything the mapping doesn't match is ILLEGAL.
val decode : bits32 -> instr
function decode(w) = match w {
    encdec(i) => i,
    _ => ILLEGAL(),
}

// Encode (used by tests; the assembler mirrors this table in Python).
val encode : instr -> bits32
function encode(i) = encdec(i)
