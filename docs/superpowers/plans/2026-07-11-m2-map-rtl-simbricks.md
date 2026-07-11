# M2: MAP RTL + SimBricks — "the table is the forwarding policy"

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Amaranth `MapCore` passing full-contract cosim against `nanuk-map-emu`, composed PP→MAP RTL verified against `run_pipeline`, and the SimBricks demo forwarding by table lookup instead of hardcoded flooding — with beats for L2 unicast, table-swap policy change, and the two-switch tunnel.

**Architecture:** `MapCore` is `NanukCore`'s sibling (same FETCH/EXEC FSM shape, sequential byte loops for memory-touching instructions, per-instruction `steps`). Composition stays at the glue layer: the C++ SimBricks component instantiates BOTH Verilator models and wires PP outputs to MAP inputs per frame — mirroring `run_pipeline`'s gating exactly. Tables are poked through control-plane write ports, loaded from a `tables.txt` file (startup + mtime-poll reload).

**Tech Stack:** Amaranth 0.5 (pysim), Verilator 5 (native devcontainer) + SimBricks container (amd64), existing `nanuk_spec.map_harness` as the golden side.

## Global Constraints

- Sail model = truth: `MapCore` reproduces `spec/map-model/` semantics bit-for-bit; cosim diffs the ENTIRE outbound contract: verdict, error, egress, delta, steps, and the transmitted frame bytes.
- `steps` counts executed instructions (budget check before execute, = 256 on exhaustion), NOT clock cycles; LD/ST/LOOKUP/CSUMUPD may take many cycles.
- PP core (`hw/nanuk_hw/core.py`) is frozen — no edits.
- All parameters mirror `spec/map-model/params.sail`: HEADROOM 32, BUF 256 (window 288), IMEM 1024, N_PORTS 4, N_TABLES 4, TABLE_MAX 64, STEP_BUDGET 256.
- EXT lesson applies everywhere: NO wide combinational datapaths — every window/table access is a sequential FSM loop over a memory port (Verilator-friendly).
- Verify in devcontainer (`docker run -v $PWD:/workspace ... vsc-nanuk-3d83...`); SimBricks runs via `hw/simbricks/build_and_run*.sh` (not in CI, like stage 4); commit per task; push + watch CI at the end.

## Frozen interfaces

**`MapCore` (hw/nanuk_hw/map_core.py)** — `wiring.Component`:

```
prog_we In(1), prog_addr In(10), prog_data In(32)      # imem load
win_we In(1), win_addr In(9), win_data In(8)           # WINDOW index (0..287); driver fills all 288 (headroom zeros + frame + padding)
plen In(16), ingress In(8)
smd_in In(16*8), hdr_present_in In(16), hdr_offset_in In(16*16)
tbl_cfg_we In(1), tbl_cfg_id In(2), tbl_cfg_kw In(8), tbl_cfg_aw In(8)   # config: set widths, clear count
tbl_add_we In(1), tbl_add_id In(2), tbl_add_key In(64), tbl_add_action In(64)  # append entry (pre-masked by DRIVER to widths — RTL stores as-given; drivers mirror emu_map_table_add masking)
start In(1)
done Out(1), verdict Out(8), error Out(8), egress Out(8), delta Out(signed(16)), steps Out(32)
win_rd_addr In(9), win_rd_data Out(8)                  # post-done frame readback (sync read: data valid the cycle after addr)
```

`start` clears architectural state (regs, pc, steps, status, egress, delta) but NOT imem, window, or tables. Table memory: one `memory.Memory(shape=128, depth=256)` — entry = `{action[127:64], key[63:0]}`, address = `{tbl_id(2), idx(6)}`; per-table regs: `kw[8], aw[8], count[7]`. `tbl_add_we` at full count (64) is ignored (driver enforces; Sail asserts).

FSM states: IDLE, FETCH, EXEC, MEM_ISSUE, MEM_CAPTURE (LD reads; ST writes go straight from EXEC over n cycles in ST_WRITE), LKP_ISSUE, LKP_SCAN, CSUM_ISSUE, CSUM_CAPTURE, CSUM_WB0, CSUM_WB1. Decode fields per `spec/map-model/decode.sail`; opcodes 0x01–0x0C; reserved-bits-zero enforced exactly as NanukCore does (`illegal` default-1 pattern).

