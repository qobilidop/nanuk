"""NanukCore: the composed PP -> MAP datapath behind the streaming face.

The spec (docs/superpowers/specs/2026-07-12-core-interface-design.md) in
RTL: the core is two windows — the frame window and the metadata window —
each loaded once at ingress, edited in place by the two processors in
turn, and drained at egress. The frame window memory is owned here and
shared: the PP reads it through its pktmem/pkt_base hooks, the MAP takes
its read/write ports on it via the winmem hook, and the fill FSM loads it
through the MAP's driver-load port. Nothing is ever copied; the head
delta is one subtractor in the drain address path.

Per packet the core is the pure function
(frame stream, md_in) -> (verdict, error, md_out, frame' stream),
parameterized by the quasi-static control plane (two programs, tables).

External face (see the spec):
- in_tdata/in_tvalid/in_tready/in_tlast + md_in[128], sampled at SOP.
- out_tdata/out_tvalid/out_tready/out_tlast; one result strobe per packet
  (result_valid + verdict/error/md_out) whatever the verdict — drop and
  error strobe with no output stream.
- ctrl_sel/ctrl_addr/ctrl_data/ctrl_we, write-only, between packets:
  sel 0 = PP imem, 1 = MAP imem, 2 = table config (addr = table id,
  data = {aw[15:8], kw[7:0]}), 3 = table add (addr[1:0] = table id;
  addr[15] = 0 latches the key, addr[15] = 1 supplies the action and
  commits the entry).
- error = {stage nibble, code nibble}: stage 0 = PP, 1 = MAP, 2 = core
  (code 1 = frame overflow: the stream exceeded max_frame; the core
  consumes it to tlast, runs nothing, and strobes verdict = error).

`in_tready` stays low while a packet is in flight: the turn-based,
one-packet-at-a-time behavior is contractual (spec: "ready-gated").
"""

from amaranth import Module, Mux, Signal, signed
from amaranth.lib import memory, wiring
from amaranth.lib.wiring import In, Out

from .map import (
    BUF_BYTES,
    HEADROOM_BYTES,
    MD_SLOTS,
    WIN_BYTES,
    MatchActionProcessor,
)
from .map import VERDICT_DROP as MAP_DROP
from .map import VERDICT_SENT as MAP_SENT
from .pp import VERDICT_ACCEPT as PP_ACCEPT
from .pp import VERDICT_DROP as PP_DROP
from .pp import ParserProcessor

# Composed verdicts (2 bits).
VERDICT_SENT = 0
VERDICT_DROP = 1
VERDICT_ERROR = 2

# Error stage nibbles.
STAGE_PP = 0
STAGE_MAP = 1
STAGE_CORE = 2

# Core error codes.
CORE_ERR_OVERFLOW = 1

# Phase FSM.
_PH_IDLE = 0
_PH_FILL = 1
_PH_PAD = 2
_PH_PP_RUN = 3
_PH_HANDOFF = 4
_PH_MAP_RUN = 5
_PH_DRAIN_ADDR = 6
_PH_DRAIN_DATA = 7
_PH_DRAIN_TAIL = 8
_PH_RESULT = 9
_PH_FLUSH = 10   # overflow: consume the stream to tlast


