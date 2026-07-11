// Tests for machine state, reset, and packet bit reads.

val test_reset_zeroes_state : unit -> unit
function test_reset_zeroes_state() = {
    emu_reset();
    // Dirty some state, reset again, verify clean.
    emu_poke_pkt(0x0003, 0xAB);
    emu_poke_imem(0x0002, 0xDEADBEEF);
    emu_set_plen(0x0040);
    write_reg(R1, 0x00000000_000000FF);
    set_hdr(5);
    write_smd_slot(2, 0x1234);
    emu_reset();
    assert(read_reg(R1) == sail_zeros(64), "reset clears registers");
    assert(read_pkt_byte(3) == 0x00, "reset clears packet buffer");
    assert(read_imem(2) == 0x00000000, "reset clears imem");
    assert(unsigned(plen) == 0, "reset clears plen");
    assert(emu_get_hdr_present(0x05) == 0x00, "reset clears hdr_present");
    assert(emu_get_smd(0x02) == 0x0000, "reset clears smd");
    assert(emu_get_verdict() == verdict_accept, "reset clears verdict");
    assert(emu_get_error() == err_none, "reset clears error")
}

val test_poke_roundtrip : unit -> unit
function test_poke_roundtrip() = {
    emu_reset();
    emu_poke_pkt(0x0000, 0xAB);
    emu_poke_pkt(0x0001, 0xCD);
    emu_poke_imem(0x0007, 0x10000007);
    assert(read_pkt_byte(0) == 0xAB, "pkt poke byte 0");
    assert(read_pkt_byte(1) == 0xCD, "pkt poke byte 1");
    assert(read_imem(7) == 0x10000007, "imem poke word 7")
}

val test_rz_semantics : unit -> unit
function test_rz_semantics() = {
    emu_reset();
    write_reg(RZ, 0xFFFFFFFF_FFFFFFFF);
    assert(read_reg(RZ) == sail_zeros(64), "RZ reads zero after write")
}

val test_read_pkt_bits : unit -> unit
function test_read_pkt_bits() = {
    emu_reset();
    emu_poke_pkt(0x0000, 0xAB);
    emu_poke_pkt(0x0001, 0xCD);
    emu_poke_pkt(0x0002, 0xEF);
    // Whole first byte
    assert(read_pkt_bits(0, 8) == 0x00000000_000000AB, "bits 0..7 = 0xAB");
    // 16 bits across two bytes
    assert(read_pkt_bits(0, 16) == 0x00000000_0000ABCD, "bits 0..15 = 0xABCD");
    // Sub-byte: high nibble of byte 0 (bit 0 = MSB)
    assert(read_pkt_bits(0, 4) == 0x00000000_0000000A, "bits 0..3 = 0xA");
    // Sub-byte: low nibble of byte 0
    assert(read_pkt_bits(4, 4) == 0x00000000_0000000B, "bits 4..7 = 0xB");
    // Byte-boundary crossing: bits 4..11 = 0xBC
    assert(read_pkt_bits(4, 8) == 0x00000000_000000BC, "bits 4..11 = 0xBC");
    // 24 bits
    assert(read_pkt_bits(0, 24) == 0x00000000_00ABCDEF, "bits 0..23")
}

val test_hdr_limit_clamps : unit -> unit
function test_hdr_limit_clamps() = {
    emu_reset();
    emu_set_plen(0x0040);
    assert(hdr_limit() == 64, "hdr_limit is plen when short");
    emu_set_plen(0x0400);  // 1024 > 256
    assert(hdr_limit() == 256, "hdr_limit clamps at buffer size")
}

val test_set_hdr_records_cursor : unit -> unit
function test_set_hdr_records_cursor() = {
    emu_reset();
    cursor = 0x000E;
    set_hdr(3);
    assert(emu_get_hdr_present(0x03) == 0x01, "hdr 3 present");
    assert(emu_get_hdr_offset(0x03) == 0x000E, "hdr 3 offset = cursor")
}

val main : unit -> unit
function main() = {
    test_reset_zeroes_state();
    test_poke_roundtrip();
    test_rz_semantics();
    test_read_pkt_bits();
    test_hdr_limit_clamps();
    test_set_hdr_records_cursor();
    print_endline("test_state: OK")
}
