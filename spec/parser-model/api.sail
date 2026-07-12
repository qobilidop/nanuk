// C-facing harness API. Every signature uses machine-word-sized types only,
// so the generated C functions take/return plain uint64_t — the emulator CLI
// drives the model without touching Sail runtime internals.
// (emu_run lives in exec.sail with the step loop.)

val emu_reset : unit -> unit
function emu_reset() = reset_state()

val emu_poke_imem : (bits16, bits32) -> unit
function emu_poke_imem(idx, w) = write_imem(unsigned(idx), w)

val emu_poke_pkt : (bits16, bits8) -> unit
function emu_poke_pkt(idx, b) = write_pkt_byte(unsigned(idx), b)

val emu_set_plen : bits16 -> unit
function emu_set_plen(l) = plen = l

val emu_get_verdict : unit -> bits8
function emu_get_verdict() = verdict

val emu_get_error : unit -> bits8
function emu_get_error() = err

// Payload offset: cursor position at halt.
val emu_get_cursor : unit -> bits16
function emu_get_cursor() = cursor

val emu_get_steps : unit -> bits32
function emu_get_steps() = get_slice_int(32, steps, 0)

val emu_get_hdr_present : bits8 -> bits8
function emu_get_hdr_present(h) = {
    let i = unsigned(h);
    assert(0 <= i & i < 16, "header id out of bounds");
    if hdr_present[i] then 0x01 else 0x00
}

val emu_get_hdr_offset : bits8 -> bits16
function emu_get_hdr_offset(h) = {
    let i = unsigned(h);
    assert(0 <= i & i < 16, "header id out of bounds");
    hdr_offset[i]
}

val emu_get_smd : bits8 -> bits16
function emu_get_smd(s) = {
    let i = unsigned(s);
    assert(0 <= i & i < 8, "SMD slot index out of bounds");
    smd[i]
}
