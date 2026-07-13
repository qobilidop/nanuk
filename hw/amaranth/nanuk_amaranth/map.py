"""MatchActionProcessor: Amaranth RTL for the Nanuk MAP ISA v0.

The Sail model (spec/sail/model/map/*.sail) is the single source of truth; this
core reproduces its semantics bit-for-bit:

- fetch order per exec.sail step(): step budget first, then pc range, then
  decode (illegal), then execute;
- all six error codes (state.sail): 1 window_violation, 2 step_budget,
  3 illegal, 4 pc_range, 5 hdr_absent, 6 send_range; error halts set
  verdict = 2 (verdicts: 0 sent, 1 drop, 2 error);
- steps counts *executed* instructions and equals the budget (256) on
  watchdog exhaustion.

Sibling of ParserProcessor (core.py) — same FETCH/EXEC FSM shape, and the same
EXT lesson applied throughout: every window/table access is a sequential
loop over a memory port, never a wide combinational datapath.

`start` clears architectural state (regs, pc, steps, status, delta) and
loads the metadata window from md_in, but NOT imem, the frame window, or
the tables — drivers must fill the whole 288-byte window (headroom zeros
+ frame + padding) per packet. md_out presents the metadata window's
live value (the output contract once done).
"""

from amaranth import Array, C, Cat, Module, Mux, Signal, signed
from amaranth.lib import memory, wiring
from amaranth.lib.wiring import In, Out

# Implementation parameters (mirror spec/sail/model/map/params.sail).
HEADROOM_BYTES = 32
BUF_BYTES = 256
WIN_BYTES = 288
IMEM_WORDS = 1024
N_TABLES = 4
TABLE_MAX_ENTRIES = 64
MD_SLOTS = 8
NHDR = 16
STEP_BUDGET = 256

# Verdicts (mirror spec/sail/model/map/state.sail).
VERDICT_SENT = 0x00
VERDICT_DROP = 0x01
VERDICT_ERROR = 0x02

# Error codes (mirror spec/sail/model/map/state.sail).
ERR_NONE = 0x00
ERR_WINDOW_VIOLATION = 0x01
ERR_STEP_BUDGET = 0x02
ERR_ILLEGAL = 0x03
ERR_PC_RANGE = 0x04
ERR_HDR_ABSENT = 0x05
ERR_SEND_RANGE = 0x06

# Opcodes (mirror spec/sail/model/map/decode.sail).
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
# v0.1: register-register ALU (the calculator benchmark).
OP_ADD = 0x10
OP_SUB = 0x11
OP_AND = 0x12
OP_OR = 0x13
OP_XOR = 0x14

# FSM states.
_ST_IDLE = 0
_ST_FETCH = 1
_ST_EXEC = 2
_ST_LD_ISSUE = 3
_ST_LD_CAPTURE = 4
_ST_STORE = 5
_ST_LKP_ISSUE = 6
_ST_LKP_SCAN = 7
_ST_CSUM_ISSUE = 8
_ST_CSUM_CAPTURE = 9


