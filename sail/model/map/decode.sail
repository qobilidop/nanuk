// Instruction encoding/decoding: instr <-> bits(32).
// Opcode at [31:26]; fields packed MSB-first; reserved bits are zero and
// enforced (nonzero reserved bits do not match -> ILLEGAL). The all-zeros
// word has opcode 0x00, which is unassigned -> ILLEGAL by design.
// `off` (LD/ST/CSUMUPD) and `delta` (SEND) fields are 10-bit two's
// complement; sign-extension happens at execute.

// Register index <-> 3 bits. Codes 0b101-0b111 have no mapping -> ILLEGAL.
mapping encdec_reg : regidx <-> bits(3) = {
    R0 <-> 0b000,
    R1 <-> 0b001,
    R2 <-> 0b010,
    R3 <-> 0b011,
    RZ <-> 0b100,
}

val encdec : instr <-> bits32
scattered mapping encdec

// Opcode 0x01: LD rd, hdr(4), off(10), nbytes-1(3)
mapping clause encdec = LD(rd, h, off, nm1)
    <-> 0b000001 @ encdec_reg(rd) @ h @ off @ nm1 @ 0b000000 : bits(6)

// Opcode 0x02: ST rs, hdr(4), off(10), nbytes-1(3)
mapping clause encdec = ST(rs, h, off, nm1)
    <-> 0b000010 @ encdec_reg(rs) @ h @ off @ nm1 @ 0b000000 : bits(6)

// Opcode 0x03: LDMD rd, field(4)
mapping clause encdec = LDMD(rd, f)
    <-> 0b000011 @ encdec_reg(rd) @ f @ 0b0000000000000000000 : bits(19)

// Opcode 0x04: MOVI rd, imm16
mapping clause encdec = MOVI(rd, imm)
    <-> 0b000100 @ encdec_reg(rd) @ 0b0000000 : bits(7) @ imm

// Opcode 0x05: ADDI rd, rs, imm16 (imm sign-extended at execute)
mapping clause encdec = ADDI(rd, rs, imm)
    <-> 0b000101 @ encdec_reg(rd) @ encdec_reg(rs) @ 0b0000 : bits(4) @ imm

// Opcode 0x06: BEQ rs, rt, target16 (absolute word address)
mapping clause encdec = BEQ(rs, rt, tgt)
    <-> 0b000110 @ encdec_reg(rs) @ encdec_reg(rt) @ 0b0000 : bits(4) @ tgt

// Opcode 0x07: BNE rs, rt, target16
mapping clause encdec = BNE(rs, rt, tgt)
    <-> 0b000111 @ encdec_reg(rs) @ encdec_reg(rt) @ 0b0000 : bits(4) @ tgt

// Opcode 0x08: JMP target16
mapping clause encdec = JMP(tgt)
    <-> 0b001000 @ 0b0000000000 : bits(10) @ tgt

// Opcode 0x09: LOOKUP rd, table(4), rs, miss-target16
mapping clause encdec = LOOKUP(rd, t, rs, tgt)
    <-> 0b001001 @ encdec_reg(rd) @ t @ encdec_reg(rs) @ tgt

// Opcode 0x0A: CSUMUPD hdr(4), off(10)
mapping clause encdec = CSUMUPD(h, off)
    <-> 0b001010 @ h @ off @ 0b000000000000 : bits(12)

// Opcode 0x0B: SEND rs, delta(10)
mapping clause encdec = SEND(rs, delta)
    <-> 0b001011 @ encdec_reg(rs) @ delta @ 0b0000000000000 : bits(13)

// Opcode 0x0C: DROP
mapping clause encdec = DROP()
    <-> 0b001100 @ 0b00000000000000000000000000 : bits(26)

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
