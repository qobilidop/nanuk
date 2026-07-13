# Core Redesign Plan 2/3: Core RTL + Cosim

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the RTL half of `docs/superpowers/specs/2026-07-12-core-interface-design.md`: both processors speak the metadata-window ISA, and the composed `NanukCore` exists with the streaming face, gated by per-processor cosim and a new core-level suite against the chained ISS oracle.

**Architecture:** Shared-window composition (design A): one window memory (headroom + frame) written once by the fill FSM; PP reads via `pkt_base`; MAP edits in place; drain applies the head delta with one subtractor; the md register file is shared state both processors touch in their turn. Phase FSM doubles as all port muxing.

**Tech Stack:** Amaranth (Python RTL), pytest + Amaranth simulator, ISS oracles from plan 1.

## Global Constraints

- Plan 1 is merged into this branch; the SW vertical (ISS/emulators/examples) is the oracle. Do not touch `sw/python` except cosim harness call sites under `hw/amaranth/tests/`.
- RTL mirrors Sail semantics bit-for-bit; the per-processor cosim suites (`test_pp_cosim.py`, `test_map_cosim.py`) are the gate, `NANUK_COSIM=1`.
- All tests: `./dev.sh bash -lc 'cd hw/amaranth && uv sync && NANUK_COSIM=1 uv run pytest tests'`.
- Commit style per git log; every task ends green for the files it owns.
- Locked encodings and semantics: same table as plan 1 (`docs/superpowers/plans/2026-07-12-core-redesign-1-isa-sw.md`).

## External face of NanukCore (locked, from the spec)

```
Data in :  in_tdata[8], in_tvalid, in_tready(out), in_tlast, md_in[128]
Data out:  out_tdata[8], out_tvalid, out_tready(in), out_tlast,
           result_valid (1-cycle strobe), result_verdict[2],
           result_error[8] (stage nibble: 0 PP / 1 MAP / 2 core; code nibble),
           md_out[128]
Control :  ctrl_sel[2] (0 PP imem, 1 MAP imem, 2 table config, 3 table add),
           ctrl_addr[16], ctrl_data[64], ctrl_we
           (table config: addr = table id, data = {aw[7:0], kw[7:0]};
            table add: addr = table id, data written twice — first ctrl_we
            with addr bit 15 clear = key, then bit 15 set = action;
            simpler: two sels are enough — see Task 4 for the final split)
Params  :  HEADROOM=32, WINDOW=256, MD_SLOTS=8, MAX_FRAME=2048 (RTL-only)
Core error codes: stage 2, code 1 = frame overflow (> MAX_FRAME).
```

---

### Task 1: MAP RTL — metadata window + instruction changes

**Files:**
- Modify: `hw/amaranth/nanuk_amaranth/map.py`
- Test: `hw/amaranth/tests/test_map.py`, `hw/amaranth/tests/test_map_cosim.py`, `hw/amaranth/tests/test_fuzz.py` (MAP legs)

