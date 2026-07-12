"""Instruction-set simulator for the nanuk parser ISA v0.

Executes assembled 32-bit words — the same artifact the generated C
emulator and the RTL core consume — and records a full per-step trace.
Semantics mirror spec/parser-model ({exec,insts,decode,state}.sail)
exactly: same error codes, same step accounting (budget checked before
execute, counted at fetch), reserved encoding bits enforced (nonzero →
ILLEGAL). Constants are deliberate local mirrors of params.sail
(mirror-with-tripwire doctrine); spec/python's differential test pins
this file to the golden model at run time.
"""

from dataclasses import dataclass

# Mirror of spec/parser-model/params.sail
BUF_BYTES = 256
IMEM_WORDS = 1024
NHDR = 16
SMD_SLOTS = 8
STEP_BUDGET = 256

# Mirror of spec/parser-model/state.sail
VERDICT_ACCEPT = 0
VERDICT_DROP = 1
VERDICT_ERROR = 2

ERR_NONE = 0
ERR_HDR_VIOLATION = 1
ERR_STEP_BUDGET = 2
ERR_ILLEGAL = 3
ERR_PC_RANGE = 4
ERR_SMD_RANGE = 5

_MASK64 = (1 << 64) - 1
_MASK16 = (1 << 16) - 1
_RZ = 4


@dataclass(frozen=True)
class IssStep:
    """One executed instruction: pc before, architectural state after."""

    pc: int
    line: int | None
    regs: tuple[int, int, int, int]
    cursor: int
    hdr_present: tuple[int, ...]
    hdr_offset: tuple[int, ...]
    smd: tuple[int, ...]


@dataclass(frozen=True)
class IssResult:
    """First seven fields mirror nanuk_spec.harness.ParseResult."""

    verdict: int
    error: int
    payload_offset: int
    steps: int
    hdr_present: list[int]
    hdr_offset: list[int]
    smd: list[int]
    trace: list[IssStep]


def _decode(w: int):
    """Word -> ("mnemonic", fields...) or None for ILLEGAL.

    Field layouts and reserved-bit masks mirror spec/parser-model/decode.sail;
    any nonzero reserved bit or register code > 4 fails to decode.
    """
    op = w >> 26
    r1 = (w >> 23) & 7  # first register field, when present
    r2 = (w >> 20) & 7  # second register field, when present
    match op:
        case 0x01:  # EXT rd, boff(11), szm1(6)
            if w & 0x3F or r1 > 4:
                return None
            return ("ext", r1, (w >> 12) & 0x7FF, ((w >> 6) & 0x3F) + 1)
        case 0x02:  # ADVI imm16
            if w & 0x03FF0000:
                return None
            return ("advi", w & _MASK16)
        case 0x03:  # ADVR rs
            if w & 0x007FFFFF or r1 > 4:
                return None
            return ("advr", r1)
        case 0x04:  # MOVI rd, imm16
            if w & 0x007F0000 or r1 > 4:
                return None
            return ("movi", r1, w & _MASK16)
        case 0x05:  # SHL rd, rs, shamt(6)
            if w & 0x3FFF or r1 > 4 or r2 > 4:
                return None
            return ("shl", r1, r2, (w >> 14) & 0x3F)
        case 0x06 | 0x07:  # BEQ/BNE rs, rt, tgt16
            if w & 0x000F0000 or r1 > 4 or r2 > 4:
                return None
            return ("beq" if op == 0x06 else "bne", r1, r2, w & _MASK16)
        case 0x08:  # JMP tgt16
            if w & 0x03FF0000:
                return None
            return ("jmp", w & _MASK16)
        case 0x09:  # SETHDR hdr(4)
            if w & 0x03FFFFF0:
                return None
            return ("sethdr", w & 0xF)
        case 0x0A:  # STMD rs, nm1(2), slot(4)
            if w & 0x0001FFFF or r1 > 4:
                return None
            return ("stmd", r1, ((w >> 21) & 0x3) + 1, (w >> 17) & 0xF)
        case 0x0B:  # HALT drop(1)
            if w & 0x03FFFFFE:
                return None
            return ("halt", w & 1)
        case _:
            return None


