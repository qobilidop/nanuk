// Control flow, SETHDR/STMD/HALT, and the run loop with its watchdog.

val test_branches : unit -> unit
function test_branches() = {
    emu_reset();
    write_reg(R0, 0x00000000_00000005);
    write_reg(R1, 0x00000000_00000005);
    write_reg(R2, 0x00000000_00000007);
    pc = 0x0001;
    execute(BEQ(R0, R1, 0x0042));
    assert(unsigned(pc) == 66, "BEQ taken sets pc");
    pc = 0x0001;
    execute(BEQ(R0, R2, 0x0042));
    assert(unsigned(pc) == 1, "BEQ not taken leaves pc");
    pc = 0x0001;
    execute(BNE(R0, R2, 0x0042));
    assert(unsigned(pc) == 66, "BNE taken sets pc");
    pc = 0x0001;
    execute(JMP(0x0099));
    assert(unsigned(pc) == 153, "JMP sets pc")
}

val test_stmd_multislot : unit -> unit
function test_stmd_multislot() = {
    emu_reset();
    // 48-bit DMAC-like value into slots 0..2, MSB-first.
    write_reg(R0, 0x00000102_03040506);
    execute(STMD(R0, 0b10, 0b0000));  // 3 units at slot 0
    assert(emu_get_smd(0x00) == 0x0102, "slot 0 = bits[47:32]");
    assert(emu_get_smd(0x01) == 0x0304, "slot 1 = bits[31:16]");
    assert(emu_get_smd(0x02) == 0x0506, "slot 2 = bits[15:0]");
    // Single slot elsewhere.
    write_reg(R1, 0x00000000_0000BEEF);
    execute(STMD(R1, 0b00, 0b0100));
    assert(emu_get_smd(0x04) == 0xBEEF, "single-unit STMD")
}

val test_stmd_range_error : unit -> unit
function test_stmd_range_error() = {
    emu_reset();
    execute(STMD(R0, 0b01, 0b0111));  // 2 units at slot 7 -> slots 7,8: range
    assert(halted, "STMD out of range halts");
    assert(emu_get_verdict() == verdict_error, "STMD range verdict");
    assert(emu_get_error() == err_smd_range, "STMD range error code")
}

val test_halt_verdicts : unit -> unit
function test_halt_verdicts() = {
    emu_reset();
    cursor = 0x0022;
    execute(HALT(false));
    assert(halted, "HALT halts");
    assert(emu_get_verdict() == verdict_accept, "HALT accept verdict");
    assert(emu_get_cursor() == 0x0022, "payload offset = cursor at halt");
    emu_reset();
    execute(HALT(true));
    assert(emu_get_verdict() == verdict_drop, "HALT drop verdict")
}

// Full program through the run loop: MOVI + BEQ (taken) + HALT accept.
val test_run_loop_program : unit -> unit
function test_run_loop_program() = {
    emu_reset();
    emu_poke_imem(0x0000, encode(MOVI(R0, 0x0007)));
    emu_poke_imem(0x0001, encode(MOVI(R1, 0x0007)));
    emu_poke_imem(0x0002, encode(BEQ(R0, R1, 0x0004)));
    emu_poke_imem(0x0003, encode(HALT(true)));    // skipped if branch taken
    emu_poke_imem(0x0004, encode(HALT(false)));
    emu_run();
    assert(halted, "program halts");
    assert(emu_get_verdict() == verdict_accept, "branch skipped the drop");
    assert(unsigned(emu_get_steps()) == 4, "4 instructions executed")
}

// Backward-branch infinite loop is terminated by the step budget.
val test_step_budget_watchdog : unit -> unit
function test_step_budget_watchdog() = {
    emu_reset();
    emu_poke_imem(0x0000, encode(JMP(0x0000)));
    emu_run();
    assert(halted, "watchdog halts infinite loop");
    assert(emu_get_verdict() == verdict_error, "watchdog verdict is error");
    assert(emu_get_error() == err_step_budget, "watchdog error code");
    assert(unsigned(emu_get_steps()) == 256, "exactly step_budget steps ran")
}

// The all-zeros word (default imem content) is an illegal instruction.
val test_illegal_instruction_halt : unit -> unit
function test_illegal_instruction_halt() = {
    emu_reset();
    // pc=0 holds 0x00000000 after reset.
    emu_run();
    assert(halted, "illegal instruction halts");
    assert(emu_get_verdict() == verdict_error, "illegal verdict is error");
    assert(emu_get_error() == err_illegal, "illegal error code");
    assert(unsigned(emu_get_steps()) == 1, "halted on first instruction")
}

// SETHDR through the run loop, recording distinct cursor positions.
val test_sethdr_program : unit -> unit
function test_sethdr_program() = {
    emu_reset();
    emu_set_plen(0x0020);
    emu_poke_imem(0x0000, encode(SETHDR(0x0)));
    emu_poke_imem(0x0001, encode(ADVI(0x000E)));
    emu_poke_imem(0x0002, encode(SETHDR(0x2)));
    emu_poke_imem(0x0003, encode(HALT(false)));
    emu_run();
    assert(emu_get_hdr_present(0x00) == 0x01, "hdr 0 present");
    assert(emu_get_hdr_offset(0x00) == 0x0000, "hdr 0 at offset 0");
    assert(emu_get_hdr_present(0x02) == 0x01, "hdr 2 present");
    assert(emu_get_hdr_offset(0x02) == 0x000E, "hdr 2 at offset 14");
    assert(emu_get_hdr_present(0x01) == 0x00, "hdr 1 untouched")
}

val main : unit -> unit
function main() = {
    test_branches();
    test_stmd_multislot();
    test_stmd_range_error();
    test_halt_verdicts();
    test_run_loop_program();
    test_step_budget_watchdog();
    test_illegal_instruction_halt();
    test_sethdr_program();
    print_endline("test_exec_control: OK")
}
