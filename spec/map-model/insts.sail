// Execute clauses for the MAP v0 core. Each clause either completes (pc
// already advanced by step()), redirects pc (branches, LOOKUP miss), or
// enters an error/halt state.

let bit64_one : bits64 = 0x0000000000000001

// Bitmap of all ports: n_ports low bits set.
val port_mask_all : unit -> bits64
function port_mask_all() = {
    let np = n_ports;
    // Literal bounds for flow typing (can't see through the let-global).
    assert(1 <= np & np <= 8, "n_ports out of supported range");
    sail_mask(64, sail_ones(np))
}

// Flood mask: all ports except ingress. If ingress >= n_ports (harness
// misconfiguration), the ingress bit falls outside the mask and flood = all.
val flood_mask : unit -> bits64
function flood_mask() = {
    let all = port_mask_all();
    let ing = sail_shiftleft(bit64_one, unsigned(ingress)) & all;
    xor_vec(all, ing)
}

// Effective window index for hdr-relative addressing, or -1 when the header
// base is absent (caller raises err_hdr_absent). Offsets are signed.
val eff_addr : (bits4, bits10) -> int
function eff_addr(h, off) = {
    let base = hdr_base(unsigned(h));
    if base < 0 then negate(1)
    else headroom_bytes + base + signed(off)
}

// Inbound-SMD field read (LDMD). Fields 0-7: PP SMD slots; 8: ingress port;
// 9: flood mask; 10: hdr_present bitmap; 11-15: defined zero.
val ld_field : int -> bits64
function ld_field(f) = {
    assert(0 <= f & f < 16, "LDMD field id out of range");
    if f < 8 then {
        assert(0 <= f & f < 8, "SMD slot index out of bounds");
        sail_zero_extend(smd_in[f], 64)
    } else if f == 8 then sail_zero_extend(ingress, 64)
    else if f == 9 then flood_mask()
    else if f == 10 then {
        var bm : bits64 = sail_zeros(64);
        var h : int = 0;
        while h < 16 do {
            let hi = h;
            assert(0 <= hi & hi < 16, "header id out of bounds");
            if hdr_present_in[hi] then bm = bm | sail_shiftleft(bit64_one, hi);
            h = h + 1
        };
        bm
    }
    else sail_zeros(64)
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

// LDMD rd, field: rd = inbound-SMD field (see ld_field).
function clause execute(LDMD(rd, f)) = {
    write_reg(rd, ld_field(unsigned(f)))
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

// CSUMUPD hdr, off: recompute the IPv4 header checksum in place. Header
// length comes from the IHL nibble; the checksum field (bytes 10-11) is
// treated as zero for the sum; RFC 1071 end-around-carry fold; complement
// written back big-endian. IHL < 5 or a header hanging past the window is a
// window violation.
function clause execute(CSUMUPD(h, off)) = {
    let base = eff_addr(h, off);
    if base < 0 & hdr_base(unsigned(h)) < 0 then {
        raise_err(err_hdr_absent)
    } else if base < 0 | base + 20 > win_limit() then {
        raise_err(err_window_violation)
    } else {
        let ihl = unsigned(read_win_byte(base) & 0x0F);
        if ihl < 5 then {
            raise_err(err_window_violation)
        } else {
            let hlen = ihl * 4;
            if base + hlen > win_limit() then {
                raise_err(err_window_violation)
            } else {
                var sum : int = 0;
                var i : int = 0;
                while i < hlen do {
                    let b0 = if i == 10 then 0x00 else read_win_byte(base + i);
                    let b1 = if i == 10 then 0x00 else read_win_byte(base + i + 1);
                    sum = sum + unsigned(b0) * 256 + unsigned(b1);
                    i = i + 2
                };
                // Fold carries in the bits32 domain (sum < 2^21 for hlen <= 60).
                var s : bits32 = get_slice_int(32, sum, 0);
                while unsigned(sail_shiftright(s, 16)) != 0 do {
                    s = (s & 0x0000FFFF) + sail_shiftright(s, 16)
                };
                let ck : bits16 = xor_vec(s[15 .. 0], 0xFFFF);
                write_win_byte(base + 10, ck[15 .. 8]);
                write_win_byte(base + 11, ck[7 .. 0])
            }
        }
    }
}

// SEND rs, delta: terminate; egress = rs masked to the port bitmap; the
// transmitted frame starts delta bytes before the original frame start
// (positive = prepend from headroom, negative = strip). Range: within
// headroom, and strictly less than the frame length.
function clause execute(SEND(rs, delta)) = {
    let d = signed(delta);
    let pl = if unsigned(plen) < buf_bytes then unsigned(plen) else buf_bytes;
    if d > headroom_bytes | d <= negate(pl) then {
        raise_err(err_send_range)
    } else {
        egress = (read_reg(rs) & port_mask_all())[7 .. 0];
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
