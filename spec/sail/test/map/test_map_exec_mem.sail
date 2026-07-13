// Execute tests: LD/ST/LDMD/STMD/MOVI/ADDI/ANDI/SHLI, addressing totality
// (absent header, window violation, headroom reach), md slot bounds.

// Set up a fresh state with a plen-byte frame of 0x00 and h2 present at
// frame offset 14 (an "IPv4 after Ethernet" shape).
val setup : int -> unit
function setup(pl) = {
    reset_map_state();
    plen = to_bits16(pl);
    hdr_present_in[2] = true;
    hdr_offset_in[2] = 0x000E;
    md[0] = 0x0001
}

val test_st_ld_roundtrip : unit -> unit
function test_st_ld_roundtrip() = {
    setup(64);
    // ST r0 (6 bytes of a MAC) at h_frame+0, LD it back.
    write_reg(R0, 0x0000_02DE_ADBE_EF01);
    execute(ST(R0, 0xF, 0b0000000000, 0b101));
    assert(not_bool(halted), "ST in-window does not halt");
    assert(read_win_byte(32) == 0x02, "ST wrote MSB first at frame start");
    assert(read_win_byte(37) == 0x01, "ST wrote LSB last");
    execute(LD(R1, 0xF, 0b0000000000, 0b101));
    assert(read_reg(R1) == 0x0000_02DE_ADBE_EF01, "LD round-trips the ST");
    // Headroom reach: ST 2 bytes at h_frame-22 lands at window index 10.
    write_reg(R2, 0x0000_0000_0000_8100);
    execute(ST(R2, 0xF, 0b1111101010, 0b001));
    assert(read_win_byte(10) == 0x81, "headroom write hi byte");
    assert(read_win_byte(11) == 0x00, "headroom write lo byte");
    // hdr-relative: LD 1 byte at h2+8 = frame offset 22 = window 54.
    write_win_byte(54, 0x40);
    execute(LD(R3, 0x2, 0b0000001000, 0b000));
    assert(read_reg(R3) == 0x0000_0000_0000_0040, "hdr-relative LD");
    print_endline("test_st_ld_roundtrip passed")
}

val test_addressing_totality : unit -> unit
function test_addressing_totality() = {
    // Absent header id 3 -> err_hdr_absent.
    setup(64);
    execute(LD(R0, 0x3, 0b0000000000, 0b000));
    assert(halted & err == err_hdr_absent, "LD absent header errors");
    // Past frame end (plen 64; h_frame+63 legal for 1 byte, +64 not).
    setup(64);
    execute(LD(R0, 0xF, 0b0001000000, 0b000));
    assert(halted & err == err_window_violation, "LD past frame end errors");
    setup(64);
    execute(LD(R0, 0xF, 0b0000111111, 0b000));
    assert(not_bool(halted), "LD at last frame byte is legal");
    // Below the window: h_frame-33 with 32-byte headroom.
    setup(64);
    execute(ST(R0, 0xF, 0b1111011111, 0b000));
    assert(halted & err == err_window_violation, "ST below headroom errors");
    // Straddling the frame-end boundary with a multi-byte access.
    setup(64);
    execute(LD(R0, 0xF, 0b0000111111, 0b001));
    assert(halted & err == err_window_violation, "2-byte LD straddling frame end errors");
    print_endline("test_addressing_totality passed")
}

