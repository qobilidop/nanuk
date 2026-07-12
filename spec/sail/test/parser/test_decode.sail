// Encode/decode round-trip tests for every instruction, plus totality:
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
    // Golden words hand-computed from the plan's encoding table; the Python
    // assembler mirrors these exact values in its own tests.
    check_roundtrip(EXT(R0, 0b00001100000, 0b001111), 0x040603C0, "EXT r0, 96, 16");
    check_roundtrip(ADVI(0x000E), 0x0800000E, "ADVI 14");
    check_roundtrip(ADVR(R1), 0x0C800000, "ADVR r1");
    check_roundtrip(MOVI(R0, 0x0007), 0x10000007, "MOVI r0, 7");
    check_roundtrip(MOVI(R1, 0x8100), 0x10808100, "MOVI r1, 0x8100");
    check_roundtrip(SHL(R1, R1, 0b000010), 0x14908000, "SHL r1, r1, 2");
    check_roundtrip(BEQ(R0, R1, 0x0009), 0x18100009, "BEQ r0, r1, 9");
    check_roundtrip(BNE(R1, R2, 0x0015), 0x1CA00015, "BNE r1, r2, 21");
    check_roundtrip(JMP(0x0005), 0x20000005, "JMP 5");
    check_roundtrip(SETHDR(0x3), 0x24000003, "SETHDR 3");
    check_roundtrip(STMD(R0, 0b10, 0b0000), 0x28400000, "STMD 0, r0, 3");
    check_roundtrip(HALT(false), 0x2C000000, "HALT accept");
    check_roundtrip(HALT(true), 0x2C000001, "HALT drop")
}

val test_illegal_words : unit -> unit
function test_illegal_words() = {
    // The all-zeros word (opcode 0x00) is illegal by design.
    assert(is_illegal(decode(0x00000000)), "all-zeros word is ILLEGAL");
    // Unassigned opcode above HALT.
    assert(is_illegal(decode(0x30000000)), "opcode 0x0C is ILLEGAL");
    assert(is_illegal(decode(0xFFFFFFFF)), "all-ones word is ILLEGAL");
    // HALT with nonzero reserved bits.
    assert(is_illegal(decode(0x2C000002)), "HALT with reserved bit set is ILLEGAL");
    // ADVI with nonzero reserved bits [25:16].
    assert(is_illegal(decode(0x0801000E)), "ADVI with reserved bit set is ILLEGAL");
    // Register code 0b101 (unassigned) in ADVR.
    assert(is_illegal(decode(0x0E800000)), "register code 5 is ILLEGAL")
}

val main : unit -> unit
function main() = {
    test_golden_encodings();
    test_illegal_words();
    print_endline("test_decode: OK")
}
