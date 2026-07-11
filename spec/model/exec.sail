// Fetch-decode-execute step and run loop.

// One machine step. Order of checks: step budget, then pc range, then
// decode (illegal), then execute. Every path is total.
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
val emu_run : unit -> unit
function emu_run() = {
    while not_bool(halted) do step()
}