**Interfaces (Produces):** `MatchActionProcessor` port changes — `smd_in`, `hdr?` unchanged except: delete `ingress`, `egress`; rename `smd_in` to `md_in: In(16 * MD_SLOTS)`; add `md_out: Out(16 * MD_SLOTS)` (the md file's live value, valid at `done`). `start` loads the md file from `md_in`. All else unchanged.

- [ ] Mirror plan 1 Task 1's Sail changes in Amaranth: opcode constants (`OP_CSUM = 0x0A`, `OP_STMD = 0x0D`, `OP_ANDI = 0x0E`, `OP_SHLI = 0x0F`); decode field extraction for the new layouts; delete the flood/ingress logic and `N_PORTS`; md register file `[Signal(16) for _ in range(8)]` loaded from `md_in` on `start`, written by STMD (MSB-first mux like PP's existing STMD RTL shape), read by LDMD (slot ≥ 8 → illegal); CSUM as a new FSM loop (`_ST_CSUM_ISSUE/_CAPTURE` reworked: base+len from register, no IHL parse, no WB states — result to a register via `reg_write`; delete `_ST_CSUM_WB0/1`); SEND: reg field must-be-zero at decode, delta only; ANDI/SHLI single-cycle EXEC ops.
- [ ] Update `test_map.py` unit tests to the new semantics (same behaviors as plan 1's ISS tests: stmd/ldmd round-trip + illegal edges, andi/shli, csum golden via the worked IPv4 example, bare send).
- [ ] Update `test_map_cosim.py` + fuzz call sites: `run_map_iss(..., md_in)`, drive `dut.md_in`, compare `md_out` slots against `iss.md`.
- [ ] Run: `NANUK_COSIM=1 uv run pytest tests/test_map.py tests/test_map_cosim.py tests/test_fuzz.py -q` → green. Commit: `feat(hw): MAP RTL speaks the metadata window`.

### Task 2: PP RTL — pkt_base + LDMD + md window

**Files:**
- Modify: `hw/amaranth/nanuk_amaranth/pp.py`
- Test: `hw/amaranth/tests/test_pp.py`, `test_pp_cosim.py`, `test_fuzz.py` (PP legs)

**Interfaces (Produces):** `ParserProcessor(pktmem=None, pkt_base=0)` construction params — when `pktmem` (an `amaranth.lib.memory.Memory`) is given, PP takes a read port on it instead of instantiating its own buffer, and every EXT byte-read address gains `+ pkt_base` (hdr_limit unchanged — it bounds frame offsets, not window addresses; the `pkt_we/addr/data` load ports go unused in shared mode, tied off by the parent). Standalone default (`pktmem=None`) is bit-identical to today. Ports: rename `smd` out to `md_out: Out(16 * 8)`; add `md_in: In(16 * 8)` loaded on `start`. Add `OP_LDMD = 0x0C` decode + EXEC arm (slot ≥ 8 → err illegal). `ERR_SMD_RANGE` renamed `ERR_MD_RANGE` (same code 5).
- [ ] Implement; update unit + cosim call sites (`run_pp_iss(..., md_in)`); standalone instantiation defaults keep existing tests valid.
- [ ] Run PP suites green. Commit: `feat(hw): PP reads/writes the md window; pkt_base param`.

### Task 3: NanukCore — shared window, fill/drain, sequencer

**Files:**
- Create: `hw/amaranth/nanuk_amaranth/core.py`
- Test: `hw/amaranth/tests/test_core.py` (new)

**Interfaces (Produces):** `NanukCore(max_frame=2048)` wiring.Component with the locked external face. Internals:
- One window memory 288×8 owned by the core (write mux: fill FSM during FILL | MAP's window write port during MAP_RUN — MAP keeps its internal window? NO: MAP also gains the same `winmem=None` sharing hook as PP, taking read+write ports on the core's memory with its existing `win_we` driver-load port unused in shared mode. If threading MAP's window out proves invasive, fall back to MAP-private window + fill FSM writing both memories — note it in core.py's docstring as an area shortcut; the sequencing and interfaces stay identical either way). One tail memory (max_frame−256)×8, fill-written, drain-read.
- Phase FSM: IDLE→FILL→PP_RUN→HANDOFF→MAP_RUN→DRAIN→RESULT (early exits: PP !accept → RESULT; MAP drop/error → RESULT; FILL overflow → consume to tlast then RESULT with stage-2 error).
- PP instantiated `ParserProcessor(pktmem=window, pkt_base=HEADROOM)` — it reads the shared window through its own read port; no copy exists anywhere.
- HANDOFF: latch PP hdr map + md_out into MAP's `hdr_*_in`/`md_in`; pulse MAP start.
- DRAIN: window read from `HEADROOM − delta + i` for `min(plen,256)+delta` bytes via MAP's `win_rd_addr` port, then tail bytes verbatim; out stream valid/ready handshake.
- RESULT strobe: verdict map (PP accept+MAP sent→0; PP drop or MAP drop→1; any error→2), error byte `{stage, code}`, `md_out` from MAP (or PP md on PP-stage halt, or md_in snapshot on core overflow).
- Control decode: sel 0/1 → imem write ports; sel 2 → `tbl_cfg_*`; sel 3 → `tbl_add_*` (key in ctrl_data on addr[15]=0, action+commit on addr[15]=1; table id in addr[1:0]).

- [ ] Write `test_core.py` first: a stream BFM helper (drive bytes with random ready/valid gaps), the chained ISS oracle (`run_pp_iss` → gate → `run_map_iss` with `pp.md` as md_in → frame/md/verdict), and cases: l2fwd hit/miss over the example programs + demo tables, ttl rewrite, tunnel push (delta +22) and pop (−22), PP drop gates, MAP error reports stage nibble 1, oversize frame (> max_frame, use a small `max_frame=64` instance) reports stage 2 code 1, back-to-back packets reuse the core (ready-gated), tail passthrough for a 300-byte frame with a 256-byte window.
- [ ] Implement `core.py` until green. Run the whole HW suite. Commit: `feat(hw): NanukCore — streaming face over the composed PP->MAP datapath`.

### Task 4: Export + gate

**Files:**
- Modify: `hw/amaranth/nanuk_amaranth/export.py` (add `--processor core` → `nanuk_core`)
- Test: full sweep

- [ ] Export all three Verilog modules successfully (`uv run nanuk-export --processor core /tmp/nanuk_core.v` inside the container).
- [ ] `NANUK_COSIM=1 uv run pytest tests -q` fully green; ruff clean.
- [ ] Commit: `feat(hw): export nanuk_core Verilog`.
