// Execute tests: EXT, ADVI, ADVR, MOVI, SHL, and their error paths.

// Load an Ethernet-ish prefix into the packet buffer for extraction tests.
val load_test_packet : unit -> unit
function load_test_packet() = {
    emu_reset();
    // Bytes 0..3: 0x45 0x00 0xAB 0xCD (0x45 = IPv4 version 4, IHL 5)
    emu_poke_pkt(0x0000, 0x45);
    emu_poke_pkt(0x0001, 0x00);
    emu_poke_pkt(0x0002, 0xAB);
    emu_poke_pkt(0x0003, 0xCD);
    emu_set_plen(0x0004)
}

val test_ext_basic : unit -> unit
function test_ext_basic() = {
    load_test_packet();
    execute(EXT(R0, 0b00000010000, 0b001111));  // boff 16, 16 bits
    assert(read_reg(R0) == 0x00000000_0000ABCD, "EXT 16 bits at bit 16");
    assert(not_bool(halted), "EXT in range does not halt")
}

val test_ext_subbyte : unit -> unit
function test_ext_subbyte() = {
    load_test_packet();
    // IPv4 version: 4 bits at bit 0 -> 4; IHL: 4 bits at bit 4 -> 5.
    execute(EXT(R1, 0b00000000000, 0b000011));
    assert(read_reg(R1) == 0x00000000_00000004, "version nibble = 4");
    execute(EXT(R2, 0b00000000100, 0b000011));
    assert(read_reg(R2) == 0x00000000_00000005, "IHL nibble = 5")
}

val test_ext_respects_cursor : unit -> unit
function test_ext_respects_cursor() = {
    load_test_packet();
    cursor = 0x0002;
    execute(EXT(R0, 0b00000000000, 0b000111));  // 8 bits at cursor 2
    assert(read_reg(R0) == 0x00000000_000000AB, "EXT is cursor-relative")
}

val test_ext_violation : unit -> unit
function test_ext_violation() = {
    load_test_packet();  // plen = 4
    execute(EXT(R0, 0b00000011101, 0b000011));  // bits 29..32 cross plen*8=32
    assert(halted, "EXT past packet end halts");
    assert(emu_get_verdict() == verdict_error, "EXT violation verdict");
    assert(emu_get_error() == err_hdr_violation, "EXT violation error code")
}

val test_advi : unit -> unit
function test_advi() = {
    load_test_packet();
    execute(ADVI(0x0003));
    assert(unsigned(cursor) == 3, "ADVI advances cursor");
    execute(ADVI(0x0001));
    assert(unsigned(cursor) == 4, "cursor at packet end is legal");
    execute(ADVI(0x0001));
    assert(halted, "ADVI past packet end halts");
    assert(emu_get_error() == err_hdr_violation, "ADVI violation error code")
}

val test_advr_uses_low16 : unit -> unit
function test_advr_uses_low16() = {
    load_test_packet();
    // Upper register bits must be ignored (defined semantics).
    write_reg(R1, 0xFFFF0000_00000002);
    execute(ADVR(R1));
    assert(unsigned(cursor) == 2, "ADVR uses rs[15:0] only")
}

val test_movi_zero_extends : unit -> unit
function test_movi_zero_extends() = {
    emu_reset();
    write_reg(R0, 0xFFFFFFFF_FFFFFFFF);
    execute(MOVI(R0, 0x8100));
    assert(read_reg(R0) == 0x00000000_00008100, "MOVI zero-extends imm16")
}

val test_shl : unit -> unit
function test_shl() = {
    emu_reset();
    write_reg(R1, 0x00000000_00000005);
    execute(SHL(R1, R1, 0b000010));
    assert(read_reg(R1) == 0x00000000_00000014, "5 << 2 = 20 (IHL*4)");
    write_reg(R2, 0x80000000_00000001);
    execute(SHL(R2, R2, 0b000001));
    assert(read_reg(R2) == 0x00000000_00000002, "SHL truncates at 64 bits")
}

val main : unit -> unit
function main() = {
    test_ext_basic();
    test_ext_subbyte();
    test_ext_respects_cursor();
    test_ext_violation();
    test_advi();
    test_advr_uses_low16();
    test_movi_zero_extends();
    test_shl();
    print_endline("test_exec_linear: OK")
}