class MatchActionProcessor(wiring.Component):
    """Nanuk match-action core.

    Load the program via the imem write port, the window via the window
    write port (window index 0..287 — headroom included), tables via the
    control-plane ports, present plen/md_in/hdr_*_in, pulse ``start``,
    wait for ``done``, then read the outbound contract (md_out carries the
    metadata window); when verdict = 0 (sent), stream the frame out
    through win_rd_addr/win_rd_data (sync read: data valid the cycle
    after addr).
    """

    prog_we: In(1)
    prog_addr: In(10)
    prog_data: In(32)

    win_we: In(1)
    win_addr: In(9)
    win_data: In(8)

    plen: In(16)
    md_in: In(16 * MD_SLOTS)
    hdr_present_in: In(NHDR)
    hdr_offset_in: In(16 * NHDR)

    tbl_cfg_we: In(1)
    tbl_cfg_id: In(2)
    tbl_cfg_kw: In(8)
    tbl_cfg_aw: In(8)
    tbl_add_we: In(1)
    tbl_add_id: In(2)
    tbl_add_key: In(64)
    tbl_add_action: In(64)

    start: In(1)

    done: Out(1)
    verdict: Out(8)
    error: Out(8)
    md_out: Out(16 * MD_SLOTS)
    delta: Out(signed(16))
    steps: Out(32)

    win_rd_addr: In(9)
    win_rd_data: Out(8)

    def __init__(self, winmem=None):
        """winmem: an amaranth.lib.memory.Memory (depth WIN_BYTES) to use as
        the window instead of an internal one — the composed NanukCore owns
        the window so the PP can read the same bytes in place. Ports are
        created now (pre-elaboration), as in ParserProcessor."""
        super().__init__()
        self._winmem = winmem
        self._ext_wwp = None if winmem is None else winmem.write_port()
        self._ext_wrp = None if winmem is None else winmem.read_port()
        # Architectural state, created here so simulations can peek at it.
        self.regs = [Signal(64, name=f"reg{i}") for i in range(4)]
        self.pc = Signal(16)

    def elaborate(self, platform):
        m = Module()

        # --- Instruction memory: 1024 x 32, sync read + write port. --------
        m.submodules.imem = imem = memory.Memory(shape=32, depth=IMEM_WORDS, init=[])
        iwp = imem.write_port()
        irp = imem.read_port()
        m.d.comb += [
            iwp.addr.eq(self.prog_addr),
            iwp.data.eq(self.prog_data),
            iwp.en.eq(self.prog_we),
        ]

        # --- Window: 288 x 8. One write port (driver load / ST / CSUM
        # write-back, muxed by state) and one read port (LD / CSUM reads
        # while running; frame readback when done). ---
        if self._winmem is None:
            m.submodules.winmem = winmem = memory.Memory(
                shape=8, depth=WIN_BYTES, init=[]
            )
            wwp = winmem.write_port()
            wrp = winmem.read_port()
        else:
            wwp = self._ext_wwp
            wrp = self._ext_wrp

        # --- Tables: 256 x 128 ({action[127:64], key[63:0]}), address =
        # {tbl_id(2), idx(6)}. Config/count registers per table. ---
        m.submodules.tblmem = tblmem = memory.Memory(shape=128, depth=256, init=[])
        twp = tblmem.write_port()
        trp = tblmem.read_port()

        tbl_kw = [Signal(8, name=f"tbl{i}_kw") for i in range(N_TABLES)]
        tbl_aw = [Signal(8, name=f"tbl{i}_aw") for i in range(N_TABLES)]
        tbl_count = [Signal(7, name=f"tbl{i}_count") for i in range(N_TABLES)]

        with m.If(self.tbl_cfg_we):
            with m.Switch(self.tbl_cfg_id):
                for t in range(N_TABLES):
                    with m.Case(t):
                        m.d.sync += [
                            tbl_kw[t].eq(self.tbl_cfg_kw),
                            tbl_aw[t].eq(self.tbl_cfg_aw),
                            tbl_count[t].eq(0),
                        ]
        add_count = Signal(7)
        with m.Switch(self.tbl_add_id):
            for t in range(N_TABLES):
                with m.Case(t):
                    m.d.comb += add_count.eq(tbl_count[t])
        m.d.comb += [
            twp.addr.eq(Cat(add_count[0:6], self.tbl_add_id)),
            twp.data.eq(Cat(self.tbl_add_key, self.tbl_add_action)),
            twp.en.eq(self.tbl_add_we & (add_count < TABLE_MAX_ENTRIES)),
        ]
        with m.If(self.tbl_add_we):
            with m.Switch(self.tbl_add_id):
                for t in range(N_TABLES):
                    with m.Case(t):
                        with m.If(tbl_count[t] < TABLE_MAX_ENTRIES):
                            m.d.sync += tbl_count[t].eq(tbl_count[t] + 1)

        # --- Architectural state -------------------------------------------
        regs_arr = Array(self.regs)
        pc = self.pc
        plen_r = Signal(16)
        steps_r = Signal(32)
        verdict_r = Signal(8)
        err_r = Signal(8)
        delta_r = Signal(signed(16))
        done_r = Signal(1)
        state = Signal(4, init=_ST_IDLE)

        # The metadata window: loaded from md_in at start, LDMD-read,
        # STMD-written, presented on md_out whatever the verdict.
        md_r = [Signal(16, name=f"md{i}") for i in range(MD_SLOTS)]
        md_arr = Array(md_r)

        # Memory-op bookkeeping (LD/ST/CSUM share the byte counter).
        mem_rd_r = Signal(3)          # destination register field (LD/LOOKUP)
        mem_addr_r = Signal(9)        # current window byte address
        mem_i_r = Signal(9)           # bytes processed
        mem_n_r = Signal(7)           # bytes total (LD/ST <= 8)
        mem_acc_r = Signal(64)        # LD accumulator
        mem_len_r = Signal(16)        # CSUM byte length (from a register)
        st_val_r = Signal(64)         # ST source value
        lkp_key_r = Signal(64)        # LOOKUP masked key
        lkp_tbl_r = Signal(2)         # LOOKUP table id
        lkp_tgt_r = Signal(16)        # LOOKUP miss target
        lkp_i_r = Signal(7)           # LOOKUP scan index
        lkp_n_r = Signal(7)           # LOOKUP entry count snapshot
        lkp_aw_r = Signal(8)          # LOOKUP action width snapshot
        csum_rd_r = Signal(3)         # CSUM destination register field
        csum_sum_r = Signal(25)       # CSUM running sum (<= 144 * 0xFFFF)

        m.d.comb += [
            self.done.eq(done_r),
            self.verdict.eq(verdict_r),
            self.error.eq(err_r),
            self.md_out.eq(Cat(*md_r)),
            self.delta.eq(delta_r),
            self.steps.eq(steps_r),
        ]

        # Valid window limit: HEADROOM + min(plen, BUF).
        plen_min = Signal(range(BUF_BYTES + 1))
        win_limit = Signal(range(WIN_BYTES + 1))
        m.d.comb += [
            plen_min.eq(Mux(plen_r < BUF_BYTES, plen_r, BUF_BYTES)),
            win_limit.eq(HEADROOM_BYTES + plen_min),
        ]

        # --- Fetch ----------------------------------------------------------
        m.d.comb += [irp.addr.eq(pc), irp.en.eq(1)]
        word = irp.data

        # --- Decode fields (positions per spec/sail/model/map/decode.sail) -------
        opcode = word[26:32]
        f_ra = word[23:26]        # rd/rs at [25:23]
        f_rb = word[20:23]        # rs/rt at [22:20] (ADDI/BEQ/BNE)
        f_rc = word[16:19]        # LOOKUP key register at [18:16]
        imm16 = word[0:16]        # imm / branch & jump / miss target
        f_hdr = word[19:23]       # LD/ST/LDMD hdr or field id at [22:19]
        f_off = word[9:19]        # LD/ST byte offset (10b two's complement)
        f_nm1 = word[6:9]         # LD/ST nbytes-1
        f_tbl = word[19:23]       # LOOKUP table id at [22:19]
        cs_rl = word[6:9]         # CSUM length register at [8:6]
        sd_delta = word[13:23]    # SEND delta (10b two's complement)
        st_nm1 = word[21:23]      # STMD nunits-1
        st_slot = word[17:21]     # STMD slot
        shl_sh = word[14:20]      # SHLI shift amount (6 bits)
        f_rt = word[17:20]        # reg-reg ALU third register at [19:17]

        def reg_ok(f):
            return f <= 4

        def reg_read(f):
            return Mux(f[2], 0, regs_arr[f[0:2]])

        def reg_write(f, value):
            with m.If(~f[2]):
                m.d.sync += regs_arr[f[0:2]].eq(value)

        def halt_error(code):
            m.d.sync += [
                verdict_r.eq(VERDICT_ERROR),
                err_r.eq(code),
                done_r.eq(1),
                state.eq(_ST_IDLE),
            ]

        # --- Header-relative effective address (LD/ST/CSUM) ----------------
        # base(hdr): h_frame (15) -> 0 always; else PP hdr_offset, absent -> err 5.
        hdr_present_bit = Signal(1)
        hdr_base_val = Signal(16)
        hdr_sel = Signal(4)
        m.d.comb += hdr_sel.eq(f_hdr)
        m.d.comb += [
            hdr_present_bit.eq(self.hdr_present_in.bit_select(hdr_sel, 1)),
            hdr_base_val.eq(
                self.hdr_offset_in.word_select(hdr_sel, 16)
            ),
        ]
        hdr_is_frame = Signal(1)
        hdr_absent = Signal(1)
        m.d.comb += [
            hdr_is_frame.eq(hdr_sel == 15),
            hdr_absent.eq(~hdr_is_frame & ~hdr_present_bit),
        ]
        eff_off = Signal(signed(11))
        m.d.comb += eff_off.eq(f_off.as_signed())
        # addr = 32 + base + off; signed 18-bit intermediate covers all cases.
        eff_addr = Signal(signed(18))
        m.d.comb += eff_addr.eq(
            HEADROOM_BYTES + Mux(hdr_is_frame, 0, hdr_base_val) + eff_off
        )

        # --- SEND range math -------------------------------------------------
        sd_val = Signal(signed(11))
        m.d.comb += sd_val.eq(sd_delta.as_signed())
        # NOT plen_min.as_signed(): that reinterprets 256 (9 bits) as -256.
        neg_plen = Signal(signed(11))
        m.d.comb += neg_plen.eq(-plen_min)
        send_bad = Signal(1)
        m.d.comb += send_bad.eq(
            (sd_val > HEADROOM_BYTES) | (sd_val <= neg_plen)
        )


        # --- Window write/read port muxing -----------------------------------
        # Write port: driver load (any state), ST bytes, CSUM write-back.
        st_byte = Signal(8)
        with m.Switch(mem_n_r - mem_i_r - 1):
            for sh in range(8):
                with m.Case(sh):
                    m.d.comb += st_byte.eq(st_val_r[8 * sh : 8 * sh + 8])

        with m.If(self.win_we):
            m.d.comb += [
                wwp.addr.eq(self.win_addr),
                wwp.data.eq(self.win_data),
                wwp.en.eq(1),
            ]
        with m.Elif(state == _ST_STORE):
            m.d.comb += [
                wwp.addr.eq(mem_addr_r),
                wwp.data.eq(st_byte),
                wwp.en.eq(1),
            ]

        # Read port: LD/CSUM byte streaming while running; frame readback
        # whenever the core is idle/done.
        with m.If((state == _ST_LD_ISSUE) | (state == _ST_CSUM_ISSUE)):
            m.d.comb += [wrp.addr.eq(mem_addr_r), wrp.en.eq(1)]
        with m.Else():
            m.d.comb += [wrp.addr.eq(self.win_rd_addr), wrp.en.eq(1)]
        m.d.comb += self.win_rd_data.eq(wrp.data)

        # Table read port (LOOKUP scan).
        m.d.comb += [
            trp.addr.eq(Cat(lkp_i_r[0:6], lkp_tbl_r)),
            trp.en.eq(1),
        ]
        ent_key = trp.data[0:64]
        ent_action = trp.data[64:128]

        # --- Control ----------------------------------------------------------
        with m.If(self.start):
            m.d.sync += [r.eq(0) for r in self.regs]
            m.d.sync += [
                md_r[i].eq(self.md_in.word_select(C(i, 3), 16))
                for i in range(MD_SLOTS)
            ]
            m.d.sync += [
                pc.eq(0),
                steps_r.eq(0),
                verdict_r.eq(VERDICT_SENT),
                err_r.eq(ERR_NONE),
                delta_r.eq(0),
                done_r.eq(0),
                plen_r.eq(self.plen),
                state.eq(_ST_FETCH),
            ]

        with m.Elif(state == _ST_FETCH):
            with m.If(steps_r >= STEP_BUDGET):
                halt_error(ERR_STEP_BUDGET)
            with m.Elif(pc >= IMEM_WORDS):
                halt_error(ERR_PC_RANGE)
            with m.Else():
                m.d.sync += state.eq(_ST_EXEC)

        with m.Elif(state == _ST_EXEC):
            m.d.sync += [
                steps_r.eq(steps_r + 1),
                pc.eq(pc + 1),
                state.eq(_ST_FETCH),
            ]

            illegal = Signal(1, name="illegal")
            m.d.comb += illegal.eq(1)

            with m.Switch(opcode):
                with m.Case(OP_LD, OP_ST):
                    with m.If((word[0:6] == 0) & reg_ok(f_ra)):
                        m.d.comb += illegal.eq(0)
                        nbytes = Signal(4)
                        m.d.comb += nbytes.eq(f_nm1 + 1)
                        with m.If(hdr_absent):
                            halt_error(ERR_HDR_ABSENT)
                        with m.Elif(
                            (eff_addr < 0) | (eff_addr + nbytes > win_limit)
                        ):
                            halt_error(ERR_WINDOW_VIOLATION)
                        with m.Else():
                            m.d.sync += [
                                mem_addr_r.eq(eff_addr),
                                mem_i_r.eq(0),
                                mem_n_r.eq(nbytes),
                            ]
                            with m.If(opcode == OP_LD):
                                m.d.sync += [
                                    mem_rd_r.eq(f_ra),
                                    mem_acc_r.eq(0),
                                    state.eq(_ST_LD_ISSUE),
                                ]
                            with m.Else():
                                m.d.sync += [
                                    st_val_r.eq(reg_read(f_ra)),
                                    state.eq(_ST_STORE),
                                ]

                with m.Case(OP_LDMD):
                    with m.If((word[0:19] == 0) & reg_ok(f_ra)):
                        m.d.comb += illegal.eq(0)
                        with m.If(f_hdr >= MD_SLOTS):
                            halt_error(ERR_ILLEGAL)
                        with m.Else():
                            reg_write(f_ra, md_arr[f_hdr[0:3]])

                with m.Case(OP_STMD):
                    with m.If((word[0:17] == 0) & reg_ok(f_ra)):
                        m.d.comb += illegal.eq(0)
                        with m.If(st_slot + st_nm1 + 1 > MD_SLOTS):
                            halt_error(ERR_ILLEGAL)
                        with m.Else():
                            value = reg_read(f_ra)
                            with m.Switch(st_nm1):
                                for nm1 in range(4):
                                    with m.Case(nm1):
                                        n = nm1 + 1
                                        for i in range(n):
                                            # MSB-first across the slots.
                                            lo = (n - 1 - i) * 16
                                            m.d.sync += md_arr[
                                                st_slot + i
                                            ].eq(value[lo:lo + 16])

                with m.Case(OP_ANDI):
                    with m.If((word[16:20] == 0) & reg_ok(f_ra) & reg_ok(f_rb)):
                        m.d.comb += illegal.eq(0)
                        reg_write(f_ra, reg_read(f_rb) & imm16)

                with m.Case(OP_SHLI):
                    with m.If((word[0:14] == 0) & reg_ok(f_ra) & reg_ok(f_rb)):
                        m.d.comb += illegal.eq(0)
                        # 64-bit left shift; assignment truncates at 64.
                        reg_write(f_ra, reg_read(f_rb) << shl_sh)

                with m.Case(OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR):
                    # rd [25:23], rs [22:20], rt [19:17]; [16:0] reserved.
                    # Total: every 64-bit pair has a result, so the only way to
                    # be illegal is a bad register code or dirty reserved bits.
                    with m.If(
                        (word[0:17] == 0)
                        & reg_ok(f_ra)
                        & reg_ok(f_rb)
                        & reg_ok(f_rt)
                    ):
                        m.d.comb += illegal.eq(0)
                        a = reg_read(f_rb)
                        b = reg_read(f_rt)
                        alu = Signal(64)
                        with m.Switch(opcode):
                            with m.Case(OP_ADD):
                                m.d.comb += alu.eq((a + b)[0:64])
                            with m.Case(OP_SUB):
                                m.d.comb += alu.eq((a - b)[0:64])
                            with m.Case(OP_AND):
                                m.d.comb += alu.eq(a & b)
                            with m.Case(OP_OR):
                                m.d.comb += alu.eq(a | b)
                            with m.Case(OP_XOR):
                                m.d.comb += alu.eq(a ^ b)
                        reg_write(f_ra, alu)

                with m.Case(OP_MOVI):
                    with m.If((word[16:23] == 0) & reg_ok(f_ra)):
                        m.d.comb += illegal.eq(0)
                        reg_write(f_ra, imm16)

                with m.Case(OP_ADDI):
                    with m.If((word[16:20] == 0) & reg_ok(f_ra) & reg_ok(f_rb)):
                        m.d.comb += illegal.eq(0)
                        reg_write(
                            f_ra,
                            (reg_read(f_rb).as_signed() + imm16.as_signed())[0:64],
                        )

                with m.Case(OP_BEQ, OP_BNE):
                    with m.If((word[16:20] == 0) & reg_ok(f_ra) & reg_ok(f_rb)):
                        m.d.comb += illegal.eq(0)
                        eq = reg_read(f_ra) == reg_read(f_rb)
                        taken = Mux(opcode == OP_BEQ, eq, ~eq)
                        with m.If(taken):
                            m.d.sync += pc.eq(imm16)

                with m.Case(OP_JMP):
                    with m.If(word[16:26] == 0):
                        m.d.comb += illegal.eq(0)
                        m.d.sync += pc.eq(imm16)

                with m.Case(OP_LOOKUP):
                    with m.If(reg_ok(f_ra) & reg_ok(f_rc)):
                        m.d.comb += illegal.eq(0)
                        # Key mask from the table's configured width.
                        kw = Signal(8)
                        cnt = Signal(7)
                        aw = Signal(8)
                        with m.Switch(f_tbl):
                            for t in range(N_TABLES):
                                with m.Case(t):
                                    m.d.comb += [
                                        kw.eq(tbl_kw[t]),
                                        cnt.eq(tbl_count[t]),
                                        aw.eq(tbl_aw[t]),
                                    ]
                            with m.Default():
                                pass  # kw = 0 -> always miss
                        kmask = Signal(64)
                        kw_clamp = Signal(7)
                        m.d.comb += [
                            kw_clamp.eq(Mux(kw > 64, 64, kw)),
                            kmask.eq(
                                Mux(
                                    kw_clamp >= 64,
                                    C(0xFFFF_FFFF_FFFF_FFFF, 64),
                                    (C(1, 65) << kw_clamp[0:7]) - 1,
                                )
                            ),
                        ]
                        with m.If((kw == 0) | (cnt == 0)):
                            # Unconfigured or empty: immediate miss.
                            reg_write(f_ra, 0)
                            m.d.sync += pc.eq(imm16)
                        with m.Else():
                            m.d.sync += [
                                mem_rd_r.eq(f_ra),
                                lkp_key_r.eq(reg_read(f_rc) & kmask),
                                lkp_tbl_r.eq(f_tbl[0:2]),
                                lkp_tgt_r.eq(imm16),
                                lkp_i_r.eq(0),
                                lkp_n_r.eq(cnt),
                                lkp_aw_r.eq(aw),
                                state.eq(_ST_LKP_ISSUE),
                            ]

                with m.Case(OP_CSUM):
                    with m.If((word[0:6] == 0) & reg_ok(f_ra) & reg_ok(cs_rl)):
                        m.d.comb += illegal.eq(0)
                        csum_len = Signal(16)
                        m.d.comb += csum_len.eq(reg_read(cs_rl)[0:16])
                        with m.If(hdr_absent):
                            halt_error(ERR_HDR_ABSENT)
                        with m.Elif(
                            (eff_addr < 0) | (eff_addr + csum_len > win_limit)
                        ):
                            halt_error(ERR_WINDOW_VIOLATION)
                        with m.Elif(csum_len == 0):
                            # Empty sum: ~0 = 0xFFFF, no memory walk.
                            reg_write(f_ra, C(0xFFFF, 64))
                        with m.Else():
                            m.d.sync += [
                                csum_rd_r.eq(f_ra),
                                mem_addr_r.eq(eff_addr),
                                mem_i_r.eq(0),
                                mem_len_r.eq(csum_len),
                                csum_sum_r.eq(0),
                                state.eq(_ST_CSUM_ISSUE),
                            ]

                with m.Case(OP_SEND):
                    with m.If((word[0:13] == 0) & (word[23:26] == 0)):
                        m.d.comb += illegal.eq(0)
                        with m.If(send_bad):
                            halt_error(ERR_SEND_RANGE)
                        with m.Else():
                            m.d.sync += [
                                delta_r.eq(sd_val),
                                verdict_r.eq(VERDICT_SENT),
                                done_r.eq(1),
                                state.eq(_ST_IDLE),
                            ]

                with m.Case(OP_DROP):
                    with m.If(word[0:26] == 0):
                        m.d.comb += illegal.eq(0)
                        m.d.sync += [
                            verdict_r.eq(VERDICT_DROP),
                            done_r.eq(1),
                            state.eq(_ST_IDLE),
                        ]

            with m.If(illegal):
                halt_error(ERR_ILLEGAL)

        # --- LD byte loop ----------------------------------------------------
        with m.Elif(state == _ST_LD_ISSUE):
            # Window read issued combinationally (read port muxed above).
            m.d.sync += state.eq(_ST_LD_CAPTURE)

        with m.Elif(state == _ST_LD_CAPTURE):
            next_acc = Signal(64)
            m.d.comb += next_acc.eq((mem_acc_r << 8) | wrp.data)
            with m.If(mem_i_r + 1 == mem_n_r):
                reg_write(mem_rd_r, next_acc)
                m.d.sync += state.eq(_ST_FETCH)
            with m.Else():
                m.d.sync += [
                    mem_acc_r.eq(next_acc),
                    mem_i_r.eq(mem_i_r + 1),
                    mem_addr_r.eq(mem_addr_r + 1),
                    state.eq(_ST_LD_ISSUE),
                ]

        # --- ST byte loop (one write per cycle; write port muxed above) ----
        with m.Elif(state == _ST_STORE):
            with m.If(mem_i_r + 1 == mem_n_r):
                m.d.sync += state.eq(_ST_FETCH)
            with m.Else():
                m.d.sync += [
                    mem_i_r.eq(mem_i_r + 1),
                    mem_addr_r.eq(mem_addr_r + 1),
                ]

        # --- LOOKUP scan loop -------------------------------------------------
        with m.Elif(state == _ST_LKP_ISSUE):
            # Table read issued combinationally (trp.addr wired above).
            m.d.sync += state.eq(_ST_LKP_SCAN)

        with m.Elif(state == _ST_LKP_SCAN):
            aw_clamp = Signal(7)
            amask = Signal(64)
            m.d.comb += [
                aw_clamp.eq(Mux(lkp_aw_r > 64, 64, lkp_aw_r)),
                amask.eq(
                    Mux(
                        aw_clamp >= 64,
                        C(0xFFFF_FFFF_FFFF_FFFF, 64),
                        (C(1, 65) << aw_clamp[0:7]) - 1,
                    )
                ),
            ]
            with m.If(ent_key == lkp_key_r):
                # Hit: action data (stored pre-masked; mask again for safety).
                reg_write(mem_rd_r, ent_action & amask)
                m.d.sync += state.eq(_ST_FETCH)
            with m.Elif(lkp_i_r + 1 >= lkp_n_r):
                # Miss after the last entry.
                reg_write(mem_rd_r, 0)
                m.d.sync += [pc.eq(lkp_tgt_r), state.eq(_ST_FETCH)]
            with m.Else():
                m.d.sync += [lkp_i_r.eq(lkp_i_r + 1), state.eq(_ST_LKP_ISSUE)]

        # --- CSUM loop -------------------------------------------------------
        # Generic RFC 1071 range sum: one byte per issue/capture pair; even
        # byte index weights <<8 (big-endian 16-bit words); an odd final
        # byte is high-weighted. Fold + complement land in the destination
        # register — no window write-back.
        with m.Elif(state == _ST_CSUM_ISSUE):
            m.d.sync += state.eq(_ST_CSUM_CAPTURE)

        with m.Elif(state == _ST_CSUM_CAPTURE):
            contrib = Signal(16)
            m.d.comb += contrib.eq(
                Mux(mem_i_r[0], wrp.data, wrp.data << 8)
            )
            with m.If(mem_i_r + 1 >= mem_len_r):
                # Last byte: fold and complement combinationally, retire.
                total = Signal(25)
                m.d.comb += total.eq(csum_sum_r + contrib)
                fold1 = Signal(17)
                m.d.comb += fold1.eq(total[0:16] + total[16:25])
                fold2 = Signal(16)
                m.d.comb += fold2.eq(fold1[0:16] + fold1[16])
                with m.Switch(csum_rd_r):
                    for r in range(4):
                        with m.Case(r):
                            m.d.sync += self.regs[r].eq(~fold2)
                    # RZ (0b100): write discarded.
                m.d.sync += state.eq(_ST_FETCH)
            with m.Else():
                m.d.sync += [
                    csum_sum_r.eq(csum_sum_r + contrib),
                    mem_i_r.eq(mem_i_r + 1),
                    mem_addr_r.eq(mem_addr_r + 1),
                    state.eq(_ST_CSUM_ISSUE),
                ]

        return m
