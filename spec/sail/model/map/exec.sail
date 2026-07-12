// Fetch-decode-execute step and run loop. Same shape as the parser's
// (sail/model/pp/exec.sail): budget check, pc range check, decode, execute.
// Every path is total.

val step : unit -> unit
function step() = {
    if steps >= step_budget then {
        raise_err(err_step_budget)
    } else if unsigned(pc) >= imem_words then {
        raise_err(err_pc_range)
    } else {
        let w = read_imem(unsigned(pc));
        steps = steps + 1;
        pc = to_bits16(unsigned(pc) + 1);
        execute(decode(w))
    }
}

// Run until halt. Termination is guaranteed by the step budget.
val emu_map_run : unit -> unit
function emu_map_run() = {
    while not_bool(halted) do step()
}
