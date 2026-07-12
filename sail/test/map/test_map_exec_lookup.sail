// LOOKUP semantics: hit falls through with action data; miss branches with
// rd = 0; keys mask to the table's key width; empty/unconfigured/out-of-plane
// tables always miss.

// Configure table 0 as a 48-bit-key, 8-bit-action table with two entries.
val setup_l2_table : unit -> unit
function setup_l2_table() = {
    reset_map_state();
    plen = 0x0040;
    pc = 0x0000;
    tbl_keywidth[0] = 0x30;   // 48
    tbl_actwidth[0] = 0x08;
    tbl_nentries[0] = 0x02;
    tbl_keys[0][0] = 0x0000_02DE_ADBE_EF01;
    tbl_actions[0][0] = 0x0000_0000_0000_0004;  // port bitmap 0b0100
    tbl_keys[0][1] = 0x0000_0A0B_0C0D_0E0F;
    tbl_actions[0][1] = 0x0000_0000_0000_0008   // port bitmap 0b1000
}

val test_hit_falls_through : unit -> unit
function test_hit_falls_through() = {
    setup_l2_table();
    pc = 0x0005;
    write_reg(R0, 0x0000_02DE_ADBE_EF01);
    execute(LOOKUP(R1, 0x0, R0, 0x0020));
    assert(read_reg(R1) == 0x0000_0000_0000_0004, "hit loads action data");
    assert(pc == 0x0005, "hit falls through (pc untouched by execute)");
    assert(not_bool(halted), "hit does not halt");
    print_endline("test_hit_falls_through passed")
}

val test_miss_branches : unit -> unit
function test_miss_branches() = {
    setup_l2_table();
    pc = 0x0005;
    write_reg(R0, 0x0000_1111_2222_3333);
    write_reg(R1, 0xFFFF_FFFF_FFFF_FFFF);
    execute(LOOKUP(R1, 0x0, R0, 0x0020));
    assert(read_reg(R1) == sail_zeros(64), "miss zeroes rd");
    assert(pc == 0x0020, "miss branches to target");
    print_endline("test_miss_branches passed")
}

val test_key_masking : unit -> unit
function test_key_masking() = {
    setup_l2_table();
    // Garbage above the 48-bit key width must not affect the match.
    write_reg(R2, 0xBEEF_02DE_ADBE_EF01);
    execute(LOOKUP(R3, 0x0, R2, 0x0020));
    assert(read_reg(R3) == 0x0000_0000_0000_0004, "key masked to key width still hits");
    print_endline("test_key_masking passed")
}

val test_always_miss_tables : unit -> unit
function test_always_miss_tables() = {
    setup_l2_table();
    // Table 1 exists but is unconfigured (key width 0).
    pc = 0x0000;
    write_reg(R0, sail_zeros(64));
    execute(LOOKUP(R1, 0x1, R0, 0x0011));
    assert(pc == 0x0011, "unconfigured table always misses");
    // Table 2 configured but empty.
    tbl_keywidth[2] = 0x30;
    tbl_actwidth[2] = 0x08;
    pc = 0x0000;
    execute(LOOKUP(R1, 0x2, R0, 0x0012));
    assert(pc == 0x0012, "empty table always misses");
    // Table id beyond n_tables (0xC) is defined as always-miss.
    pc = 0x0000;
    execute(LOOKUP(R1, 0xC, R0, 0x0013));
    assert(pc == 0x0013, "out-of-plane table id always misses");
    assert(not_bool(halted), "out-of-plane lookup is not an error");
    print_endline("test_always_miss_tables passed")
}

val main : unit -> unit
function main() = {
    test_hit_falls_through();
    test_miss_branches();
    test_key_masking();
    test_always_miss_tables();
    print_endline("test_map_exec_lookup: all assertions passed")
}
