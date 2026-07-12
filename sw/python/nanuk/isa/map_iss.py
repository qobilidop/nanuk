"""Instruction-set simulator for the Nanuk MAP ISA v0. Sibling of iss.py.

Executes assembled 32-bit words against the window (32-byte headroom +
frame), the metadata window, the inbound PP hdr map, and exact-match
tables. Semantics
mirror spec/sail/model/map ({exec,insts,decode,state}.sail): same error
codes, same step accounting, reserved bits enforced. Table entries are
masked to the declared widths, matching the emulator's load behavior
and map_interp. Records a full per-step trace including window-write
and lookup events.
"""

from dataclasses import dataclass

# Mirror of spec/sail/model/map/params.sail
HEADROOM_BYTES = 32
BUF_BYTES = 256
WIN_BYTES = 288
N_TABLES = 4
IMEM_WORDS = 1024
STEP_BUDGET = 256

# Mirror of spec/sail/model/map/state.sail
VERDICT_SENT = 0
VERDICT_DROP = 1
VERDICT_ERROR = 2

ERR_NONE = 0
ERR_WINDOW_VIOLATION = 1
ERR_STEP_BUDGET = 2
ERR_ILLEGAL = 3
ERR_PC_RANGE = 4
ERR_HDR_ABSENT = 5
ERR_SEND_RANGE = 6

H_FRAME = 15

_MASK64 = (1 << 64) - 1
_MASK16 = (1 << 16) - 1
_RZ = 4


@dataclass(frozen=True)
class MatchActionIssStep:
    """One executed instruction: pc before, registers after, effects."""

    pc: int
    line: int | None
    regs: tuple[int, int, int, int]
    writes: tuple[tuple[int, bytes], ...]
    lookup: tuple[int, int, bool, int] | None


@dataclass(frozen=True)
class MatchActionIssResult:
    """First six fields mirror nanuk.testkit.map_harness.MatchActionResult."""

    verdict: int
    error: int
    md: tuple[int, ...]
    delta: int
    steps: int
    frame: bytes | None
    trace: list[MatchActionIssStep]


def _sext10(v: int) -> int:
    return v - (1 << 10) if v & (1 << 9) else v


def _sext16(v: int) -> int:
    return v - (1 << 16) if v & (1 << 15) else v


def _mask(value: int, width: int) -> int:
    if width <= 0:
        return 0
    return value & ((1 << min(width, 64)) - 1)


def _decode(w: int):
    """Word -> ("mnemonic", fields...) or None for ILLEGAL. Layouts and
    reserved-bit masks mirror spec/sail/model/map/decode.sail."""
    op = w >> 26
    r1 = (w >> 23) & 7
    r2 = (w >> 20) & 7
    match op:
        case 0x01 | 0x02:  # LD/ST r, hdr(4), off(10 signed), nm1(3)
            if w & 0x3F or r1 > 4:
                return None
            return (
                "ld" if op == 0x01 else "st",
                r1,
                (w >> 19) & 0xF,
                _sext10((w >> 9) & 0x3FF),
                ((w >> 6) & 0x7) + 1,
            )
        case 0x03:  # LDMD rd, field(4)
            if w & 0x0007FFFF or r1 > 4:
                return None
            return ("ldmd", r1, (w >> 19) & 0xF)
        case 0x04:  # MOVI rd, imm16
            if w & 0x007F0000 or r1 > 4:
                return None
            return ("movi", r1, w & _MASK16)
        case 0x05:  # ADDI rd, rs, imm16 (sign-extended)
            if w & 0x000F0000 or r1 > 4 or r2 > 4:
                return None
            return ("addi", r1, r2, _sext16(w & _MASK16))
        case 0x06 | 0x07:  # BEQ/BNE rs, rt, tgt16
            if w & 0x000F0000 or r1 > 4 or r2 > 4:
                return None
            return ("beq" if op == 0x06 else "bne", r1, r2, w & _MASK16)
        case 0x08:  # JMP tgt16
            if w & 0x03FF0000:
                return None
            return ("jmp", w & _MASK16)
        case 0x09:  # LOOKUP rd, table(4), rs, miss-tgt16
            rs = (w >> 16) & 7
            if r1 > 4 or rs > 4:
                return None
            return ("lookup", r1, (w >> 19) & 0xF, rs, w & _MASK16)
        case 0x0A:  # CSUM rd, hdr(4), off(10 signed), rl
            if w & 0x3F or r1 > 4 or ((w >> 6) & 7) > 4:
                return None
            return ("csum", r1, (w >> 19) & 0xF, _sext10((w >> 9) & 0x3FF), (w >> 6) & 7)
        case 0x0B:  # SEND delta(10 signed)
            if w & 0x1FFF or (w >> 23) & 7:
                return None
            return ("send", _sext10((w >> 13) & 0x3FF))
        case 0x0C:  # DROP
            if w & 0x03FFFFFF:
                return None
            return ("drop",)
        case 0x0D:  # STMD rs, nm1(2), slot(4)
            if w & 0x1FFFF or r1 > 4:
                return None
            return ("stmd", r1, ((w >> 21) & 3) + 1, (w >> 17) & 0xF)
        case 0x0E:  # ANDI rd, rs, imm16 (zero-extended)
            if w & 0x000F0000 or r1 > 4 or r2 > 4:
                return None
            return ("andi", r1, r2, w & _MASK16)
        case 0x0F:  # SHLI rd, rs, sh(6)
            if w & 0x3FFF or r1 > 4 or r2 > 4:
                return None
            return ("shli", r1, r2, (w >> 14) & 0x3F)
        case _:
            return None