class _Machine:
    def __init__(self, words: list[int], packet: bytes, line_map=None):
        self.words = words
        self.packet = packet
        self.line_map = line_map
        self.hdr_limit = min(len(packet), BUF_BYTES)
        self.regs = [0, 0, 0, 0]
        self.cursor = 0
        self.pc = 0
        self.steps = 0
        self.hdr_present = [0] * NHDR
        self.hdr_offset = [0] * NHDR
        self.smd = [0] * SMD_SLOTS
        self.halted = False
        self.verdict = VERDICT_ACCEPT
        self.err = ERR_NONE
        self.trace: list[IssStep] = []

    def read_reg(self, r: int) -> int:
        return 0 if r == _RZ else self.regs[r]

    def write_reg(self, r: int, v: int) -> None:
        if r != _RZ:
            self.regs[r] = v & _MASK64

    def raise_err(self, code: int) -> None:
        self.verdict = VERDICT_ERROR
        self.err = code
        self.halted = True

    def step(self) -> None:
        # Mirrors step() in spec/parser-model/exec.sail: budget, then pc
        # range, then decode/execute; the executed instruction is counted
        # at fetch, so an error-halting instruction has already ticked.
        if self.steps >= STEP_BUDGET:
            self.raise_err(ERR_STEP_BUDGET)
            return
        if self.pc >= IMEM_WORDS:
            self.raise_err(ERR_PC_RANGE)
            return
        fetch_pc = self.pc
        w = self.words[fetch_pc] if fetch_pc < len(self.words) else 0
        self.steps += 1
        self.pc += 1
        self._execute(_decode(w))
        self.trace.append(
            IssStep(
                pc=fetch_pc,
                line=(
                    self.line_map[fetch_pc]
                    if self.line_map is not None and fetch_pc < len(self.line_map)
                    else None
                ),
                regs=tuple(self.regs),
                cursor=self.cursor,
                hdr_present=tuple(self.hdr_present),
                hdr_offset=tuple(self.hdr_offset),
                smd=tuple(self.smd),
            )
        )

    def _execute(self, decoded) -> None:
        if decoded is None:
            self.raise_err(ERR_ILLEGAL)
            return
        match decoded:
            case ("ext", rd, boff, size):
                pos = self.cursor * 8 + boff
                if pos + size > self.hdr_limit * 8:
                    self.raise_err(ERR_HDR_VIOLATION)
                    return
                first, last = pos // 8, (pos + size - 1) // 8
                chunk = int.from_bytes(self.packet[first : last + 1], "big")
                drop = (last - first + 1) * 8 - (pos % 8) - size
                self.write_reg(rd, (chunk >> drop) & ((1 << size) - 1))
            case ("advi", imm):
                self._advance(imm)
            case ("advr", rs):
                self._advance(self.read_reg(rs) & _MASK16)
            case ("movi", rd, imm):
                self.write_reg(rd, imm)
            case ("shl", rd, rs, sh):
                self.write_reg(rd, self.read_reg(rs) << sh)
            case ("beq", rs, rt, tgt):
                if self.read_reg(rs) == self.read_reg(rt):
                    self.pc = tgt
            case ("bne", rs, rt, tgt):
                if self.read_reg(rs) != self.read_reg(rt):
                    self.pc = tgt
            case ("jmp", tgt):
                self.pc = tgt
            case ("sethdr", h):
                self.hdr_present[h] = 1
                self.hdr_offset[h] = self.cursor
            case ("stmd", rs, nunits, slot):
                if slot + nunits > SMD_SLOTS:
                    self.raise_err(ERR_SMD_RANGE)
                    return
                v = self.read_reg(rs)
                for i in range(nunits):
                    self.smd[slot + i] = (v >> (16 * (nunits - 1 - i))) & _MASK16
            case ("halt", drop):
                self.verdict = VERDICT_DROP if drop else VERDICT_ACCEPT
                self.halted = True

    def _advance(self, amount: int) -> None:
        if self.cursor + amount > self.hdr_limit:
            self.raise_err(ERR_HDR_VIOLATION)
            return
        self.cursor += amount


def run_iss(
    prog: bytes, packet: bytes, *, line_map: list[int] | None = None
) -> IssResult:
    """Run one packet through the ISS. Total, like the ISA.

    prog: big-endian 32-bit words (the assembler's output). line_map:
    per-word 1-based source lines, from assemble_with_lines.
    """
    if len(prog) % 4:
        raise ValueError("program length is not a multiple of 4 bytes")
    words = [int.from_bytes(prog[i : i + 4], "big") for i in range(0, len(prog), 4)]
    m = _Machine(words, packet, line_map)
    while not m.halted:
        m.step()
    return IssResult(
        verdict=m.verdict,
        error=m.err,
        payload_offset=m.cursor,
        steps=m.steps,
        hdr_present=m.hdr_present,
        hdr_offset=m.hdr_offset,
        smd=m.smd,
        trace=m.trace,
    )
