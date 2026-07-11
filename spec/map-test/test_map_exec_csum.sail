// CSUMUPD: recompute the IPv4 header checksum in place. Golden value from
// the classic worked example (192.168.0.1 -> 192.168.0.199, UDP, len 0x73):
// header 4500 0073 0000 4000 4011 ---- c0a8 0001 c0a8 00c7, checksum b861.

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
    // From a zeroed checksum field.
    setup_ipv4(0x00, 0x00);
    execute(CSUMUPD(0x2, 0b0000000000));
    assert(not_bool(halted), "CSUMUPD in-window does not halt");
    assert(read_win_byte(headroom_bytes + 24) == 0xB8, "checksum hi byte");
    assert(read_win_byte(headroom_bytes + 25) == 0x61, "checksum lo byte");
    // From stale garbage: the field is ignored in the sum, result identical.
    setup_ipv4(0xDE, 0xAD);
    execute(CSUMUPD(0x2, 0b0000000000));
    assert(read_win_byte(headroom_bytes + 24) == 0xB8, "stale field ignored, hi");
    assert(read_win_byte(headroom_bytes + 25) == 0x61, "stale field ignored, lo");
    // Idempotent when already correct.
    setup_ipv4(0xB8, 0x61);
    execute(CSUMUPD(0x2, 0b0000000000));
    assert(read_win_byte(headroom_bytes + 24) == 0xB8, "idempotent, hi");
    assert(read_win_byte(headroom_bytes + 25) == 0x61, "idempotent, lo");
    print_endline("test_csum_golden passed")
}

val test_csum_after_ttl_dec : unit -> unit
function test_csum_after_ttl_dec() = {
    // Decrement TTL 0x40 -> 0x3F, recompute: incremental update per RFC 1624
    // predicts checksum + 0x0100 = 0xB961.
    setup_ipv4(0xB8, 0x61);
    execute(LD(R0, 0x2, 0b0000001000, 0b000));
    execute(ADDI(R0, R0, 0xFFFF));
    execute(ST(R0, 0x2, 0b0000001000, 0b000));
    execute(CSUMUPD(0x2, 0b0000000000));
    assert(read_win_byte(headroom_bytes + 22) == 0x3F, "TTL decremented");
    assert(read_win_byte(headroom_bytes + 24) == 0xB9, "post-TTL checksum hi");
    assert(read_win_byte(headroom_bytes + 25) == 0x61, "post-TTL checksum lo");
    print_endline("test_csum_after_ttl_dec passed")
}

val test_csum_totality : unit -> unit
function test_csum_totality() = {
    // IHL < 5 is a window violation.
    setup_ipv4(0x00, 0x00);
    put_frame_byte(14, 0x40);
    execute(CSUMUPD(0x2, 0b0000000000));
    assert(halted & err == err_window_violation, "IHL < 5 errors");
    // Header hanging past the frame end (plen 24 < 14 + 20).
    setup_ipv4(0x00, 0x00);
    plen = 0x0018;
    execute(CSUMUPD(0x2, 0b0000000000));
    assert(halted & err == err_window_violation, "header past frame end errors");
    // Absent header base.
    setup_ipv4(0x00, 0x00);
    execute(CSUMUPD(0x7, 0b0000000000));
    assert(halted & err == err_hdr_absent, "absent header errors");
    print_endline("test_csum_totality passed")
}

val main : unit -> unit
function main() = {
    test_csum_golden();
    test_csum_after_ttl_dec();
    test_csum_totality();
    print_endline("test_map_exec_csum: all assertions passed")
}
