"""Instruction encodings for the nanuk parser ISA v0.

This mirrors the encdec mapping in spec/model/decode.sail — the Sail spec
owns the encoding truth; test_encoding.py pins both to the same golden words,
and the harness's differential test guards against drift at run time.

32-bit words, opcode at [31:26], reserved bits zero.
"""

REGS = {"r0": 0, "r1": 1, "r2": 2, "r3": 3, "rz": 4}

OP_EXT = 0x01
OP_ADVI = 0x02
OP_ADVR = 0x03
OP_MOVI = 0x04
OP_SHL = 0x05
OP_BEQ = 0x06
OP_BNE = 0x07
OP_JMP = 0x08
OP_SETHDR = 0x09
OP_STMD = 0x0A
OP_HALT = 0x0B


def _check(value: int, width: int, what: str) -> int:
    if not 0 <= value < (1 << width):
        raise ValueError(f"{what} {value} does not fit in {width} bits")
    return value


def _reg(name: str) -> int:
    if name not in REGS:
        raise ValueError(f"unknown register {name!r}")
    return REGS[name]


def encode_ext(rd: str, boff: int, bsize: int) -> int:
    if not 1 <= bsize <= 64:
        raise ValueError(f"EXT size {bsize} out of range 1..64")
    return (
        (OP_EXT << 26)
        | (_reg(rd) << 23)
        | (_check(boff, 11, "EXT bit offset") << 12)
        | ((bsize - 1) << 6)
    )


def encode_advi(imm: int) -> int:
    return (OP_ADVI << 26) | _check(imm, 16, "ADVI immediate")


def encode_advr(rs: str) -> int:
    return (OP_ADVR << 26) | (_reg(rs) << 23)


def encode_movi(rd: str, imm: int) -> int:
    return (OP_MOVI << 26) | (_reg(rd) << 23) | _check(imm, 16, "MOVI immediate")


def encode_shl(rd: str, rs: str, shamt: int) -> int:
    return (
        (OP_SHL << 26)
        | (_reg(rd) << 23)
        | (_reg(rs) << 20)
        | (_check(shamt, 6, "SHL shift amount") << 14)
    )


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


def encode_sethdr(hdr: int) -> int:
    return (OP_SETHDR << 26) | _check(hdr, 4, "header id")


def encode_stmd(slot: int, rs: str, nunits: int) -> int:
    if not 1 <= nunits <= 4:
        raise ValueError(f"STMD unit count {nunits} out of range 1..4")
    if _check(slot, 4, "SMD slot") + nunits > 8:
        raise ValueError(f"STMD slots {slot}..{slot + nunits - 1} exceed slot 7")
    return (
        (OP_STMD << 26)
        | (_reg(rs) << 23)
        | ((nunits - 1) << 21)
        | (slot << 17)
    )


def encode_halt(drop: bool) -> int:
    return (OP_HALT << 26) | (1 if drop else 0)
