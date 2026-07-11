// Control flow, SEND/DROP termination, and step-loop totality, driven
// through the real fetch-decode-execute loop (emu_map_run).

val fresh : int -> unit
function fresh(pl) = {
    reset_map_state();
    plen = to_bits16(pl);
    ingress = 0x00
}

val test_branches : unit -> unit
function test_branches() = {
    fresh(64);
    // 0: MOVI r0, 5
    // 1: MOVI r1, 5
    // 2: BEQ r0, r1, 4     (taken)
    // 3: DROP              (skipped)
    // 4: BNE r0, r1, 6     (not taken)
    // 5: SEND rz, 0        (terminates, empty egress)
    // 6: DROP
    write_imem(0, encode(MOVI(R0, 0x0005)));
    write_imem(1, encode(MOVI(R1, 0x0005)));
    write_imem(2, encode(BEQ(R0, R1, 0x0004)));
    write_imem(3, encode(DROP()));
    write_imem(4, encode(BNE(R0, R1, 0x0006)));
    write_imem(5, encode(SEND(RZ, 0b0000000000)));
    write_imem(6, encode(DROP()));
    emu_map_run();
    assert(verdict == verdict_sent, "branch path reaches SEND");
    assert(err == err_none, "no error on branch path");
    assert(steps == 5, "took 5 steps (skipped word 3)");
    print_endline("test_branches passed")
}

val test_send_semantics : unit -> unit
function test_send_semantics() = {
    fresh(64);
    // Egress bitmap masked to n_ports; delta recorded.
    write_imem(0, encode(MOVI(R0, 0x00FF)));
    write_imem(1, encode(SEND(R0, 0b0000010110)));   // delta 22
    emu_map_run();
    assert(verdict == verdict_sent, "SEND terminates sent");
    assert(egress == 0x0F, "egress masked to 4 ports");
    assert(send_delta == 22, "positive delta recorded");
    // Negative delta (strip).
    fresh(64);
    write_imem(0, encode(SEND(RZ, 0b1111101010)));   // delta -22
    emu_map_run();
    assert(verdict == verdict_sent & send_delta == negate(22), "negative delta recorded");
    print_endline("test_send_semantics passed")
}

val test_send_range : unit -> unit
function test_send_range() = {
    // delta = headroom_bytes (32) is legal.
    fresh(64);
    write_imem(0, encode(SEND(RZ, 0b0000100000)));
    emu_map_run();
    assert(verdict == verdict_sent, "delta = headroom is legal");
    // delta = 33 is out of range.
    fresh(64);
    write_imem(0, encode(SEND(RZ, 0b0000100001)));
    emu_map_run();
    assert(verdict == verdict_error & err == err_send_range, "delta past headroom errors");
    // delta = -plen strips the whole frame: error.
    fresh(64);
    write_imem(0, encode(SEND(RZ, 0b1111000000)));   // -64
    emu_map_run();
    assert(verdict == verdict_error & err == err_send_range, "delta = -plen errors");
    // delta = -(plen-1) leaves one byte: legal.
    fresh(64);
    write_imem(0, encode(SEND(RZ, 0b1111000001)));   // -63
    emu_map_run();
    assert(verdict == verdict_sent & send_delta == negate(63), "delta = -(plen-1) legal");
    print_endline("test_send_range passed")
}

val test_totality : unit -> unit
function test_totality() = {
    // Step budget: JMP 0 spins until the watchdog fires.
    fresh(64);
    write_imem(0, encode(JMP(0x0000)));
    emu_map_run();
    assert(verdict == verdict_error & err == err_step_budget, "infinite loop hits step budget");
    assert(steps == step_budget, "watchdog fired exactly at the budget");
    // All-zeros imem: first fetch is ILLEGAL.
    fresh(64);
    emu_map_run();
    assert(verdict == verdict_error & err == err_illegal, "all-zeros word is illegal");
    assert(steps == 1, "illegal detected on first step");
    // pc past imem: a branch target beyond imem_words.
    fresh(64);
    write_imem(0, encode(JMP(0xFFFF)));
    emu_map_run();
    assert(verdict == verdict_error & err == err_pc_range, "pc past imem errors");
    print_endline("test_totality passed")
}

val main : unit -> unit
function main() = {
    test_branches();
    test_send_semantics();
    test_send_range();
    test_totality();
    print_endline("test_map_exec_control: all assertions passed")
}