class _Machine:
    def __init__(self, words, packet: bytes, pp, tables, md_in, line_map=None):
        self.words = words
        self.line_map = line_map
        self.window = bytearray(WIN_BYTES)
        self.plen_min = min(len(packet), BUF_BYTES)
        self.window[HEADROOM_BYTES : HEADROOM_BYTES + self.plen_min] = packet[
            : self.plen_min
        ]
        self.win_limit = HEADROOM_BYTES + self.plen_min
        self.pp = pp
        self.tables = list(tables)
        md = [v & _MASK16 for v in md_in]
        self.md = md + [0] * (8 - len(md))
        self.regs = [0, 0, 0, 0]
        self.pc = 0
        self.steps = 0
        self.halted = False
        self.verdict = VERDICT_SENT
        self.err = ERR_NONE
        self.delta = 0
        self.trace: list[MatchActionIssStep] = []
        # per-step effect accumulators
        self._writes: list[tuple[int, bytes]] = []
        self._lookup: tuple[int, int, bool, int] | None = None

    def read_reg(self, r: int) -> int:
        return 0 if r == _RZ else self.regs[r]

    def write_reg(self, r: int, v: int) -> None:
        if r != _RZ:
            self.regs[r] = v & _MASK64

    def raise_err(self, code: int) -> None:
        self.verdict = VERDICT_ERROR
        self.err = code
        self.halted = True

    def write_win(self, addr: int, data: bytes) -> None:
        self.window[addr : addr + len(data)] = data
        self._writes.append((addr, bytes(data)))

    def hdr_base(self, h: int) -> int:
        if h == H_FRAME:
            return 0
        if self.pp.hdr_present[h]:
            return self.pp.hdr_offset[h]
        return -1

    def _checked_addr(self, h: int, off: int, nbytes: int) -> int | None:
        """eff_addr + the LD/ST/CSUMUPD error ladder from insts.sail."""
        base = self.hdr_base(h)
        if base < 0:
            self.raise_err(ERR_HDR_ABSENT)
            return None
        addr = HEADROOM_BYTES + base + off
        if addr < 0 or addr + nbytes > self.win_limit:
            self.raise_err(ERR_WINDOW_VIOLATION)
            return None
        return addr

    def step(self) -> None:
        # Mirrors step() in spec/sail/model/map/exec.sail.
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
        self._writes = []
        self._lookup = None
        self._execute(_decode(w))
        self.trace.append(
            MatchActionIssStep(
                pc=fetch_pc,
                line=(
                    self.line_map[fetch_pc]
                    if self.line_map is not None and fetch_pc < len(self.line_map)
                    else None
                ),
                regs=tuple(self.regs),
                writes=tuple(self._writes),
                lookup=self._lookup,
            )
        )

    def _execute(self, decoded) -> None:
        if decoded is None:
            self.raise_err(ERR_ILLEGAL)
            return
        match decoded:
            case ("ld", rd, h, off, n):
                addr = self._checked_addr(h, off, n)
                if addr is not None:
                    self.write_reg(rd, int.from_bytes(self.window[addr : addr + n], "big"))
            case ("st", rs, h, off, n):
                addr = self._checked_addr(h, off, n)
                if addr is not None:
                    v = self.read_reg(rs) & ((1 << (8 * n)) - 1)
                    self.write_win(addr, v.to_bytes(n, "big"))
            case ("ldmd", rd, f):
                if f >= 8:
                    self.raise_err(ERR_ILLEGAL)
                else:
                    self.write_reg(rd, self.md[f])
            case ("stmd", rs, n, slot):
                if slot + n > 8:
                    self.raise_err(ERR_ILLEGAL)
                else:
                    v = self.read_reg(rs)
                    for i in range(n):
                        self.md[slot + i] = (v >> (16 * (n - 1 - i))) & _MASK16
            case ("andi", rd, rs, imm):
                self.write_reg(rd, self.read_reg(rs) & imm)
            case ("shli", rd, rs, sh):
                self.write_reg(rd, self.read_reg(rs) << sh)
            case ("movi", rd, imm):
                self.write_reg(rd, imm)
            case ("addi", rd, rs, imm):
                self.write_reg(rd, self.read_reg(rs) + imm)
            case ("beq", rs, rt, tgt):
                if self.read_reg(rs) == self.read_reg(rt):
                    self.pc = tgt
            case ("bne", rs, rt, tgt):
                if self.read_reg(rs) != self.read_reg(rt):
                    self.pc = tgt
            case ("jmp", tgt):
                self.pc = tgt
            case ("lookup", rd, t, rs, tgt):
                hit, action, key = False, 0, 0
                table = self.tables[t] if t < min(len(self.tables), N_TABLES) else None
                if table is not None and table.key_width > 0:
                    key = _mask(self.read_reg(rs), table.key_width)
                    for k, act in table.entries.items():
                        if _mask(k, table.key_width) == key:
                            hit, action = True, _mask(act, table.action_width)
                            break
                self._lookup = (t, key, hit, action)
                if hit:
                    self.write_reg(rd, action)
                else:
                    self.write_reg(rd, 0)
                    self.pc = tgt
            case ("csum", rd, h, off, rl):
                self._csum(rd, h, off, rl)
            case ("send", d):
                if d > HEADROOM_BYTES or d <= -self.plen_min:
                    self.raise_err(ERR_SEND_RANGE)
                    return
                self.delta = d
                self.verdict = VERDICT_SENT
                self.halted = True
            case ("drop",):
                self.verdict = VERDICT_DROP
                self.halted = True

    def _csum(self, rd: int, h: int, off: int, rl: int) -> None:
        # RFC 1071 ones-complement checksum of an explicit range into rd.
        base_hdr = self.hdr_base(h)
        if base_hdr < 0:
            self.raise_err(ERR_HDR_ABSENT)
            return
        base = HEADROOM_BYTES + base_hdr + off
        length = self.read_reg(rl) & _MASK16
        if base < 0 or base + length > self.win_limit:
            self.raise_err(ERR_WINDOW_VIOLATION)
            return
        total = 0
        for i in range(0, length, 2):
            b0 = self.window[base + i]
            b1 = self.window[base + i + 1] if i + 1 < length else 0
            total += (b0 << 8) | b1
        while total > 0xFFFF:
            total = (total & 0xFFFF) + (total >> 16)
        self.write_reg(rd, total ^ 0xFFFF)