class NanukCore(wiring.Component):
    """The Nanuk core: PP -> MAP composed behind the streaming face."""

    in_tdata: In(8)
    in_tvalid: In(1)
    in_tready: Out(1)
    in_tlast: In(1)
    md_in: In(16 * MD_SLOTS)

    out_tdata: Out(8)
    out_tvalid: Out(1)
    out_tready: In(1)
    out_tlast: Out(1)

    result_valid: Out(1)
    result_verdict: Out(2)
    result_error: Out(8)
    md_out: Out(16 * MD_SLOTS)

    ctrl_sel: In(2)
    ctrl_addr: In(16)
    ctrl_data: In(64)
    ctrl_we: In(1)

    def __init__(self, max_frame=2048):
        assert max_frame >= BUF_BYTES
        self._max_frame = max_frame
        # The shared frame window: owned here, ports taken by both
        # processors in their constructors (pre-elaboration).
        self.winmem = memory.Memory(shape=8, depth=WIN_BYTES, init=[])
        self.pp = ParserProcessor(pktmem=self.winmem, pkt_base=HEADROOM_BYTES)
        self.map = MatchActionProcessor(winmem=self.winmem)
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.submodules.winmem = self.winmem
        m.submodules.pp = pp = self.pp
        m.submodules.map = mp = self.map

        max_frame = self._max_frame

        # --- Tail buffer: frame bytes >= BUF_BYTES pass through verbatim. --
        m.submodules.tailmem = tailmem = memory.Memory(
            shape=8, depth=max(max_frame - BUF_BYTES, 1), init=[]
        )
        tail_wp = tailmem.write_port()
        tail_rp = tailmem.read_port()

        phase = Signal(4, init=_PH_IDLE)
        plen = Signal(range(max_frame + 1))
        fill_i = Signal(range(max_frame + 1))
        pad_i = Signal(range(WIN_BYTES + 1))
        md_in_r = Signal(16 * MD_SLOTS)

        # Drain bookkeeping.
        drain_i = Signal(signed(18))      # window byte index (frame-relative)
        drain_end = Signal(signed(18))    # min(plen, BUF) + delta
        tail_i = Signal(range(max_frame + 1))
        tail_len = Signal(range(max_frame + 1))
        delta = Signal(signed(16))

        # Result registers.
        verdict_r = Signal(2)
        error_r = Signal(8)
        md_out_r = Signal(16 * MD_SLOTS)
        result_r = Signal(1)

        m.d.comb += [
            self.result_valid.eq(result_r),
            self.result_verdict.eq(verdict_r),
            self.result_error.eq(error_r),
            self.md_out.eq(md_out_r),
        ]
        m.d.sync += result_r.eq(0)  # 1-cycle strobe (overridden in RESULT)

        # --- Control plane: pure decode, write-only, between packets. ------
        add_key = Signal(64)
        m.d.comb += [
            pp.prog_we.eq((self.ctrl_sel == 0) & self.ctrl_we),
            pp.prog_addr.eq(self.ctrl_addr[0:10]),
            pp.prog_data.eq(self.ctrl_data[0:32]),
            mp.prog_we.eq((self.ctrl_sel == 1) & self.ctrl_we),
            mp.prog_addr.eq(self.ctrl_addr[0:10]),
            mp.prog_data.eq(self.ctrl_data[0:32]),
            mp.tbl_cfg_we.eq((self.ctrl_sel == 2) & self.ctrl_we),
            mp.tbl_cfg_id.eq(self.ctrl_addr[0:2]),
            mp.tbl_cfg_kw.eq(self.ctrl_data[0:8]),
            mp.tbl_cfg_aw.eq(self.ctrl_data[8:16]),
            mp.tbl_add_we.eq(
                (self.ctrl_sel == 3) & self.ctrl_we & self.ctrl_addr[15]
            ),
            mp.tbl_add_id.eq(self.ctrl_addr[0:2]),
            mp.tbl_add_key.eq(add_key),
            mp.tbl_add_action.eq(self.ctrl_data),
        ]
        with m.If((self.ctrl_sel == 3) & self.ctrl_we & ~self.ctrl_addr[15]):
            m.d.sync += add_key.eq(self.ctrl_data)

        # --- Static wiring: PP outputs feed MAP inputs (the handoff). ------
        m.d.comb += [
            mp.hdr_present_in.eq(pp.hdr_present),
            mp.hdr_offset_in.eq(pp.hdr_offset),
            mp.md_in.eq(pp.md_out),
            pp.md_in.eq(md_in_r),
            pp.plen.eq(plen),
            mp.plen.eq(plen),
        ]
        # PP's standalone load ports are unused in shared-window mode.
        m.d.comb += [pp.pkt_we.eq(0), pp.pkt_addr.eq(0), pp.pkt_data.eq(0)]

        # MAP window driver-load port: the fill FSM's write path.
        fill_we = Signal(1)
        fill_addr = Signal(range(WIN_BYTES))
        fill_data = Signal(8)
        m.d.comb += [
            mp.win_we.eq(fill_we),
            mp.win_addr.eq(fill_addr),
            mp.win_data.eq(fill_data),
        ]

        m.d.comb += self.in_tready.eq(
            (phase == _PH_FILL) | (phase == _PH_FLUSH)
        )
        beat = Signal(1)
        m.d.comb += beat.eq(self.in_tvalid & self.in_tready)

        m.d.comb += [pp.start.eq(0), mp.start.eq(0)]

        with m.Switch(phase):
            with m.Case(_PH_IDLE):
                with m.If(self.in_tvalid):
                    # SOP: sample the metadata window, zero the counters.
                    # (tready is still low this cycle; the first byte is
                    # accepted once FILL asserts it.)
                    m.d.sync += [
                        md_in_r.eq(self.md_in),
                        plen.eq(0),
                        fill_i.eq(0),
                        pad_i.eq(0),
                        phase.eq(_PH_FILL),
                    ]

            with m.Case(_PH_FILL):
                with m.If(beat):
                    with m.If(fill_i >= max_frame):
                        # Overflow: this byte does not fit. Consume the
                        # rest of the packet, run nothing.
                        m.d.sync += [
                            phase.eq(
                                Mux(self.in_tlast, _PH_RESULT, _PH_FLUSH)
                            ),
                            verdict_r.eq(VERDICT_ERROR),
                            error_r.eq((STAGE_CORE << 4) | CORE_ERR_OVERFLOW),
                            md_out_r.eq(md_in_r),
                        ]
                    with m.Else():
                        with m.If(fill_i < BUF_BYTES):
                            m.d.comb += [
                                fill_we.eq(1),
                                fill_addr.eq(HEADROOM_BYTES + fill_i),
                                fill_data.eq(self.in_tdata),
                            ]
                        with m.Else():
                            m.d.comb += [
                                tail_wp.addr.eq(fill_i - BUF_BYTES),
                                tail_wp.data.eq(self.in_tdata),
                                tail_wp.en.eq(1),
                            ]
                        m.d.sync += fill_i.eq(fill_i + 1)
                        with m.If(self.in_tlast):
                            m.d.sync += [plen.eq(fill_i + 1), phase.eq(_PH_PAD)]

            with m.Case(_PH_FLUSH):
                with m.If(beat & self.in_tlast):
                    m.d.sync += phase.eq(_PH_RESULT)

            with m.Case(_PH_PAD):
                # Zero the headroom (0..31) and the window padding beyond
                # the frame (32+min(plen,256) .. 287): the MAP contract
                # says the driver fills the whole window per packet.
                pad_addr = Signal(range(WIN_BYTES))
                in_headroom = pad_i < HEADROOM_BYTES
                frame_end = Signal(range(WIN_BYTES + 1))
                m.d.comb += frame_end.eq(
                    HEADROOM_BYTES + Mux(plen < BUF_BYTES, plen, BUF_BYTES)
                )
                m.d.comb += pad_addr.eq(
                    Mux(in_headroom, pad_i, frame_end + (pad_i - HEADROOM_BYTES))
                )
                done_pad = Signal(1)
                m.d.comb += done_pad.eq(
                    frame_end + (pad_i - HEADROOM_BYTES) >= WIN_BYTES
                )
                with m.If(in_headroom | ~done_pad):
                    m.d.comb += [
                        fill_we.eq(1),
                        fill_addr.eq(pad_addr),
                        fill_data.eq(0),
                    ]
                    m.d.sync += pad_i.eq(pad_i + 1)
                with m.Else():
                    m.d.comb += pp.start.eq(1)
                    m.d.sync += phase.eq(_PH_PP_RUN)

            with m.Case(_PH_PP_RUN):
                with m.If(pp.done):
                    with m.If(pp.verdict == PP_ACCEPT):
                        m.d.sync += phase.eq(_PH_HANDOFF)
                    with m.Else():
                        m.d.sync += [
                            verdict_r.eq(
                                Mux(pp.verdict == PP_DROP,
                                    VERDICT_DROP, VERDICT_ERROR)
                            ),
                            error_r.eq(
                                Mux(pp.verdict == PP_DROP, 0,
                                    (STAGE_PP << 4) | pp.error[0:4])
                            ),
                            md_out_r.eq(pp.md_out),
                            phase.eq(_PH_RESULT),
                        ]

            with m.Case(_PH_HANDOFF):
                # PP's hdr map + md feed MAP combinationally; start latches.
                m.d.comb += mp.start.eq(1)
                m.d.sync += phase.eq(_PH_MAP_RUN)

            with m.Case(_PH_MAP_RUN):
                with m.If(mp.done):
                    m.d.sync += md_out_r.eq(mp.md_out)
                    with m.If(mp.verdict == MAP_SENT):
                        wlen = Signal(signed(18))
                        m.d.comb += wlen.eq(
                            Mux(plen < BUF_BYTES, plen, BUF_BYTES)
                            + mp.delta
                        )
                        strip = Signal(signed(18))
                        m.d.comb += strip.eq(-mp.delta)
                        m.d.sync += [
                            delta.eq(mp.delta),
                            drain_i.eq(0),
                            drain_end.eq(wlen),
                            # A strip deeper than the window starts the
                            # tail readout mid-tail (window part empty).
                            tail_i.eq(
                                Mux(strip > BUF_BYTES, strip - BUF_BYTES, 0)
                            ),
                            tail_len.eq(
                                Mux(plen > BUF_BYTES, plen - BUF_BYTES, 0)
                            ),
                            verdict_r.eq(VERDICT_SENT),
                            error_r.eq(0),
                            phase.eq(_PH_DRAIN_ADDR),
                        ]
                    with m.Elif(mp.verdict == MAP_DROP):
                        m.d.sync += [
                            verdict_r.eq(VERDICT_DROP),
                            error_r.eq(0),
                            phase.eq(_PH_RESULT),
                        ]
                    with m.Else():
                        m.d.sync += [
                            verdict_r.eq(VERDICT_ERROR),
                            error_r.eq((STAGE_MAP << 4) | mp.error[0:4]),
                            phase.eq(_PH_RESULT),
                        ]

            with m.Case(_PH_DRAIN_ADDR):
                # Issue the window read; data is valid next cycle.
                with m.If(drain_i < drain_end):
                    m.d.comb += mp.win_rd_addr.eq(
                        (HEADROOM_BYTES - delta + drain_i)[0:9]
                    )
                    m.d.sync += phase.eq(_PH_DRAIN_DATA)
                with m.Elif(tail_i < tail_len):
                    m.d.comb += [
                        tail_rp.addr.eq(tail_i),
                        tail_rp.en.eq(1),
                    ]
                    m.d.sync += phase.eq(_PH_DRAIN_TAIL)
                with m.Else():
                    m.d.sync += phase.eq(_PH_RESULT)

            with m.Case(_PH_DRAIN_DATA):
                last = Signal(1)
                m.d.comb += [
                    mp.win_rd_addr.eq((HEADROOM_BYTES - delta + drain_i)[0:9]),
                    self.out_tdata.eq(mp.win_rd_data),
                    self.out_tvalid.eq(1),
                    last.eq((drain_i + 1 >= drain_end) & (tail_len == 0)),
                    self.out_tlast.eq(last),
                ]
                with m.If(self.out_tready):
                    m.d.sync += [drain_i.eq(drain_i + 1), phase.eq(_PH_DRAIN_ADDR)]

            with m.Case(_PH_DRAIN_TAIL):
                last = Signal(1)
                m.d.comb += [
                    tail_rp.addr.eq(tail_i),
                    tail_rp.en.eq(1),
                    self.out_tdata.eq(tail_rp.data),
                    self.out_tvalid.eq(1),
                    last.eq(tail_i + 1 >= tail_len),
                    self.out_tlast.eq(last),
                ]
                with m.If(self.out_tready):
                    m.d.sync += [tail_i.eq(tail_i + 1), phase.eq(_PH_DRAIN_ADDR)]

            with m.Case(_PH_RESULT):
                m.d.sync += [result_r.eq(1), phase.eq(_PH_IDLE)]

        return m
