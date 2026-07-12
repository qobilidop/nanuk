"""NanukCore: Amaranth RTL for the nanuk parser ISA v0.

The Sail model (spec/sail/model/parser/*.sail) is the single source of truth; this core
reproduces its semantics bit-for-bit:

- fetch order per exec.sail step(): step budget first, then pc range, then
  decode (illegal), then execute;
- all five error codes (state.sail): 1 hdr_violation, 2 step_budget,
  3 illegal, 4 pc_range, 5 smd_range; error halts set verdict = 2;
- bit 0 of a packet byte is its MSB (network order, state.sail
  read_pkt_bits);
- steps counts *executed* instructions (including a faulting illegal one),
  and equals the budget (256) on watchdog exhaustion.

Interface contract per docs/superpowers/plans/2026-07-11-stage4-rtl-simbricks.md.
One instruction takes one EXEC cycle; a 2-state FETCH/EXEC loop covers the
synchronous imem read port (steps counts EXEC cycles, not clock cycles).
"""

from amaranth import Array, C, Cat, Module, Mux, Signal
from amaranth.lib import memory, wiring
from amaranth.lib.wiring import In, Out

# Implementation parameters (mirror spec/sail/model/parser/params.sail).
BUF_BYTES = 256
IMEM_WORDS = 1024
NHDR = 16
SMD_SLOTS = 8
STEP_BUDGET = 256

# Verdicts (mirror spec/sail/model/parser/state.sail).
VERDICT_ACCEPT = 0x00
VERDICT_DROP = 0x01
VERDICT_ERROR = 0x02

# Error codes (mirror spec/sail/model/parser/state.sail).
ERR_NONE = 0x00
ERR_HDR_VIOLATION = 0x01
ERR_STEP_BUDGET = 0x02
ERR_ILLEGAL = 0x03
ERR_PC_RANGE = 0x04
ERR_SMD_RANGE = 0x05

# Opcodes (mirror spec/sail/model/parser/decode.sail).
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

# FSM states.
_ST_IDLE = 0
_ST_FETCH = 1
_ST_EXEC = 2
_ST_EXT_ISSUE = 3
_ST_EXT_CAPTURE = 4