val test_ldmd_stmd : unit -> unit
function test_ldmd_stmd() = {
    setup(64);
    md[3] = 0x1234;
    execute(LDMD(R0, 0x3));
    assert(read_reg(R0) == 0x0000_0000_0000_1234, "LDMD reads a harness-set slot");
    execute(LDMD(R1, 0x0));
    assert(read_reg(R1) == 0x0000_0000_0000_0001, "LDMD slot 0 (pass-through)");
    // STMD single unit: low 16 bits land in the slot.
    write_reg(R2, 0x0000_0000_0001_000D);
    execute(STMD(R2, 0b00, 0x0));
    assert(md[0] == 0x000D, "STMD writes the low unit");
    // STMD multi-unit, MSB-first: 4 units of r3 into slots 4-7.
    write_reg(R3, 0x1111_2222_3333_4444);
    execute(STMD(R3, 0b11, 0x4));
    assert(md[4] == 0x1111 & md[5] == 0x2222 & md[6] == 0x3333 & md[7] == 0x4444,
           "STMD writes MSB-first across slots");
    // Round-trip: LDMD sees what STMD wrote.
    execute(LDMD(R0, 0x5));
    assert(read_reg(R0) == 0x0000_0000_0000_2222, "LDMD/STMD round-trip");
    // Slots >= md_slots are illegal (halt is sticky: fresh setup each).
    setup(64);
    execute(LDMD(R1, 0x8));
    assert(halted & err == err_illegal, "LDMD slot 8 is illegal");
    setup(64);
    execute(LDMD(R1, 0xF));
    assert(halted & err == err_illegal, "LDMD slot 15 is illegal");
    // STMD crossing the top of the window is illegal.
    setup(64);
    execute(STMD(R0, 0b11, 0x6));
    assert(halted & err == err_illegal, "STMD slots 6..9 is illegal");
    print_endline("test_ldmd_stmd passed")
}

val test_movi_addi : unit -> unit
function test_movi_addi() = {
    setup(64);
    execute(MOVI(R0, 0x8100));
    assert(read_reg(R0) == 0x0000_0000_0000_8100, "MOVI zero-extends");
    execute(ADDI(R1, R0, 0x0001));
    assert(read_reg(R1) == 0x0000_0000_0000_8101, "ADDI positive");
    execute(ADDI(R1, R1, 0xFFFF));
    assert(read_reg(R1) == 0x0000_0000_0000_8100, "ADDI -1 decrements");
    // 64-bit wraparound: 0 + (-1).
    execute(ADDI(R2, RZ, 0xFFFF));
    assert(read_reg(R2) == 0xFFFF_FFFF_FFFF_FFFF, "ADDI wraps at zero");
    // Writes to RZ are discarded.
    execute(MOVI(RZ, 0x00FF));
    assert(read_reg(RZ) == sail_zeros(64), "RZ write discarded");
    print_endline("test_movi_addi passed")
}

val test_andi_shli : unit -> unit
function test_andi_shli() = {
    setup(64);
    // ANDI zero-extends the immediate: high bits always cleared.
    write_reg(R0, 0xFFFF_FFFF_FFFF_FF45);
    execute(ANDI(R1, R0, 0x000F));
    assert(read_reg(R1) == 0x0000_0000_0000_0005, "ANDI masks to the immediate");
    execute(ANDI(R1, R0, 0xFFFF));
    assert(read_reg(R1) == 0x0000_0000_0000_FF45, "ANDI immediate is zero-extended");
    // SHLI truncates at 64 bits, PP SHL semantics.
    execute(MOVI(R2, 0x0005));
    execute(SHLI(R2, R2, 0b000010));
    assert(read_reg(R2) == 0x0000_0000_0000_0014, "SHLI 5 << 2 = 20");
    write_reg(R3, 0x8000_0000_0000_0001);
    execute(SHLI(R3, R3, 0b000001));
    assert(read_reg(R3) == 0x0000_0000_0000_0002, "SHLI truncates at 64 bits");
    // Reads of RZ are zero; writes discarded.
    execute(SHLI(RZ, R0, 0b000001));
    assert(read_reg(RZ) == sail_zeros(64), "SHLI to RZ discarded");
    print_endline("test_andi_shli passed")
}

val main : unit -> unit
function main() = {
    test_st_ld_roundtrip();
    test_addressing_totality();
    test_ldmd_stmd();
    test_movi_addi();
    test_andi_shli();
    print_endline("test_map_exec_mem: all assertions passed")
}
