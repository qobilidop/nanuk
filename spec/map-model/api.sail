// C-facing harness API. Every signature uses machine-word-sized types only,
// so the generated C functions take/return plain uint64_t — the emulator CLI
// drives the model without touching Sail runtime internals.
// (emu_map_run lives in exec.sail with the step loop.)

val emu_map_reset : unit -> unit
function emu_map_reset() = reset_map_state()

val emu_map_poke_imem : (bits16, bits32) -> unit
function emu_map_poke_imem(idx, w) = write_imem(unsigned(idx), w)

// Poke a frame byte (frame offset, not window index; headroom bias applied).
val emu_map_poke_pkt : (bits16, bits8) -> unit
function emu_map_poke_pkt(idx, b) = write_win_byte(headroom_bytes + unsigned(idx), b)

val emu_map_set_plen : bits16 -> unit
function emu_map_set_plen(l) = plen = l

val emu_map_set_ingress : bits8 -> unit
function emu_map_set_ingress(p) = ingress = p

val emu_map_set_smd : (bits8, bits16) -> unit
function emu_map_set_smd(s, v) = {
    let i = unsigned(s);
    assert(0 <= i & i < 8, "SMD slot index out of bounds");
    smd_in[i] = v
}

// Mark header h present at frame offset off (present = 0 clears).
val emu_map_set_hdr : (bits8, bits8, bits16) -> unit
function emu_map_set_hdr(h, present, off) = {
    let i = unsigned(h);
    assert(0 <= i & i < 16, "header id out of bounds");
    hdr_present_in[i] = (present != 0x00);
    hdr_offset_in[i] = off
}

// Control-plane table API. Config sets key/action widths and clears entries;
// add appends one entry (key and action masked to their configured widths).
val emu_map_table_config : (bits8, bits8, bits8) -> unit
function emu_map_table_config(t, kw, aw) = {
    let ti = unsigned(t);
    assert(0 <= ti & ti < 4, "table id out of bounds");
    tbl_keywidth[ti] = kw;
    tbl_actwidth[ti] = aw;
    tbl_nentries[ti] = 0x00
}

val width_mask : bits8 -> bits64
function width_mask(w) = {
    let wi = unsigned(w);
    let wc = if wi > 64 then 64 else wi;
    assert(0 <= wc & wc <= 64, "width out of range");
    if wc == 0 then sail_zeros(64) else sail_mask(64, sail_ones(wc))
}

val emu_map_table_add : (bits8, bits64, bits64) -> unit
function emu_map_table_add(t, key, action) = {
    let ti = unsigned(t);
    assert(0 <= ti & ti < 4, "table id out of bounds");
    let ne = unsigned(tbl_nentries[ti]);
    assert(0 <= ne & ne < 64, "table is full");
    tbl_keys[ti][ne] = key & width_mask(tbl_keywidth[ti]);
    tbl_actions[ti][ne] = action & width_mask(tbl_actwidth[ti]);
    tbl_nentries[ti] = get_slice_int(8, ne + 1, 0)
}

val emu_map_get_verdict : unit -> bits8
function emu_map_get_verdict() = verdict

val emu_map_get_error : unit -> bits8
function emu_map_get_error() = err

val emu_map_get_egress : unit -> bits8
function emu_map_get_egress() = egress

val emu_map_get_steps : unit -> bits32
function emu_map_get_steps() = get_slice_int(32, steps, 0)

// Send delta as 16-bit two's complement (the C shim sign-extends).
val emu_map_get_delta : unit -> bits16
function emu_map_get_delta() = get_slice_int(16, send_delta, 0)

// Window byte readback (window index, headroom included) for frame assembly.
val emu_map_get_win_byte : bits16 -> bits8
function emu_map_get_win_byte(idx) = read_win_byte(unsigned(idx))
