// Execute clauses for the MAP v0 core. Each clause either completes (pc
// already advanced by step()), redirects pc (branches, LOOKUP miss), or
// enters an error/halt state.

let one64 : bits64 = 0x0000000000000001

// Effective window index for hdr-relative addressing, or -1 when the header
// base is absent (caller raises err_hdr_absent). Offsets are signed.
val eff_addr : (bits4, bits10) -> int
function eff_addr(h, off) = {
    let base = hdr_base(unsigned(h));
    if base < 0 then negate(1)
    else headroom_bytes + base + signed(off)
}

val execute : instr -> unit
scattered function execute

// LD rd, hdr, off, nm1: rd = zero-extended (nm1+1) bytes, big-endian, at the
// hdr-relative signed byte offset. Absent header and out-of-window reads are
// defined errors.
function clause execute(LD(rd, h, off, nm1)) = {
    let n = unsigned(nm1) + 1;
    let addr = eff_addr(h, off);
    if addr < 0 & hdr_base(unsigned(h)) < 0 then {
        raise_err(err_hdr_absent)
    } else if addr < 0 | addr + n > win_limit() then {
        raise_err(err_window_violation)
    } else {
        var acc : bits64 = sail_zeros(64);
        var i : int = 0;
        while i < n do {
            acc = sail_shiftleft(acc, 8) | sail_zero_extend(read_win_byte(addr + i), 64);
            i = i + 1
        };
        write_reg(rd, acc)
    }
}

// ST rs, hdr, off, nm1: write the low (nm1+1) bytes of rs big-endian at the
// hdr-relative signed byte offset (negative offsets reach the headroom).
function clause execute(ST(rs, h, off, nm1)) = {
    let n = unsigned(nm1) + 1;
    let addr = eff_addr(h, off);
    if addr < 0 & hdr_base(unsigned(h)) < 0 then {
        raise_err(err_hdr_absent)
    } else if addr < 0 | addr + n > win_limit() then {
        raise_err(err_window_violation)
    } else {
        let v = read_reg(rs);
        var i : int = 0;
        while i < n do {
            let sh = (n - 1 - i) * 8;
            let chunk = sail_shiftright(v, sh);
            write_win_byte(addr + i, chunk[7 .. 0]);
            i = i + 1
        }
    }
}

// LDMD rd, f: rd = md slot f (zero-extended). Slots >= md_slots are ILLEGAL.
function clause execute(LDMD(rd, f)) = {
    let fi = unsigned(f);
    if fi >= 8 then raise_err(err_illegal)
    else {
        assert(0 <= fi & fi < 8, "md slot index out of bounds");
        write_reg(rd, sail_zero_extend(md[fi], 64))
    }
}

// STMD rs, nm1, slot: write (nm1+1) 16-bit units of rs into md, MSB-first.
// slot + n > md_slots is ILLEGAL (the MAP has no SMD-range error code; the
// PP's err_smd_range shape maps to illegal here).
function clause execute(STMD(rs, nm1, slot)) = {
    let n = unsigned(nm1) + 1;
    let s = unsigned(slot);
    if s + n > 8 then raise_err(err_illegal)
    else {
        let v = read_reg(rs);
        var i : int = 0;
        while i < n do {
            let sh = (n - 1 - i) * 16;
            let chunk = sail_shiftright(v, sh);
            let si = s + i;
            assert(0 <= si & si < 8, "md slot index out of bounds");
            md[si] = chunk[15 .. 0];
            i = i + 1
        }
    }
}

// ANDI rd, rs, imm16: rd = rs & zero-extended immediate.
function clause execute(ANDI(rd, rs, imm)) = {
    write_reg(rd, read_reg(rs) & sail_zero_extend(imm, 64))
}

// SHLI rd, rs, sh: rd = rs << sh (64-bit truncating), the PP SHL semantics.
function clause execute(SHLI(rd, rs, sh)) = {
    write_reg(rd, sail_shiftleft(read_reg(rs), unsigned(sh)))
}

// Register-register ALU. Total by construction: every 64-bit pair has a
// result, so none of these can fail. ADD/SUB wrap; no flags, no traps.
function clause execute(ADD(rd, rs, rt)) = {
    write_reg(rd, read_reg(rs) + read_reg(rt))
}

function clause execute(SUB(rd, rs, rt)) = {
    write_reg(rd, sub_bits(read_reg(rs), read_reg(rt)))
}

function clause execute(AND(rd, rs, rt)) = {
    write_reg(rd, read_reg(rs) & read_reg(rt))
}

function clause execute(OR(rd, rs, rt)) = {
    write_reg(rd, read_reg(rs) | read_reg(rt))
}

function clause execute(XOR(rd, rs, rt)) = {
    write_reg(rd, xor_vec(read_reg(rs), read_reg(rt)))
}