def run_map_iss(
    prog: bytes,
    packet: bytes,
    pp,
    tables,
    md_in,
    *,
    line_map: list[int] | None = None,
) -> MatchActionIssResult:
    """Run one already-parsed frame through the MAP ISS. Total, like the ISA.

    pp: ParserResult-shaped (hdr_present/hdr_offset). tables: list of
    Table-shaped objects (key_width/action_width/entries), index = table
    id. md_in: up to 8 16-bit metadata slots (pass-through default).
    prog: big-endian 32-bit words.
    """
    if len(prog) % 4:
        raise ValueError("program length is not a multiple of 4 bytes")
    words = [int.from_bytes(prog[i : i + 4], "big") for i in range(0, len(prog), 4)]
    m = _Machine(words, bytes(packet), pp, tables, md_in, line_map)
    while not m.halted:
        m.step()
    frame = None
    if m.verdict == VERDICT_SENT:
        start = HEADROOM_BYTES - m.delta
        frame = bytes(m.window[start : HEADROOM_BYTES + m.plen_min]) + bytes(
            packet[BUF_BYTES:]
        )
    return MatchActionIssResult(
        verdict=m.verdict,
        error=m.err,
        md=tuple(m.md),
        delta=m.delta,
        steps=m.steps,
        frame=frame,
        trace=m.trace,
    )
