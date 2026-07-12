"""Instruction-set simulator for the nanuk MAP ISA v0. Sibling of iss.py.

Executes assembled 32-bit words against the window (32-byte headroom +
frame), the inbound PP contract, and exact-match tables. Semantics
mirror spec/map-model ({exec,insts,decode,state}.sail): same error
codes, same step accounting, reserved bits enforced. Table entries are
masked to the declared widths, matching the emulator's load behavior
and interp_map. Records a full per-step trace including window-write
and lookup events.
"""

from dataclasses import dataclass

# Mirror of spec/map-model/params.sail
HEADROOM_BYTES = 32
BUF_BYTES = 256
WIN_BYTES = 288
N_PORTS = 4
N_TABLES = 4
IMEM_WORDS = 1024
STEP_BUDGET = 256

# Mirror of spec/map-model/state.sail
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
class MapIssStep:
    """One executed instruction: pc before, registers after, effects."""

    pc: int
    line: int | None
    regs: tuple[int, int, int, int]
    writes: tuple[tuple[int, bytes], ...]
    lookup: tuple[int, int, bool, int] | None


@dataclass(frozen=True)
class MapIssResult:
    """First six fields mirror tests.support.map_harness.MapResult."""

    verdict: int
    error: int
    egress: int
    delta: int
    steps: int
    frame: bytes | None
    trace: list[MapIssStep]


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
    reserved-bit masks mirror spec/map-model/decode.sail."""
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
        case 0x0A:  # CSUMUPD hdr(4), off(10 signed)
            if w & 0x0FFF:
                return None
            return ("csumupd", (w >> 22) & 0xF, _sext10((w >> 12) & 0x3FF))
        case 0x0B:  # SEND rs, delta(10 signed)
            if w & 0x1FFF or r1 > 4:
                return None
            return ("send", r1, _sext10((w >> 13) & 0x3FF))
        case 0x0C:  # DROP
            if w & 0x03FFFFFF:
                return None
            return ("drop",)
        case _:
            return None


class _Machine:
    def __init__(self, words, packet: bytes, pp, tables, ingress: int, line_map=None):
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
        self.ingress = ingress
        self.regs = [0, 0, 0, 0]
        self.pc = 0
        self.steps = 0
        self.halted = False
        self.verdict = VERDICT_SENT
        self.err = ERR_NONE
        self.egress = 0
        self.delta = 0
        self.trace: list[MapIssStep] = []
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
        # Mirrors step() in spec/map-model/exec.sail.
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
            MapIssStep(
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
                self.write_reg(rd, self._ld_field(f))
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
            case ("csumupd", h, off):
                self._csumupd(h, off)
            case ("send", rs, d):
                if d > HEADROOM_BYTES or d <= -self.plen_min:
                    self.raise_err(ERR_SEND_RANGE)
                    return
                self.egress = self.read_reg(rs) & ((1 << N_PORTS) - 1)
                self.delta = d
                self.verdict = VERDICT_SENT
                self.halted = True
            case ("drop",):
                self.verdict = VERDICT_DROP
                self.halted = True

    def _ld_field(self, f: int) -> int:
        # Mirrors ld_field in spec/map-model/insts.sail.
        if f < 8:
            return self.pp.smd[f]
        if f == 8:
            return self.ingress
        if f == 9:
            all_ports = (1 << N_PORTS) - 1
            return (all_ports ^ (1 << self.ingress)) & all_ports
        if f == 10:
            return sum(1 << h for h in range(16) if self.pp.hdr_present[h])
        return 0

    def _csumupd(self, h: int, off: int) -> None:
        base = self._checked_addr(h, off, 20)
        if base is None:
            return
        ihl = self.window[base] & 0xF
        if ihl < 5:
            self.raise_err(ERR_WINDOW_VIOLATION)
            return
        hlen = ihl * 4
        if base + hlen > self.win_limit:
            self.raise_err(ERR_WINDOW_VIOLATION)
            return
        total = 0
        for i in range(0, hlen, 2):
            b0 = 0 if i == 10 else self.window[base + i]
            b1 = 0 if i == 10 else self.window[base + i + 1]
            total += (b0 << 8) | b1
        while total > 0xFFFF:
            total = (total & 0xFFFF) + (total >> 16)
        ck = total ^ 0xFFFF
        self.write_win(base + 10, bytes([ck >> 8, ck & 0xFF]))


def run_map_iss(
    prog: bytes,
    packet: bytes,
    pp,
    tables,
    ingress: int,
    *,
    line_map: list[int] | None = None,
) -> MapIssResult:
    """Run one already-parsed frame through the MAP ISS. Total, like the ISA.

    pp: ParseResult-shaped (hdr_present/hdr_offset/smd). tables: list of
    Table-shaped objects (key_width/action_width/entries), index = table
    id. prog: big-endian 32-bit words.
    """
    if len(prog) % 4:
        raise ValueError("program length is not a multiple of 4 bytes")
    words = [int.from_bytes(prog[i : i + 4], "big") for i in range(0, len(prog), 4)]
    m = _Machine(words, bytes(packet), pp, tables, ingress, line_map)
    while not m.halted:
        m.step()
    frame = None
    if m.verdict == VERDICT_SENT:
        start = HEADROOM_BYTES - m.delta
        frame = bytes(m.window[start : HEADROOM_BYTES + m.plen_min]) + bytes(
            packet[BUF_BYTES:]
        )
    return MapIssResult(
        verdict=m.verdict,
        error=m.err,
        egress=m.egress,
        delta=m.delta,
        steps=m.steps,
        frame=frame,
        trace=m.trace,
    )