// MOVI rd, imm16: rd = zero-extended immediate.
function clause execute(MOVI(rd, imm)) = {
    write_reg(rd, sail_zeros(48) @ imm)
}

// ADDI rd, rs, imm16: rd = rs + sign-extended immediate (64-bit wraparound).
function clause execute(ADDI(rd, rs, imm)) = {
    write_reg(rd, read_reg(rs) + sail_sign_extend(imm, 64))
}

// BEQ/BNE: register-register compare, absolute word target.
function clause execute(BEQ(rs, rt, tgt)) = {
    if read_reg(rs) == read_reg(rt) then pc = tgt
}

function clause execute(BNE(rs, rt, tgt)) = {
    if read_reg(rs) != read_reg(rt) then pc = tgt
}

function clause execute(JMP(tgt)) = {
    pc = tgt
}

// LOOKUP rd, t, rs, tgt: exact-match lookup. Key = rs masked to the table's
// key width. Hit: rd = action data, fall through. Miss (including empty,
// unconfigured, or out-of-plane table ids): rd = 0, branch to tgt.
function clause execute(LOOKUP(rd, t, rs, tgt)) = {
    let ti = unsigned(t);
    var hit : bool = false;
    var act : bits64 = sail_zeros(64);
    // Table ids at or beyond n_tables are defined as always-miss.
    // (Literal 4 rather than n_tables: flow typing needs the literal bound.)
    if ti < 4 then {
        assert(0 <= ti & ti < 4, "table id out of bounds");
        let kw = unsigned(tbl_keywidth[ti]);
        if kw > 0 then {
            let kwc = if kw > 64 then 64 else kw;
            assert(1 <= kwc & kwc <= 64, "key width out of range");
            let kmask = sail_mask(64, sail_ones(kwc));
            let key = read_reg(rs) & kmask;
            let ne = unsigned(tbl_nentries[ti]);
            var i : int = 0;
            while (i < ne) & not_bool(hit) do {
                let ei = i;
                assert(0 <= ei & ei < 64, "table entry index out of bounds");
                if tbl_keys[ti][ei] == key then {
                    hit = true;
                    act = tbl_actions[ti][ei]
                };
                i = i + 1
            }
        }
    };
    if hit then write_reg(rd, act)
    else {
        write_reg(rd, sail_zeros(64));
        pc = tgt
    }
}

// CSUM rd, hdr, off, rl: RFC 1071 ones-complement checksum (end-around-carry
// fold, complemented) of window[base+off .. base+off+len) into rd, where
// len = low 16 bits of r[rl]. No protocol knowledge: no IHL parsing, no
// skipped bytes, no write-back — the program zeroes the old field and stores
// the result itself. len = 0 yields 0xFFFF. An odd final byte is
// high-weighted (RFC 1071).
function clause execute(CSUM(rd, h, off, rl)) = {
    if hdr_base(unsigned(h)) < 0 then {
        raise_err(err_hdr_absent)
    } else {
        let base = eff_addr(h, off);
        let len = unsigned(read_reg(rl)[15 .. 0]);
        if base < 0 | base + len > win_limit() then {
            raise_err(err_window_violation)
        } else {
            var sum : int = 0;
            var i : int = 0;
            while i < len do {
                let b0 = unsigned(read_win_byte(base + i));
                let b1 = if i + 1 < len then unsigned(read_win_byte(base + i + 1)) else 0;
                sum = sum + b0 * 256 + b1;
                i = i + 2
            };
            // Fold carries in the bits32 domain (sum < 2^25 for len <= 288).
            var s : bits32 = get_slice_int(32, sum, 0);
            while unsigned(sail_shiftright(s, 16)) != 0 do {
                s = (s & 0x0000FFFF) + sail_shiftright(s, 16)
            };
            write_reg(rd, sail_zero_extend(xor_vec(s[15 .. 0], 0xFFFF), 64))
        }
    }
}

// SEND delta: terminate; the transmitted frame starts delta bytes before the
// original frame start (positive = prepend from headroom, negative = strip).
// Range: within headroom, and strictly less than the frame length. Egress
// meaning lives in md, outside the ISA.
function clause execute(SEND(delta)) = {
    let d = signed(delta);
    let pl = if unsigned(plen) < buf_bytes then unsigned(plen) else buf_bytes;
    if d > headroom_bytes | d <= negate(pl) then {
        raise_err(err_send_range)
    } else {
        send_delta = d;
        verdict = verdict_sent;
        halted = true
    }
}

// DROP: terminate without output.
function clause execute(DROP()) = {
    verdict = verdict_drop;
    halted = true
}

// ILLEGAL: any unassigned encoding, including the all-zeros word.
function clause execute(ILLEGAL()) = {
    raise_err(err_illegal)
}

end execute
