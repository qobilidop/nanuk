// Machine state, reset, and access helpers.

// Verdicts (delivered in the output contract).
let verdict_accept : bits8 = 0x00
let verdict_drop : bits8 = 0x01
let verdict_error : bits8 = 0x02

// Error codes (total semantics: every abnormal path lands on one of these).
let err_none : bits8 = 0x00
let err_hdr_violation : bits8 = 0x01
let err_step_budget : bits8 = 0x02
let err_illegal : bits8 = 0x03
let err_pc_range : bits8 = 0x04
let err_md_range : bits8 = 0x05

// General-purpose registers r0-r3 (RZ reads zero, discards writes).
register regs : vector(4, bits64)

// Cursor: byte position in the packet header buffer. Monotonic.
register cursor : bits16

// Packet length in bytes (set by the harness before a run).
register plen : bits16

// Packet header buffer.
register pkt : vector(256, bits8)

// Program counter (word index into instruction memory) and instruction memory.
register pc : bits16
register imem : vector(1024, bits32)

// Header metadata outputs.
register hdr_present : vector(16, bool)
register hdr_offset : vector(16, bits16)

// The metadata window: 8 slots x 16 bits — harness-initialized
// (pass-through default), LDMD-read, STMD-written, delivered downstream.
register md : vector(8, bits16)

// Run status.
register halted : bool
register verdict : bits8
register err : bits8
register steps : int

// Convert a (bounds-checked) int to bits16.
val to_bits16 : int -> bits16
function to_bits16(i) = get_slice_int(16, i, 0)

// Header parse boundary: reads/advances beyond this are violations.
val hdr_limit : unit -> int
function hdr_limit() =
    if unsigned(plen) < buf_bytes then unsigned(plen) else buf_bytes

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
val read_pkt_byte : int -> bits8
function read_pkt_byte(idx) = {
    assert(0 <= idx & idx < 256, "packet byte index out of bounds");
    pkt[idx]
}

val write_pkt_byte : (int, bits8) -> unit
function write_pkt_byte(idx, b) = {
    assert(0 <= idx & idx < 256, "packet byte index out of bounds");
    pkt[idx] = b
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

val write_md_slot : (int, bits16) -> unit
function write_md_slot(idx, v) = {
    assert(0 <= idx & idx < 8, "md slot index out of bounds");
    md[idx] = v
}

val set_hdr : int -> unit
function set_hdr(h) = {
    assert(0 <= h & h < 16, "header id out of bounds");
    hdr_present[h] = true;
    hdr_offset[h] = cursor
}

// Read `sz` bits (1..64) of the packet starting at absolute bit position
// `pos`. Bit 0 of a byte is its MSB (network order). The caller must have
// bounds-checked pos + sz against hdr_limit() * 8.
val read_pkt_bits : (int, int) -> bits64
function read_pkt_bits(pos, sz) = {
    assert(0 <= pos & 1 <= sz & sz <= 64, "read_pkt_bits argument range");

    // Byte index and bit-within-byte via shift/mask (avoids int division).
    let pos_bv : bits(20) = get_slice_int(20, pos, 0);
    let start_byte = unsigned(sail_shiftright(pos_bv, 3));
    let bit_in_byte = unsigned(pos_bv & 0x00007);

    // bytes_needed = ceil((bit_in_byte + sz) / 8) <= 9
    let sum_bv : bits(20) = get_slice_int(20, bit_in_byte + sz + 7, 0);
    let bytes_needed = unsigned(sail_shiftright(sum_bv, 3));

    // Accumulate bytes big-endian into 72 bits (9 bytes max), then align.
    var acc : bits(72) = sail_zeros(72);
    var i : int = 0;
    while i < bytes_needed do {
        let b : bits(72) = sail_zero_extend(read_pkt_byte(start_byte + i), 72);
        acc = acc | sail_shiftleft(b, 8 * (bytes_needed - 1 - i));
        i = i + 1
    };
    let shift_amount : int = bytes_needed * 8 - bit_in_byte - sz;
    let aligned : bits(72) = sail_shiftright(acc, shift_amount) & sail_mask(72, sail_ones(sz));
    aligned[63 .. 0]
}

val init_vec_bits8_256 : unit -> vector(256, bits8)
function init_vec_bits8_256() = {
    var v : vector(256, bits8) = undefined;
    foreach (i from 0 to 255) { v[i] = 0x00 };
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

// Reset all machine state (harness API; called before each packet).
val reset_state : unit -> unit
function reset_state() = {
    regs[0] = sail_zeros(64);
    regs[1] = sail_zeros(64);
    regs[2] = sail_zeros(64);
    regs[3] = sail_zeros(64);
    cursor = 0x0000;
    plen = 0x0000;
    pkt = init_vec_bits8_256();
    pc = 0x0000;
    imem = init_vec_bits32_1024();
    hdr_present = init_vec_bool_16();
    hdr_offset = init_vec_bits16_16();
    md = init_vec_bits16_8();
    halted = false;
    verdict = verdict_accept;
    err = err_none;
    steps = 0
}
