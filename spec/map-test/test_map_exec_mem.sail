// Execute tests: LD/ST/LDMD/MOVI/ADDI, addressing totality (absent header,
// window violation, headroom reach).

// Set up a fresh state with a plen-byte frame of 0x00 and h2 present at
// frame offset 14 (an "IPv4 after Ethernet" shape).
val setup : int -> unit
function setup(pl) = {
    reset_map_state();
    plen = to_bits16(pl);
    hdr_present_in[2] = true;
    hdr_offset_in[2] = 0x000E;
    ingress = 0x01
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

val test_ldmd : unit -> unit
function test_ldmd() = {
    setup(64);
    smd_in[0] = 0x1234;
    execute(LDMD(R0, 0x0));
    assert(read_reg(R0) == 0x0000_0000_0000_1234, "LDMD SMD slot 0");
    execute(LDMD(R1, 0x8));
    assert(read_reg(R1) == 0x0000_0000_0000_0001, "LDMD ingress");
    // md_flood with ingress 1, n_ports 4: 0b1101.
    execute(LDMD(R2, 0x9));
    assert(read_reg(R2) == 0x0000_0000_0000_000D, "LDMD flood mask excludes ingress");
    // hdr_present bitmap: only h2 set -> bit 2.
    execute(LDMD(R3, 0xA));
    assert(read_reg(R3) == 0x0000_0000_0000_0004, "LDMD hdr_present bitmap");
    // Reserved fields are defined zero.
    write_reg(R0, 0xFFFF_FFFF_FFFF_FFFF);
    execute(LDMD(R0, 0xE));
    assert(read_reg(R0) == sail_zeros(64), "LDMD reserved field reads zero");
    print_endline("test_ldmd passed")
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

val main : unit -> unit
function main() = {
    test_st_ld_roundtrip();
    test_addressing_totality();
    test_ldmd();
    test_movi_addi();
    print_endline("test_map_exec_mem: all assertions passed")
}
