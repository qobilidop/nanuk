// Execute clauses for the v0 core. Each clause either completes (pc already
// advanced by step()), redirects pc (branches), or enters an error/halt state.

val execute : instr -> unit
scattered function execute

// EXT rd, boff, szm1: rd = zero-extended bits at (cursor*8 + boff, size).
// Reading past hdr_limit is a header violation.
function clause execute(EXT(rd, boff, szm1)) = {
    let sz = unsigned(szm1) + 1;
    let pos = unsigned(cursor) * 8 + unsigned(boff);
    if pos + sz > hdr_limit() * 8 then {
        raise_err(err_hdr_violation)
    } else {
        write_reg(rd, read_pkt_bits(pos, sz))
    }
}

// ADVI imm: cursor += imm bytes. Past hdr_limit is a violation.
function clause execute(ADVI(imm)) = {
    let ncur = unsigned(cursor) + unsigned(imm);
    if ncur > hdr_limit() then {
        raise_err(err_hdr_violation)
    } else {
        cursor = to_bits16(ncur)
    }
}

// ADVR rs: cursor += rs[15:0] bytes (defined: upper bits of rs are ignored).
function clause execute(ADVR(rs)) = {
    let amt = unsigned(read_reg(rs)[15 .. 0]);
    let ncur = unsigned(cursor) + amt;
    if ncur > hdr_limit() then {
        raise_err(err_hdr_violation)
    } else {
        cursor = to_bits16(ncur)
    }
}

// MOVI rd, imm16: rd = zero-extended immediate.
function clause execute(MOVI(rd, imm)) = {
    write_reg(rd, sail_zeros(48) @ imm)
}

// SHL rd, rs, sh: 64-bit left shift, truncating.
function clause execute(SHL(rd, rs, sh)) = {
    write_reg(rd, sail_shiftleft(read_reg(rs), unsigned(sh)))
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

// SETHDR h: record present bit + cursor offset for header id h.
function clause execute(SETHDR(h)) = {
    set_hdr(unsigned(h))
}

// STMD rs, nm1, slot: write low (nm1+1)*16 bits of rs into md slots
// slot..slot+nm1, MSB-first. Out-of-range slots are an md range error.
function clause execute(STMD(rs, nm1, slot)) = {
    let n = unsigned(nm1) + 1;
    let s = unsigned(slot);
    if s + n > md_slots then {
        raise_err(err_md_range)
    } else {
        let v = read_reg(rs);
        var i : int = 0;
        while i < n do {
            let sh = (n - 1 - i) * 16;
            let chunk = sail_shiftright(v, sh);
            write_md_slot(s + i, chunk[15 .. 0]);
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

// HALT: accept (drop=false) or drop (drop=true); payload offset = cursor.
function clause execute(HALT(drop)) = {
    verdict = if drop then verdict_drop else verdict_accept;
    halted = true
}

// ILLEGAL: any unassigned encoding, including the all-zeros word.
function clause execute(ILLEGAL()) = {
    raise_err(err_illegal)
}

end execute