class NanukCore(wiring.Component):
    """nanuk parser core.

    Load the program via the imem write port and the packet via the buffer
    write port, present ``plen``, pulse ``start`` for one cycle, wait for
    ``done`` (level), then read the output contract. ``start`` clears the
    architectural state (regs, cursor, pc, hdr, smd, status, steps) but not
    the instruction memory or the packet buffer.
    """

    prog_we: In(1)
    prog_addr: In(10)
    prog_data: In(32)

    pkt_we: In(1)
    pkt_addr: In(8)
    pkt_data: In(8)

    plen: In(16)
    start: In(1)

    done: Out(1)
    verdict: Out(8)
    error: Out(8)
    payload_offset: Out(16)
    steps: Out(32)
    hdr_present: Out(NHDR)
    hdr_offset: Out(16 * NHDR)
    smd: Out(16 * SMD_SLOTS)

    def __init__(self):
        super().__init__()
        # Architectural state, created here so simulations can peek at it.
        self.regs = [Signal(64, name=f"reg{i}") for i in range(4)]
        self.cursor = Signal(16)
        self.pc = Signal(16)

    def elaborate(self, platform):
        m = Module()

        # --- Instruction memory: 1024 x 32, sync read + sync write port. --
        m.submodules.imem = imem = memory.Memory(
            shape=32, depth=IMEM_WORDS, init=[]
        )
        wp = imem.write_port()
        rp = imem.read_port()
        m.d.comb += [
            wp.addr.eq(self.prog_addr),
            wp.data.eq(self.prog_data),
            wp.en.eq(self.prog_we),
        ]

        # --- Packet buffer: 256 x 8 memory. EXT reads it sequentially, one
        # byte per read cycle (the Sail read_pkt_bits algorithm laid out in
        # time) — a combinational 2048-bit extraction datapath is both
        # unrealistic hardware and pathological for Verilator's C output. ---
        m.submodules.pktmem = pktmem = memory.Memory(
            shape=8, depth=BUF_BYTES, init=[]
        )
        pwp = pktmem.write_port()
        prp = pktmem.read_port()
        m.d.comb += [
            pwp.addr.eq(self.pkt_addr),
            pwp.data.eq(self.pkt_data),
            pwp.en.eq(self.pkt_we),
        ]

        # --- Architectural state ------------------------------------------
        regs_arr = Array(self.regs)
        cursor = self.cursor
        pc = self.pc
        plen_r = Signal(16)
        hdr_present_r = Signal(NHDR)
        hdr_off = [Signal(16, name=f"hdr_off{i}") for i in range(NHDR)]
        hdr_off_arr = Array(hdr_off)
        smd_r = [Signal(16, name=f"smd{i}") for i in range(SMD_SLOTS)]
        smd_arr = Array(smd_r)
        steps_r = Signal(32)
        verdict_r = Signal(8)
        err_r = Signal(8)
        done_r = Signal(1)
        state = Signal(3, init=_ST_IDLE)

        # EXT sequential-read bookkeeping (valid during _ST_EXT_*).
        ext_rd_r = Signal(3)       # destination register field
        ext_szm1_r = Signal(6)     # size - 1
        ext_base_r = Signal(8)     # first byte address
        ext_i_r = Signal(4)        # bytes read so far
        ext_n_r = Signal(4)        # bytes needed (<= 9)
        ext_shr_r = Signal(3)      # final right-shift (0..7)
        ext_acc_r = Signal(72)     # MSB-first byte accumulator

        m.d.comb += [
            self.done.eq(done_r),
            self.verdict.eq(verdict_r),
            self.error.eq(err_r),
            self.payload_offset.eq(cursor),
            self.steps.eq(steps_r),
            self.hdr_present.eq(hdr_present_r),
            self.hdr_offset.eq(Cat(*hdr_off)),
            self.smd.eq(Cat(*smd_r)),
        ]

        # Header parse boundary: hdr_limit = min(plen, BUF_BYTES).
        hdr_limit = Signal(range(BUF_BYTES + 1))
        m.d.comb += hdr_limit.eq(
            Mux(plen_r < BUF_BYTES, plen_r, BUF_BYTES)
        )

        # --- Fetch ---------------------------------------------------------
        m.d.comb += [rp.addr.eq(pc), rp.en.eq(1)]
        word = rp.data

        # --- Decode fields (positions per spec/sail/model/parser/decode.sail) ----------
        opcode = word[26:32]
        f_ra = word[23:26]      # rd/rs at [25:23] (EXT/ADVR/MOVI/SHL/B*/STMD)
        f_rb = word[20:23]      # rs/rt at [22:20] (SHL/BEQ/BNE)
        imm16 = word[0:16]      # imm / branch & jump target
        ext_boff = word[12:23]  # EXT bit offset (11 bits)
        ext_szm1 = word[6:12]   # EXT size-1 (6 bits)
        shl_sh = word[14:20]    # SHL shift amount (6 bits)
        hdr_id = word[0:4]      # SETHDR header id
        stmd_nm1 = word[21:23]  # STMD nunits-1
        stmd_slot = word[17:21] # STMD slot

        def reg_ok(f):
            # Register codes 0-4 decode (4 = RZ); 5-7 are ILLEGAL.
            return f <= 4

        def reg_read(f):
            # RZ (and only RZ among codes with bit 2 set) reads as zero.
            return Mux(f[2], 0, regs_arr[f[0:2]])

        def reg_write(f, value):
            # Writes to RZ are discarded.
            with m.If(~f[2]):
                m.d.sync += regs_arr[f[0:2]].eq(value)

        def halt_error(code):
            m.d.sync += [
                verdict_r.eq(VERDICT_ERROR),
                err_r.eq(code),
                done_r.eq(1),
                state.eq(_ST_IDLE),
            ]

        # --- EXT address math (combinational; the reads happen over the
        # _ST_EXT_ISSUE/_ST_EXT_CAPTURE cycles, per Sail's read_pkt_bits). --
        ext_pos = Signal(20)     # cursor*8 (<= 2048) + boff (<= 2047)
        ext_end = Signal(21)     # pos + size
        ext_size = Signal(7)     # 1..64
        ext_bib = Signal(3)      # bit-in-byte of pos
        ext_nbytes = Signal(4)   # ceil((bib + size) / 8) <= 9
        m.d.comb += [
            ext_pos.eq((cursor << 3) + ext_boff),
            ext_size.eq(ext_szm1 + 1),
            ext_end.eq(ext_pos + ext_size),
            ext_bib.eq(ext_pos[0:3]),
            ext_nbytes.eq((ext_bib + ext_size + 7) >> 3),
        ]

        # Cursor advance (shared by ADVI/ADVR).
        adv_amount = Signal(16)
        adv_next = Signal(17)
        m.d.comb += [
            adv_amount.eq(
                Mux(opcode == OP_ADVI, imm16, reg_read(f_ra)[0:16])
            ),
            adv_next.eq(cursor + adv_amount),
        ]

        # --- Control -------------------------------------------------------
        with m.If(self.start):
            # Clear architectural state; imem and packet buffer persist.
            m.d.sync += [r.eq(0) for r in self.regs]
            m.d.sync += [h.eq(0) for h in hdr_off]
            m.d.sync += [s.eq(0) for s in smd_r]
            m.d.sync += [
                cursor.eq(0),
                pc.eq(0),
                hdr_present_r.eq(0),
                steps_r.eq(0),
                verdict_r.eq(VERDICT_ACCEPT),
                err_r.eq(ERR_NONE),
                done_r.eq(0),
                plen_r.eq(self.plen),
                state.eq(_ST_FETCH),
            ]
        with m.Elif(state == _ST_FETCH):
            # Order of checks per exec.sail step(): budget, pc range, fetch.
            with m.If(steps_r >= STEP_BUDGET):
                halt_error(ERR_STEP_BUDGET)
            with m.Elif(pc >= IMEM_WORDS):
                halt_error(ERR_PC_RANGE)
            with m.Else():
                m.d.sync += state.eq(_ST_EXEC)
        with m.Elif(state == _ST_EXEC):
            # Defaults: count the executed instruction, advance pc, refetch.
            # Branch/halt/error cases below override pc/state as needed.
            m.d.sync += [
                steps_r.eq(steps_r + 1),
                pc.eq(pc + 1),
                state.eq(_ST_FETCH),
            ]

            illegal = Signal(1, name="illegal")
            m.d.comb += illegal.eq(1)  # overridden by legal encodings below

            with m.Switch(opcode):
                with m.Case(OP_EXT):
                    with m.If((word[0:6] == 0) & reg_ok(f_ra)):
                        m.d.comb += illegal.eq(0)
                        with m.If(ext_end > (hdr_limit << 3)):
                            halt_error(ERR_HDR_VIOLATION)
                        with m.Else():
                            # steps/pc already counted above (Sail increments
                            # before execute); read bytes over the next
                            # cycles, then retire in _ST_EXT_CAPTURE.
                            m.d.sync += [
                                ext_rd_r.eq(f_ra),
                                ext_szm1_r.eq(ext_szm1),
                                ext_base_r.eq(ext_pos >> 3),
                                ext_i_r.eq(0),
                                ext_n_r.eq(ext_nbytes),
                                ext_shr_r.eq(
                                    (ext_nbytes << 3) - ext_bib - ext_size
                                ),
                                ext_acc_r.eq(0),
                                state.eq(_ST_EXT_ISSUE),
                            ]

                with m.Case(OP_ADVI):
                    with m.If(word[16:26] == 0):
                        m.d.comb += illegal.eq(0)
                        with m.If(adv_next > hdr_limit):
                            halt_error(ERR_HDR_VIOLATION)
                        with m.Else():
                            m.d.sync += cursor.eq(adv_next)

                with m.Case(OP_ADVR):
                    with m.If((word[0:23] == 0) & reg_ok(f_ra)):
                        m.d.comb += illegal.eq(0)
                        with m.If(adv_next > hdr_limit):
                            halt_error(ERR_HDR_VIOLATION)
                        with m.Else():
                            m.d.sync += cursor.eq(adv_next)

                with m.Case(OP_MOVI):
                    with m.If((word[16:23] == 0) & reg_ok(f_ra)):
                        m.d.comb += illegal.eq(0)
                        reg_write(f_ra, imm16)  # zero-extends

                with m.Case(OP_SHL):
                    with m.If(
                        (word[0:14] == 0) & reg_ok(f_ra) & reg_ok(f_rb)
                    ):
                        m.d.comb += illegal.eq(0)
                        # 64-bit left shift; assignment truncates at 64.
                        reg_write(f_ra, reg_read(f_rb) << shl_sh)

                with m.Case(OP_BEQ, OP_BNE):
                    with m.If(
                        (word[16:20] == 0) & reg_ok(f_ra) & reg_ok(f_rb)
                    ):
                        m.d.comb += illegal.eq(0)
                        eq = reg_read(f_ra) == reg_read(f_rb)
                        taken = Mux(opcode == OP_BEQ, eq, ~eq)
                        with m.If(taken):
                            m.d.sync += pc.eq(imm16)

                with m.Case(OP_JMP):
                    with m.If(word[16:26] == 0):
                        m.d.comb += illegal.eq(0)
                        m.d.sync += pc.eq(imm16)

                with m.Case(OP_SETHDR):
                    with m.If(word[4:26] == 0):
                        m.d.comb += illegal.eq(0)
                        m.d.sync += [
                            hdr_present_r.bit_select(hdr_id, 1).eq(1),
                            hdr_off_arr[hdr_id].eq(cursor),
                        ]

                with m.Case(OP_STMD):
                    with m.If((word[0:17] == 0) & reg_ok(f_ra)):
                        m.d.comb += illegal.eq(0)
                        with m.If(
                            stmd_slot + stmd_nm1 + 1 > SMD_SLOTS
                        ):
                            halt_error(ERR_SMD_RANGE)
                        with m.Else():
                            value = reg_read(f_ra)
                            with m.Switch(stmd_nm1):
                                for nm1 in range(4):
                                    with m.Case(nm1):
                                        n = nm1 + 1
                                        for i in range(n):
                                            # MSB-first across the slots.
                                            lo = (n - 1 - i) * 16
                                            m.d.sync += smd_arr[
                                                stmd_slot + i
                                            ].eq(value[lo:lo + 16])

                with m.Case(OP_HALT):
                    with m.If(word[1:26] == 0):
                        m.d.comb += illegal.eq(0)
                        m.d.sync += [
                            verdict_r.eq(
                                Mux(word[0], VERDICT_DROP, VERDICT_ACCEPT)
                            ),
                            done_r.eq(1),
                            state.eq(_ST_IDLE),
                        ]

            with m.If(illegal):
                # Any unassigned pattern, nonzero required-zero field, or
                # register code 5-7 (including the all-zeros word).
                halt_error(ERR_ILLEGAL)

        with m.Elif(state == _ST_EXT_ISSUE):
            m.d.comb += [
                prp.addr.eq(ext_base_r + ext_i_r),
                prp.en.eq(1),
            ]
            m.d.sync += state.eq(_ST_EXT_CAPTURE)

        with m.Elif(state == _ST_EXT_CAPTURE):
            next_acc = Signal(72)
            m.d.comb += next_acc.eq((ext_acc_r << 8) | prp.data)
            with m.If(ext_i_r + 1 == ext_n_r):
                # Last byte: align and mask, then retire the instruction.
                aligned = Signal(72)
                mask_sh = Signal(6)
                mask = Signal(64)
                m.d.comb += [
                    aligned.eq(next_acc >> ext_shr_r),
                    # size low ones: 64 ones shifted right by (64 - size).
                    mask_sh.eq(63 - ext_szm1_r),
                    mask.eq(C(0xFFFF_FFFF_FFFF_FFFF, 64) >> mask_sh),
                ]
                reg_write(ext_rd_r, aligned[0:64] & mask)
                m.d.sync += state.eq(_ST_FETCH)
            with m.Else():
                m.d.sync += [
                    ext_acc_r.eq(next_acc),
                    ext_i_r.eq(ext_i_r + 1),
                    state.eq(_ST_EXT_ISSUE),
                ]

        return m