**pysim driver (hw/nanuk_hw/map_sim_util.py)**:

```python
@dataclass(frozen=True) class MapCoreResult:
    verdict: int; error: int; egress: int; delta: int; steps: int
    frame: bytes | None            # window[32-delta : 32+min(plen,256)] + tail passthrough NOT applied here (RTL sees window only)
    regs: list[int]
run_map_core(prog, packets, ctxs, tables) -> list[MapCoreResult]
    # ctxs: list of nanuk_spec.harness.ParseResult (or same-shaped stub) + ingress per packet:
    #   actually: list[tuple[ParseResult, int]] — (pp_result, ingress)
run_map_one(prog, packet, pp, ingress, tables) -> MapCoreResult
run_pipeline_rtl(pp_prog, map_prog, packet, tables, ingress) -> tuple[CoreResult, MapCoreResult | None]
    # PP core via run_core, gate on verdict==0, feed hdr/smd into MapCore — the RTL mirror of run_pipeline
```

Frame comparison rule (cosim): golden `MapResult.frame` includes tail passthrough for >256B packets; RTL comparison uses packets ≤ 256B (corpus is), so `frame` compares directly.

**SimBricks component (hw/simbricks/nanuk_hw.cc, rewritten)**: `nanuk_hw -f PP_PROG -m MAP_PROG -t TABLES.TXT -s SOCK...`; per frame: PP controller sequence (existing) → if PP verdict==0: load MAP (window zeros+frame, ctx wires from PP outputs, ingress = rx port), start, wait done → verdict 0: read frame via `win_rd_addr` loop, TX to every port whose egress bit is set; else drop. PP verdict != 0: drop (parser still gates). Stats line on exit: `nanuk_hw: frames in=%lu sent=%lu dropped=%lu map_err=%lu`. `tables.txt` format = the `table`/`entry` lines of the M1 ctx contract (same parser semantics, keys/actions masked in C code before poking); mtime polled every 65536 main-loop iterations → on change: re-config (clears) + re-add all entries, stderr log `nanuk_hw: tables reloaded`.

