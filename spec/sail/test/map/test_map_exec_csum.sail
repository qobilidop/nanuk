// CSUM: generic RFC 1071 ones-complement checksum of an explicit window
// range into a register — no IHL parsing, no skipped bytes, no write-back.
// Golden value from the classic worked example (192.168.0.1 -> 192.168.0.199,
// UDP, len 0x73): header 4500 0073 0000 4000 4011 ---- c0a8 0001 c0a8 00c7,
// checksum b861 (computed over the header with the checksum field zeroed).

val put_frame_byte : (int, bits8) -> unit
function put_frame_byte(off, b) = write_win_byte(headroom_bytes + off, b)

// Write the 20-byte example header at frame offset 14 with the checksum
// field set to `ck_hi`/`ck_lo` (so tests can start from zero or from garbage).
val setup_ipv4 : (bits8, bits8) -> unit
function setup_ipv4(ck_hi, ck_lo) = {
    reset_map_state();
    plen = 0x0040;
    hdr_present_in[2] = true;
    hdr_offset_in[2] = 0x000E;
    put_frame_byte(14, 0x45); put_frame_byte(15, 0x00);
    put_frame_byte(16, 0x00); put_frame_byte(17, 0x73);
    put_frame_byte(18, 0x00); put_frame_byte(19, 0x00);
    put_frame_byte(20, 0x40); put_frame_byte(21, 0x00);
    put_frame_byte(22, 0x40); put_frame_byte(23, 0x11);
    put_frame_byte(24, ck_hi); put_frame_byte(25, ck_lo);
    put_frame_byte(26, 0xC0); put_frame_byte(27, 0xA8);
    put_frame_byte(28, 0x00); put_frame_byte(29, 0x01);
    put_frame_byte(30, 0xC0); put_frame_byte(31, 0xA8);
    put_frame_byte(32, 0x00); put_frame_byte(33, 0xC7)
}

val test_csum_golden : unit -> unit
function test_csum_golden() = {
    // Over the header with a zeroed checksum field: the classic result.
    setup_ipv4(0x00, 0x00);
    write_reg(R2, sail_zero_extend(0x0014, 64));   // length 20
    execute(CSUM(R1, 0x2, 0b0000000000, R2));
    assert(not_bool(halted), "CSUM in-window does not halt");
    assert(read_reg(R1) == sail_zero_extend(0xB861, 64), "golden checksum in rd");
    // No write-back: the window's checksum field is untouched.
    assert(read_win_byte(headroom_bytes + 24) == 0x00, "no write-back, hi");
    assert(read_win_byte(headroom_bytes + 25) == 0x00, "no write-back, lo");
    // The field is NOT skipped: garbage there changes the sum (the program
    // is responsible for zeroing before recomputing).
    setup_ipv4(0xDE, 0xAD);
    write_reg(R2, sail_zero_extend(0x0014, 64));
    execute(CSUM(R1, 0x2, 0b0000000000, R2));
    assert(read_reg(R1) != sail_zero_extend(0xB861, 64), "stale field is summed");
    print_endline("test_csum_golden passed")
}

// The full program shape from the redesign spec: TTL decrement, zero the
// field, derive the length from IHL with ANDI/SHLI, CSUM, store the result.
val test_csum_after_ttl_dec : unit -> unit
function test_csum_after_ttl_dec() = {
    // RFC 1624 predicts 0xB861 + 0x0100 = 0xB961 after TTL 0x40 -> 0x3F.
    setup_ipv4(0xB8, 0x61);
    execute(LD(R0, 0x2, 0b0000001000, 0b000));     // TTL byte
    execute(ADDI(R0, R0, 0xFFFF));                 // decrement
    execute(ST(R0, 0x2, 0b0000001000, 0b000));
    execute(ST(RZ, 0x2, 0b0000001010, 0b001));     // zero checksum field
    execute(LD(R2, 0x2, 0b0000000000, 0b000));     // version|IHL byte
    execute(ANDI(R2, R2, 0x000F));                 // IHL
    execute(SHLI(R2, R2, 0b000010));               // header length = IHL * 4
    assert(read_reg(R2) == sail_zero_extend(0x0014, 64), "IHL*4 = 20");
    execute(CSUM(R1, 0x2, 0b0000000000, R2));
    execute(ST(R1, 0x2, 0b0000001010, 0b001));     // store new checksum
    assert(read_win_byte(headroom_bytes + 22) == 0x3F, "TTL decremented");
    assert(read_win_byte(headroom_bytes + 24) == 0xB9, "post-TTL checksum hi");
    assert(read_win_byte(headroom_bytes + 25) == 0x61, "post-TTL checksum lo");
    print_endline("test_csum_after_ttl_dec passed")
}

val test_csum_lengths : unit -> unit
function test_csum_lengths() = {
    // len = 0 sums nothing: ~0 = 0xFFFF.
    setup_ipv4(0x00, 0x00);
    execute(CSUM(R1, 0x2, 0b0000000000, RZ));
    assert(read_reg(R1) == sail_zero_extend(0xFFFF, 64), "len 0 yields 0xFFFF");
    // Odd length: the final byte is high-weighted (RFC 1071).
    // Bytes 45 00 00 -> sum 0x4500, checksum 0xBAFF.
    setup_ipv4(0x00, 0x00);
    write_reg(R2, sail_zero_extend(0x0003, 64));
    execute(CSUM(R1, 0x2, 0b0000000000, R2));
    assert(read_reg(R1) == sail_zero_extend(0xBAFF, 64), "odd length high-weights the tail byte");
    print_endline("test_csum_lengths passed")
}

val test_csum_totality : unit -> unit
function test_csum_totality() = {
    // Range hanging past the frame end (plen 24 < 14 + 20).
    setup_ipv4(0x00, 0x00);
    plen = 0x0018;
    write_reg(R2, sail_zero_extend(0x0014, 64));
    execute(CSUM(R1, 0x2, 0b0000000000, R2));
    assert(halted & err == err_window_violation, "range past frame end errors");
    // Absent header base.
    setup_ipv4(0x00, 0x00);
    write_reg(R2, sail_zero_extend(0x0014, 64));
    execute(CSUM(R1, 0x7, 0b0000000000, R2));
    assert(halted & err == err_hdr_absent, "absent header errors");
    print_endline("test_csum_totality passed")
}

val main : unit -> unit
function main() = {
    test_csum_golden();
    test_csum_after_ttl_dec();
    test_csum_lengths();
    test_csum_totality();
    print_endline("test_map_exec_csum: all assertions passed")
}
