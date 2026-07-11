// Encode/decode round-trip tests for every MAP instruction, plus totality:
// unassigned opcodes, nonzero reserved bits, and bad register codes are ILLEGAL.

val is_illegal : instr -> bool
function is_illegal(i) = match i {
    ILLEGAL() => true,
    _ => false,
}

val check_roundtrip : (instr, bits32, string) -> unit
function check_roundtrip(i, expected_word, name) = {
    let w = encode(i);
    assert(w == expected_word, concat_str(name, ": encoding matches golden word"));
    let d = decode(w);
    assert(encode(d) == w, concat_str(name, ": decode(encode(i)) re-encodes identically"))
}

val test_golden_encodings : unit -> unit
function test_golden_encodings() = {
    // Golden words hand-computed from the M1 plan's encoding table; the
    // Python encoding mirror pins these exact values in its own tests.
    check_roundtrip(LD(R0, 0xF, 0b0000001000, 0b000), 0x04781000, "LD r0, h_frame, 8, 1");
    check_roundtrip(ST(R1, 0x2, 0b1111101010, 0b101), 0x0897D540, "ST r1, h2, -22, 6");
    check_roundtrip(LDMD(R2, 0x9), 0x0D480000, "LDMD r2, md_flood");
    check_roundtrip(MOVI(R3, 0x8100), 0x11808100, "MOVI r3, 0x8100");
    check_roundtrip(ADDI(R0, R0, 0xFFFF), 0x1400FFFF, "ADDI r0, r0, -1");
    check_roundtrip(BEQ(R0, R1, 0x0007), 0x18100007, "BEQ r0, r1, 7");
    check_roundtrip(BNE(R2, RZ, 0x0003), 0x1D400003, "BNE r2, rz, 3");
    check_roundtrip(JMP(0x0002), 0x20000002, "JMP 2");
    check_roundtrip(LOOKUP(R1, 0x0, R0, 0x0005), 0x24800005, "LOOKUP r1, t0, r0, 5");
    check_roundtrip(LOOKUP(R1, 0x3, R2, 0x0004), 0x249A0004, "LOOKUP r1, t3, r2, 4");
    check_roundtrip(CSUMUPD(0x2, 0b0000000000), 0x28800000, "CSUMUPD h2, 0");
    check_roundtrip(CSUMUPD(0xF, 0b1111111100), 0x2BFFC000, "CSUMUPD h_frame, -4");
    check_roundtrip(SEND(R1, 0b0000010110), 0x2C82C000, "SEND r1, 22");
    check_roundtrip(SEND(R0, 0b1111101010), 0x2C7D4000, "SEND r0, -22");
    check_roundtrip(DROP(), 0x30000000, "DROP")
}

val test_illegal_words : unit -> unit
function test_illegal_words() = {
    // The all-zeros word (opcode 0x00) is illegal by design.
    assert(is_illegal(decode(0x00000000)), "all-zeros word is ILLEGAL");
    // Unassigned opcodes (0x0D and up).
    assert(is_illegal(decode(0x34000000)), "opcode 0x0D is ILLEGAL");
    assert(is_illegal(decode(0xFC000000)), "opcode 0x3F is ILLEGAL");
    // Nonzero reserved bits do not match the mapping.
    assert(is_illegal(decode(0x04781001)), "LD with nonzero reserved bits is ILLEGAL");
    assert(is_illegal(decode(0x30000001)), "DROP with nonzero reserved bits is ILLEGAL");
    assert(is_illegal(decode(0x0D480001)), "LDMD with nonzero reserved bits is ILLEGAL");
    // Bad register codes (0b101-0b111).
    assert(is_illegal(decode(0x06F81000)), "LD with reg code 0b101 is ILLEGAL");
    assert(is_illegal(decode(0x2FFA0000)), "SEND with reg code 0b111 is ILLEGAL")
}

val main : unit -> unit
function main() = {
    test_golden_encodings();
    test_illegal_words();
    print_endline("test_map_decode: all assertions passed")
}
