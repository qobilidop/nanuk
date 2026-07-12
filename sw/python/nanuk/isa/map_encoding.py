"""Instruction encodings for the Nanuk MAP ISA v0.

This mirrors the encdec mapping in spec/sail/model/map/decode.sail — the Sail spec
owns the encoding truth; test_map_encoding.py pins both to the same golden
words (the drift tripwire shared with spec/map-test/test_map_decode.sail).

32-bit words, opcode at [31:26], reserved bits zero. The `off` (LD/ST/
CSUM) and `delta` (SEND) fields are 10-bit two's complement.
"""

REGS = {"r0": 0, "r1": 1, "r2": 2, "r3": 3, "rz": 4}

# Reserved header base id: always valid, base = frame start.
H_FRAME = 15

OP_LD = 0x01
OP_ST = 0x02
OP_LDMD = 0x03
OP_MOVI = 0x04
OP_ADDI = 0x05
OP_BEQ = 0x06
OP_BNE = 0x07
OP_JMP = 0x08
OP_LOOKUP = 0x09
OP_CSUM = 0x0A
OP_SEND = 0x0B
OP_DROP = 0x0C
OP_STMD = 0x0D
OP_ANDI = 0x0E
OP_SHLI = 0x0F


def _check(value: int, width: int, what: str) -> int:
    if not 0 <= value < (1 << width):
        raise ValueError(f"{what} {value} does not fit in {width} bits")
    return value


def _signed(value: int, width: int, what: str) -> int:
    lo, hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
    if not lo <= value <= hi:
        raise ValueError(f"{what} {value} out of signed range {lo}..{hi}")
    return value & ((1 << width) - 1)


def _reg(name: str) -> int:
    if name not in REGS:
        raise ValueError(f"unknown register {name!r}")
    return REGS[name]


def _encode_mem(op: int, r: str, hdr: int, off: int, nbytes: int) -> int:
    if not 1 <= nbytes <= 8:
        raise ValueError(f"byte count {nbytes} out of range 1..8")
    return (
        (op << 26)
        | (_reg(r) << 23)
        | (_check(hdr, 4, "header id") << 19)
        | (_signed(off, 10, "byte offset") << 9)
        | ((nbytes - 1) << 6)
    )


def encode_ld(rd: str, hdr: int, off: int, nbytes: int) -> int:
    return _encode_mem(OP_LD, rd, hdr, off, nbytes)


def encode_st(rs: str, hdr: int, off: int, nbytes: int) -> int:
    return _encode_mem(OP_ST, rs, hdr, off, nbytes)


def encode_ldmd(rd: str, field: int) -> int:
    return (OP_LDMD << 26) | (_reg(rd) << 23) | (_check(field, 4, "md slot") << 19)


def encode_movi(rd: str, imm: int) -> int:
    return (OP_MOVI << 26) | (_reg(rd) << 23) | _check(imm, 16, "MOVI immediate")


def encode_addi(rd: str, rs: str, imm: int) -> int:
    # Accepts -32768..65535: negative values are encoded two's-complement;
    # the machine sign-extends the 16-bit field.
    if imm < 0:
        imm = _signed(imm, 16, "ADDI immediate")
    else:
        imm = _check(imm, 16, "ADDI immediate")
    return (OP_ADDI << 26) | (_reg(rd) << 23) | (_reg(rs) << 20) | imm


def _encode_branch(op: int, rs: str, rt: str, target: int) -> int:
    return (
        (op << 26)
        | (_reg(rs) << 23)
        | (_reg(rt) << 20)
        | _check(target, 16, "branch target")
    )


def encode_beq(rs: str, rt: str, target: int) -> int:
    return _encode_branch(OP_BEQ, rs, rt, target)


def encode_bne(rs: str, rt: str, target: int) -> int:
    return _encode_branch(OP_BNE, rs, rt, target)


def encode_jmp(target: int) -> int:
    return (OP_JMP << 26) | _check(target, 16, "jump target")


def encode_lookup(rd: str, table: int, rs: str, target: int) -> int:
    return (
        (OP_LOOKUP << 26)
        | (_reg(rd) << 23)
        | (_check(table, 4, "table id") << 19)
        | (_reg(rs) << 16)
        | _check(target, 16, "miss target")
    )


def encode_csum(rd: str, hdr: int, off: int, rl: str) -> int:
    return (
        (OP_CSUM << 26)
        | (_reg(rd) << 23)
        | (_check(hdr, 4, "header id") << 19)
        | (_signed(off, 10, "byte offset") << 9)
        | (_reg(rl) << 6)
    )


def encode_send(delta: int) -> int:
    return (OP_SEND << 26) | (_signed(delta, 10, "send delta") << 13)


def encode_drop() -> int:
    return OP_DROP << 26


def encode_stmd(rs: str, nunits: int, slot: int) -> int:
    if not 1 <= nunits <= 4:
        raise ValueError(f"unit count {nunits} out of range 1..4")
    return (
        (OP_STMD << 26)
        | (_reg(rs) << 23)
        | ((nunits - 1) << 21)
        | (_check(slot, 4, "md slot") << 17)
    )


def encode_andi(rd: str, rs: str, imm: int) -> int:
    return (
        (OP_ANDI << 26)
        | (_reg(rd) << 23)
        | (_reg(rs) << 20)
        | _check(imm, 16, "ANDI immediate")
    )


def encode_shli(rd: str, rs: str, sh: int) -> int:
    return (
        (OP_SHLI << 26)
        | (_reg(rd) << 23)
        | (_reg(rs) << 20)
        | (_check(sh, 6, "shift amount") << 14)
    )