**Demo scripts**: `nanuk_run.sh` gains `-m ${NANUK_MAP_PROG:-map.bin} -t ${NANUK_TABLES:-tables.txt}`; `build_and_run.sh` assembles both programs + writes tables.txt (host MACs discovered? NO — QEMU i40e NICs get deterministic MACs from SimBricks; discover by first flood run? Simpler: beat 1 uses FLOOD-on-miss so ping works regardless, and beat 2's A/B uses a table entry for host1's MAC — obtain it by running beat 1 first and grepping the component's stderr MAC-learning debug line? NO learning in v0. Resolution: run `ip link show` via ping app? Simplest deterministic path: SimBricks i40e NICs derive MACs from PCI/device ids — VERIFY at execution time by logging DMACs seen (component stderr prints first-seen DMAC per port, a 10-line debug aid); then beat 2 bakes the observed MAC into tables.txt. The check script does exactly this two-phase flow automatically.)

---

### Task 1: MapCore skeleton — interface, imem/window/table memories, IDLE/FETCH/EXEC, MOVI/ADDI/LDMD/branches/JMP/SEND/DROP, totality

(The straight-line instructions first — everything that needs no multi-cycle memory loop — so the FSM shape and totality land with the skeleton. Mirrors NanukCore's structure throughout.)

**Files:** Create `hw/nanuk_hw/map_core.py`, `hw/nanuk_hw/map_sim_util.py`; Test `hw/tests/test_map_core.py`.

- [ ] Write `MapCore` with the frozen interface; implement FETCH (budget → pc-range → fetch), EXEC for MOVI, ADDI (sign-extend imm16 to 64), LDMD (field mux per M1 map: 0-7 smd_in slice, 8 ingress, 9 flood = `0xF & ~(1<<ingress)` via `(C(0xF,4) & ~decoded_onehot)`, 10 hdr_present_in, 11-15 zero), BEQ/BNE/JMP, SEND (range check `d > 32 | d <= -min(plen,256)` → err 6; else egress = rs & 0xF, delta, done), DROP, ILLEGAL (default-1 pattern, reserved bits enforced per decode.sail).
- [ ] Write `map_sim_util.py` driver: program load, window fill (headroom zeros + frame + zero padding to 288), ctx pokes (smd/hdr/ingress/plen), table pokes (mask key/action to widths in Python, mirroring emu_map_table_add), start pulse, wait done (≤ 32768 cycles), snapshot + frame readback loop when verdict==0.
- [ ] Tests (pysim, no golden model needed): MOVI/ADDI wraparound (`0 + (-1)` = all-ones), LDMD flood mask per ingress 0-3, branch skip pattern, SEND masks egress to 4 bits + records delta, SEND delta 33 → err 6, all-zeros word → err 3, `JMP 0xFFFF` → err 4, JMP-0 spin → err 2 with steps == 256.
- [ ] Run: `docker run ... 'cd hw && uv run pytest tests/test_map_core.py -q'` → all pass. Commit: `M2: MapCore skeleton — straight-line instructions, totality, pysim driver`.

### Task 2: MapCore LD/ST — sequential window access, hdr-relative addressing

**Files:** Modify `hw/nanuk_hw/map_core.py`; Test `hw/tests/test_map_core.py` (extend).

- [ ] Effective address (combinational): `base = (hdr==15) ? 0 : hdr_offset_in[hdr]`, absent (hdr != 15 & !hdr_present_in[hdr]) → err 5; `addr = 32 + base + sext(off)` (signed 17-bit intermediate); bounds `addr < 0 | addr + n > 32 + min(plen,256)` → err 1. LD: MEM_ISSUE/MEM_CAPTURE byte loop accumulating MSB-first into 64-bit; ST: ST_WRITE loop writing `rs >> 8*(n-1-i)` bytes.
- [ ] Tests mirror `spec/map-test/test_map_exec_mem.sail` cases: ST/LD round-trip at h_frame, headroom write at -22 visible via readback, hdr-relative LD at h2+8, absent header err 5, past-frame err 1, straddle err 1, last-byte legal.
- [ ] Pass + commit: `M2: MapCore LD/ST with sequential window loops`.

### Task 3: MapCore LOOKUP — table scan FSM

**Files:** Modify `hw/nanuk_hw/map_core.py`; Test `hw/tests/test_map_core.py` (extend).

- [ ] EXEC(LOOKUP): key = rs & width-mask (mask from kw reg via 64-ones >> (64-kw), kw==0 or table count==0 or tbl id ≥ 4 → immediate miss path). LKP_ISSUE/LKP_SCAN: read entry {tbl,idx}, compare key field, hit → rd=action field, back to FETCH; idx+1 == count without hit → miss: rd=0, pc=tgt.
- [ ] Tests mirror `test_map_exec_lookup.sail`: hit falls through with action, miss branches rd=0, key masking (garbage above 48 bits), unconfigured/empty/out-of-plane always miss; plus: entry added twice — first match wins (scan order, matches Sail).
- [ ] Pass + commit: `M2: MapCore LOOKUP scan FSM + control-plane table ports`.

### Task 4: MapCore CSUMUPD — sequential checksum FSM

**Files:** Modify `hw/nanuk_hw/map_core.py`; Test `hw/tests/test_map_core.py` (extend).

- [ ] EXEC(CSUMUPD): base per Task-2 addressing; `base + 20 > limit` → err 1. CSUM_ISSUE reads byte 0 first for IHL (ihl<5 → err 1; `base + ihl*4 > limit` → err 1), then streams the header bytes (skipping indices 10/11 as zero) into a 24-bit sum, byte-parity tracked for hi/lo weighting (byte i even → <<8). Fold twice combinationally at the end (sum ≤ 0x1E'FFFF fits), complement, CSUM_WB0/CSUM_WB1 write bytes 10/11.
- [ ] Tests mirror `test_map_exec_csum.sail`: golden 0xB861 from zeroed and from stale field, idempotence, TTL-decrement → 0xB961, IHL<5 err, truncated header err, absent header err 5.
- [ ] Pass + commit: `M2: MapCore CSUMUPD sequential checksum FSM`.

### Task 5: MAP cosim + fuzz vs nanuk-map-emu

**Files:** Create `hw/tests/test_map_cosim.py`; Modify `hw/tests/test_fuzz.py` (add MAP leg).

- [ ] `test_map_cosim.py` (gated NANUK_COSIM=1): for each of the three demo programs (map_l2fwd, map_ttl, tunnel_push+pop with their tables): run the PP golden model for ctx, then diff `run_map` (golden) vs `run_map_one` (RTL) over the l2l3l4 corpus packets + tunnel frames — ALL fields incl. frame bytes. Assert every M1 spec/python demo scenario reproduces.
- [ ] `run_pipeline_rtl` composed test: same packets through PP-RTL→MAP-RTL vs golden `run_pipeline`; diff PP contract AND MAP contract.
- [ ] Fuzz leg in `test_fuzz.py`: 60 seeded-random packets (len 0..300) × random 48-bit-key tables (0..8 entries, some matching the packet's first 6 bytes) through map_l2fwd; plus 30 random packets through map_ttl (exercises CSUMUPD/error paths); diff emu vs RTL full contract.
- [ ] Run gated suite in devcontainer → pass. Commit: `M2: MAP cosim + differential fuzz vs nanuk-map-emu (rung-2 for the MAP)`.

### Task 6: Verilog export + native verilate check

**Files:** Modify `hw/export.py` (add `--core {parser,map}` flag, default parser — existing CLI calls stay valid); Test: verilate both in devcontainer.

- [ ] `python export.py --core map build/nanuk_map_core.v` emits `nanuk_map_core` module; run the same `verilator --cc` flags as build_and_run.sh over BOTH .v files → no fatal errors.
- [ ] Commit: `M2: export MapCore Verilog; verilator 5 accepts both cores`.

### Task 7: SimBricks component — composed PP→MAP forwarding

**Files:** Modify `hw/simbricks/nanuk_hw.cc` (Controller rewrite + args), `hw/simbricks/nanuk_run.sh`.

- [ ] Add `Vnanuk_map_core` instantiation; extend Controller FSM: kLoad(PP) → kWait(PP done) → PP verdict!=0 → drop, stats++; else kMapLoad (window: 32 zero writes then frame bytes then... window is NOT cleared by start — zero-fill headroom + frame + padding to 288 every frame), wire ctx (plen, ingress=f.port, smd_in/hdr_* copied from PP outputs — these are plain output words on Vnanuk_core), kMapStart, kMapWait → done: verdict 0 → kReadback (win_rd_addr loop, one byte/cycle, into tx buffer of len plen+delta... transmitted frame = window[32-delta .. 32+plen)) → TX to each port with egress bit set → stats; verdict != 0 → drop, map_err++.
- [ ] `tables.txt` loader: parse `table`/`entry` lines (strtoull base 0), poke tbl_cfg/tbl_add with clocked writes (mask key/action to widths before poking); mtime poll every 65536 iterations → reload + stderr log.
- [ ] Args: `-m MAP_PROG` (required), `-t TABLES` (optional; absent → all tables unconfigured → l2fwd floods). First-seen-DMAC debug: on each frame, if DMAC not seen before and seen-count < 8, stderr `nanuk_hw: port %zu dmac %02x:...`.
- [ ] `nanuk_run.sh`: `exec nanuk_hw "$@" -f ${NANUK_PROG:-prog.bin} -m ${NANUK_MAP_PROG:-map.bin} -t ${NANUK_TABLES:-tables.txt}` (paths relative to script dir as today).
- [ ] Compile-only smoke in the SimBricks container (no experiment yet): the build_and_run.sh g++ line extended with `Vnanuk_map_core__ALL.a` — factor the verilate+compile steps into `hw/simbricks/build_component.sh` reused by both run scripts. Commit: `M2: SimBricks component drives composed PP->MAP; table file + reload`.

### Task 8: Beat 1+2 — ping through table-driven forwarding; table swap flips the policy

**Files:** Modify `hw/simbricks/build_and_run.sh`, `hw/simbricks/nanuk_demo.py` (only if executable/env plumbing needs it).

- [ ] Phase A (discover + flood): assemble l2l3l4 + map_l2fwd, EMPTY tables.txt → run → ping must pass (flood-on-miss), grep the dmac debug lines → extract host MACs per port.
- [ ] Phase B (unicast): write tables.txt with both MACs → correct ports (table 0, kw 48, aw 8) → rerun → ping passes AND component stats show sent == unicast (no flood; assert stats line `sent=` equals frames in minus drops, and debug confirms egress bitmaps are single-bit — add a stats counter `flooded=` incremented when egress popcount > 1; assert flooded=0 in phase B).
- [ ] Phase C (policy flip): tables.txt with host1's MAC → the WRONG port → rerun → ping must FAIL (100% loss) — the table, not the code, decides. Script asserts all three phases; single entry point `hw/simbricks/run_beats12.sh` calling build_component.sh once.
- [ ] Run on this machine (Docker, ~minutes per phase). All three phases assert. Commit: `M2: SimBricks beats 1+2 — unicast by table, policy flip by table swap`.

### Task 9: Beat 3 — two-switch tunnel topology

**Files:** Create `hw/simbricks/nanuk_demo_tunnel.py`, `hw/simbricks/run_beat3.sh`; Modify `hw/simbricks/nanuk_run.sh` if per-instance env needed (two switches want different programs → wrapper reads `NANUK_DIR` pointing at a per-switch directory of prog.bin/map.bin/tables.txt; demo script sets `s._executable` to two different wrapper dirs).

- [ ] Topology: host0 — sw_encap — sw_decap — host1 (two `EthSwitch` instances; connect switch-to-switch with `connect_eth_peer_if` between their eth interfaces — verify orchestration supports net-net; menshen/net_switch precedent has listening ports (`-h`), and SwitchNet serializes them; if blocked, fallback: host0+host1 both on sw_encap and a THIRD host behind sw_decap — record whichever works).
- [ ] sw_encap: pp=l2l3l4, map=tunnel_push, tables: t1 host1-MAC → port-toward-decap. sw_decap: pp=parse_tunnel, map=tunnel_pop, no tables. Encap direction covers host0→host1; return traffic host1→host0 crosses sw_decap (parse_tunnel: plain → flood) then sw_encap (miss on host0's MAC in t1 → flood) — plain flood both ways is fine for ping.
- [ ] Assert: ping 0% loss AND sw_decap stats confirm decap happened (add stat `delta_neg=` count of frames sent with negative delta; assert > 0; sw_encap `delta_pos=` > 0).
- [ ] Run locally, iterate until green. Commit: `M2: SimBricks beat 3 — ping through tunnel encap/decap switches`.

### Task 10: CI + full verification + docs

- [ ] Full CI-equivalent local run (all suites; new hw tests ride the NANUK_COSIM=1 lane). Push, watch CI green.
- [ ] Lab notes `guide/notes/2026-07-11-m2-map-rtl-lab-notes.md` (FSM design calls, Verilator/SimBricks lessons, beat evidence with stats lines); memory update (M2 complete state + gotchas); design doc status line updated (M2 done, M3 next). Commit + push.

## Self-review notes

- Spec coverage: M2 = "Amaranth MAP core, cosim vs Sail, SimBricks composed, three beats" — Tasks 1-5 (core+cosim), 6-7 (export+component), 8-9 (beats), 10 (CI). Evaluation-ladder rungs: rung 1-2 for MAP = Task 5; rung 3 = Tasks 8-9.
- Known risks, called out where they land: net-net topology support (Task 9 has a fallback), MAC discovery (Task 8 phase A solves it empirically), window-not-cleared-by-start (drivers zero-fill 288 — stated in THREE places deliberately).
- Type consistency: `MapCoreResult` fields match golden `MapResult` names; `run_map_one(prog, packet, pp, ingress, tables)` argument order matches golden `run_map(prog, packet, pp, tables, ingress)` — NO it doesn't; FROZEN: `run_map_one(prog, packet, pp, tables, ingress)` exactly matching run_map. (Fixed here so Task-1 and Task-5 agree.)
