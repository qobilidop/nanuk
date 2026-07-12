// Machine state, reset, and access helpers.

// Verdicts (delivered in the output contract).
let verdict_sent : bits8 = 0x00
let verdict_drop : bits8 = 0x01
let verdict_error : bits8 = 0x02

// Error codes (total semantics: every abnormal path lands on one of these).
let err_none : bits8 = 0x00
let err_window_violation : bits8 = 0x01
let err_step_budget : bits8 = 0x02
let err_illegal : bits8 = 0x03
let err_pc_range : bits8 = 0x04
let err_hdr_absent : bits8 = 0x05
let err_send_range : bits8 = 0x06

// The h_frame header base id: always valid, base = frame start.
let h_frame : int = 15

// General-purpose registers r0-r3 (RZ reads zero, discards writes).
register regs : vector(4, bits64)

// Packet length in bytes (set by the harness; the frame as the PP saw it).
register plen : bits16

// The window: headroom (writable prepend region) followed by the frame.
// Window index = headroom_bytes + frame offset.
register window : vector(288, bits8)

// Inbound contract from the PP (set by the harness before a run).
register hdr_present_in : vector(16, bool)
register hdr_offset_in : vector(16, bits16)
register smd_in : vector(8, bits16)
register ingress : bits8

// Exact-match tables (control-plane state, set via the emu_map_table_* API).
register tbl_keywidth : vector(4, bits8)
register tbl_actwidth : vector(4, bits8)
register tbl_nentries : vector(4, bits8)
register tbl_keys : vector(4, vector(64, bits64))
register tbl_actions : vector(4, vector(64, bits64))

// Program counter (word index into instruction memory) and instruction memory.
register pc : bits16
register imem : vector(1024, bits32)

// Run status and outbound contract.
register halted : bool
register verdict : bits8
register err : bits8
register steps : int
register egress : bits8
register send_delta : int

// Convert a (bounds-checked) int to bits16.
val to_bits16 : int -> bits16
function to_bits16(i) = get_slice_int(16, i, 0)

// Valid window limit: headroom plus the frame bytes that actually exist.
val win_limit : unit -> int
function win_limit() =
    if unsigned(plen) < buf_bytes then headroom_bytes + unsigned(plen)
    else headroom_bytes + buf_bytes

// Enter the error-halt state. Outputs are still delivered (partial state).
val raise_err : bits8 -> unit
function raise_err(code) = {
    verdict = verdict_error;
    err = code;
    halted = true
}

// Register file access. RZ reads as zero; writes to RZ are discarded.
val read_reg : regidx -> bits64
function read_reg(r) = match r {
    R0 => regs[0],
    R1 => regs[1],
    R2 => regs[2],
    R3 => regs[3],
    RZ => sail_zeros(64),
}

val write_reg : (regidx, bits64) -> unit
function write_reg(r, v) = match r {
    R0 => regs[0] = v,
    R1 => regs[1] = v,
    R2 => regs[2] = v,
    R3 => regs[3] = v,
    RZ => (),
}

// Bounds-asserted array accessors (asserts are for harness/model internal
// errors; ISA-level errors go through raise_err).
val read_win_byte : int -> bits8
function read_win_byte(idx) = {
    assert(0 <= idx & idx < 288, "window byte index out of bounds");
    window[idx]
}

val write_win_byte : (int, bits8) -> unit
function write_win_byte(idx, b) = {
    assert(0 <= idx & idx < 288, "window byte index out of bounds");
    window[idx] = b
}

val read_imem : int -> bits32
function read_imem(idx) = {
    assert(0 <= idx & idx < 1024, "imem index out of bounds");
    imem[idx]
}

val write_imem : (int, bits32) -> unit
function write_imem(idx, w) = {
    assert(0 <= idx & idx < 1024, "imem index out of bounds");
    imem[idx] = w
}

// Header base for addressing: h_frame (15) is always the frame start;
// ids 0..14 resolve through the PP's outputs and are -1 when absent
// (the caller raises err_hdr_absent).
val hdr_base : int -> int
function hdr_base(h) = {
    assert(0 <= h & h < 16, "header id out of bounds");
    if h == 15 then 0
    else if hdr_present_in[h] then unsigned(hdr_offset_in[h])
    else negate(1)
}

val init_vec_bits8_288 : unit -> vector(288, bits8)
function init_vec_bits8_288() = {
    var v : vector(288, bits8) = undefined;
    foreach (i from 0 to 287) { v[i] = 0x00 };
    v
}

val init_vec_bits32_1024 : unit -> vector(1024, bits32)
function init_vec_bits32_1024() = {
    var v : vector(1024, bits32) = undefined;
    foreach (i from 0 to 1023) { v[i] = 0x00000000 };
    v
}

val init_vec_bool_16 : unit -> vector(16, bool)
function init_vec_bool_16() = {
    var v : vector(16, bool) = undefined;
    foreach (i from 0 to 15) { v[i] = false };
    v
}

val init_vec_bits16_16 : unit -> vector(16, bits16)
function init_vec_bits16_16() = {
    var v : vector(16, bits16) = undefined;
    foreach (i from 0 to 15) { v[i] = 0x0000 };
    v
}

val init_vec_bits16_8 : unit -> vector(8, bits16)
function init_vec_bits16_8() = {
    var v : vector(8, bits16) = undefined;
    foreach (i from 0 to 7) { v[i] = 0x0000 };
    v
}

val init_vec_bits8_4 : unit -> vector(4, bits8)
function init_vec_bits8_4() = {
    var v : vector(4, bits8) = undefined;
    foreach (i from 0 to 3) { v[i] = 0x00 };
    v
}

val init_vec_bits64_64 : unit -> vector(64, bits64)
function init_vec_bits64_64() = {
    var v : vector(64, bits64) = undefined;
    foreach (i from 0 to 63) { v[i] = sail_zeros(64) };
    v
}

val init_tables : unit -> vector(4, vector(64, bits64))
function init_tables() = {
    var v : vector(4, vector(64, bits64)) = undefined;
    foreach (i from 0 to 3) { v[i] = init_vec_bits64_64() };
    v
}

// Reset all machine state (harness API; called before each packet).
val reset_map_state : unit -> unit
function reset_map_state() = {
    regs[0] = sail_zeros(64);
    regs[1] = sail_zeros(64);
    regs[2] = sail_zeros(64);
    regs[3] = sail_zeros(64);
    plen = 0x0000;
    window = init_vec_bits8_288();
    hdr_present_in = init_vec_bool_16();
    hdr_offset_in = init_vec_bits16_16();
    smd_in = init_vec_bits16_8();
    ingress = 0x00;
    tbl_keywidth = init_vec_bits8_4();
    tbl_actwidth = init_vec_bits8_4();
    tbl_nentries = init_vec_bits8_4();
    tbl_keys = init_tables();
    tbl_actions = init_tables();
    pc = 0x0000;
    imem = init_vec_bits32_1024();
    halted = false;
    verdict = verdict_sent;
    err = err_none;
    steps = 0;
    egress = 0x00;
    send_delta = 0
}
