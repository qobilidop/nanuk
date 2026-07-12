# Nanuk — Core Interface Redesign

**Date:** 2026-07-12
**Status:** Approved design; implementation staged as three plans (see
Migration). Revises MAP ISA v0 (the mutable dev-phase contract).
**Siblings:** [Naming doctrine](2026-07-12-naming-doctrine.md) ·
[Deparser/editor doctrine](2026-07-12-deparser-editor-doctrine.md) ·
[MAP extension design](2026-07-11-map-extension-design.md)

## Goals

1. The Nanuk core's interfaces are clear and minimal.
2. The core is general: no hardcoded system semantics. Per packet, the
   core is the pure function **(frame, md_in) → (verdict, error, md_out,
   frame′)**, parameterized by quasi-static state (two programs, table
   contents). Port counts, egress bitmaps, and protocol names appear
   nowhere in the core or its ISAs.
3. As little logic beyond PP and MAP as possible.

Today the composed core exists only as ~200 lines of per-packet glue in
`demo/nanuk_switch.cc` (double frame load, wide hdr/smd shuttle buses,
delta readback math, egress fan-out), and the system semantics live in
the MAP ISA itself: SEND's `& 0xF` egress bitmap, LDMD's ingress/flood
fields, CSUMUPD's IPv4 IHL parsing. This design gives the core an RTL
existence and evicts all of it.

## The external face

One module: `NanukCore` (`nanuk_core`), three interfaces.

**Data plane in** — byte stream `(data[8], valid, ready, last)` carrying
the full frame (payload included), plus `md_in[MD_SLOTS × 16b]` sampled
at the first beat. Frame length is learned by counting to `last`; there
is no plen port.

**Data plane out** — the mirror: a byte stream for the edited frame,
plus one **result strobe** per packet: `result_valid` presenting
`verdict[2]` (0 sent / 1 drop / 2 error), `error[8]` (stage nibble:
0 = PP, 1 = MAP; code nibble: the stage's ISA error code), and
`md_out[MD_SLOTS × 16b]`. Drop and error strobe with no output stream.

**Control plane** — one address-mapped, write-only port
`(ctrl_sel, ctrl_addr, ctrl_data, ctrl_we)`; `sel` picks PP imem, MAP
imem, table config, or table add. Quasi-static contract: write only
between packets. No readback machinery until something real needs it.

Parameters (mirroring `params.sail` where architectural):
`HEADROOM = 32`, `WINDOW = 256`, `MD_SLOTS = 8`, `STEP_BUDGET = 256`
(per processor, build-time — the bounded-work-by-construction guarantee
is an architectural invariant, not a runtime knob), and RTL-only
`MAX_FRAME` (default 2048) sizing the tail buffer.

**Steps are not interface.** Executed-instruction counts remain outputs
of the standalone processor components (where step-exact cosim runs) and
simulation-peekable in the core, but they left the core's contract: real
per-packet descriptors don't carry instruction counts; debug counters
belong to management interfaces. Counter width is
`⌈log2(STEP_BUDGET + 1)⌉` — on watchdog exhaustion steps *equals* the
budget, so 256 needs 9 bits.

## MAP ISA v0 changes (PP: none)

That PP needs zero changes is a design result: parsing was already
generic; the system semantics had all accreted on the MAP side.

- **LDMD remap.** Fields 0–7: PP SMD slots (unchanged — the internal
  PP→MAP contract). Fields 8–15: system `md_in[0..7]` (new). Deleted:
  ingress (old 8), flood (old 9), hdr_present (old 10 — no program ever
  read it).
- **STMD (new).** Twin of PP's STMD: writes 16-bit units MSB-first from
  a register into `md_out` slots. Symmetry restored: PP writes SMD for
  MAP; MAP writes md_out for the world.
- **SEND loses its register.** `send delta`: verdict = sent, record head
  delta, halt. The delta immediate and its range check
  (−plen < delta ≤ HEADROOM) are unchanged. The old register field is
  must-be-zero, so stale encodings fault as illegal instead of silently
  changing meaning.
- **CSUMUPD → CSUM, de-protocolized.** `csum rd, hdr, off, rl`:
  RFC 1071 ones-complement checksum (folded, complemented) of window
  bytes `[base+off, base+off+r[rl])` into `rd`. No IHL parsing, no
  skipped bytes, no write-back — the program zeroes the old field and
  stores the result itself. Window violation if the range escapes
  `[0, win_limit)`; `len = 0` yields `0xFFFF`.
- **ANDI, SHLI (new).** The generic ALU ops programs need once the
  conveniences die: IPv4 recompute is `ld` → `andi 0x0F` → `shli 2` →
  `csum` → `st`. SHLI mirrors PP's SHL-by-immediate; ANDI mirrors ADDI's
  shape. ADD reg,reg was considered and deferred — no current program
  needs it.

Net: 3 modified (LDMD, SEND, CSUM), 3 added (STMD, ANDI, SHLI), 0
removed; MAP grows 12 → 15 instructions. Verdicts, errors,
LD/ST/LOOKUP/branches unchanged.

## Internal architecture: one shared window

A single window memory (`HEADROOM + WINDOW` bytes) is the only copy of
the head; a tail buffer (RTL-only) holds bytes ≥ WINDOW untouched. Five
phases, strictly turn-based: **FILL → PP_RUN → HANDOFF → MAP_RUN →
DRAIN**, with early exits to the result strobe on PP/MAP drop or error.

- **Fill** streams the frame in once; latches plen at `last`.
- **PP reads via `pkt_base`** — a new construction parameter (default 0;
  the core instantiates `pkt_base = HEADROOM`). One adder, below the
  ISA: the Sail model doesn't know it exists. Standalone instantiations
  and existing per-processor cosim are unchanged.
- **Handoff** is a one-cycle latch: PP's hdr map + SMD outputs wire
  straight into MAP's inputs. The C++ shuttle becomes a register stage.
- **MAP edits in place.** Headroom writes are ordinary STs at negative
  frame offsets (already legal: the window check only requires
  addr ≥ 0).
- **Drain** applies the head delta with one subtractor: read from
  `HEADROOM − delta`, emit `min(plen, WINDOW) + delta` window bytes,
  then the tail verbatim. Encap/decap ship with zero dedicated edit
  hardware — the deparser/editor doctrine made physical.
- **Port muxing is the phase FSM** — fill vs MAP-ST vs csum write, PP vs
  MAP vs drain read: strictly turn-based, so no arbiter exists.

Beyond PP and MAP, exhaustively: fill FSM, drain FSM (one subtractor),
handoff latch, phase FSM, control decode. No copy engine, no arbiter, no
egress interpretation, no checksum fixup, no metadata parsing.

## System conventions (nanuk_switch owns these)

The evicted semantics become documented conventions of the packaged
device, not the core:

| Slot | nanuk_switch convention |
|---|---|
| `md_in[0]` | ingress port id |
| `md_in[1..7]` | zero (reserved) |
| `md_out[0]` | egress port bitmap; 0 = drop-by-policy |
| `md_out[1..7]` | ignored (reserved) |

**The flood table.** The switch control plane installs table 3 (its
"system table" convention, kw = 16, aw = 16) with
`{ingress → flood bitmap}` entries. Programs `ldmd` the ingress,
`lookup` the flood table, `stmd` the result. Flooding is now table
content installed by the system — change the topology, reinstall the
table, same program. The thesis in miniature: the table IS the policy.

All four MAP examples migrate (l2fwd, ttl, tunnel push/pop). The eDSL
gains `md_in[i]` / `emit_md` / `csum` primitives; `send(egress=…)`
stays as sugar compiling to `stmd md_out[0]` + `send` — sugar at the
language layer, over a generic ISA.

A future `nanuk_nic` packaging defines different conventions
(md_out[0] = RSS queue, say) over the same core netlist — the
ARM-Cortex/Corundum shape the project design names as the goal.

## Migration and testing

Authority-chain order, each stage gated by its existing suite:

1. **Sail** (`spec/sail/model/map/`): the six instruction changes;
   regen `nanuk-map-emu`; extend model tests.
2. **IR schema**: Send loses egress, MapLoad ids remap, MapStore-md and
   Csum ops added. An `[ir-breaking]` commit — the hatch exists for
   this.
3. **Python vertical**: ISS, IR interp + lowering, eDSL; gated by the
   ISS-vs-emulator conformance suites and the golden pcap rig.
4. **Examples + playground**: the four programs (the flood-table l2fwd
   is the better teaching artifact), presets, bridge table installs.
5. **RTL**: `pkt_base`; MAP instruction changes; new `core.py`
   (`NanukCore`) with fill/drain/sequencer. Per-processor cosim
   unchanged; new core-level suite drives the stream face with a small
   BFM against the chained ISS oracle (`interp` → `interp_map`, which
   testkit already composes).
6. **Demo**: `nanuk_switch.cc` keeps only SimBricks plumbing, md_in[0]
   stamping, md_out[0] fan-out, flood-table install. Acceptance: the
   three beats unchanged (l2fwd ping, TTL+csum rewrite, two-switch
   nanukproto tunnel).

Staged as **three implementation plans**: (1) ISA + spec + SW vertical,
(2) core RTL + cosim, (3) system/demo — the MAP-extension arc's shape.
Every existing suite stays green throughout; the only intentionally-red
moment is the acknowledged `[ir-breaking]` schema commit.

## Rejected alternatives, and why (keep for the book)

- **Memory-window + doorbell as the public face** — today's de facto
  contract. Rejected: the window layout (offset 32, delta readback math)
  leaks into every consumer; streaming is the only style that survives a
  Tiny Tapeout pin budget, and it is how the field composes datapaths
  (AXI-Stream everywhere: Corundum, NetFPGA).
- **In-band metadata (eBPF data_meta style)** — zero new instructions,
  strong prior art, but it entangles metadata with the headroom-edit
  region and makes the md/frame split part of the stream format — the
  window layout leaking back out by another door.
- **Generalize SEND only** — smallest diff, but asymmetric (md out
  generic, md in still hardcoded) and capped at 64 bits.
- **Two-buffer relay inside the core** — PP/MAP RTL untouched, but it
  RTL-ifies the double load + copy FSM: exactly the glue this design
  exists to delete.
- **Processors as engines on a memory fabric** — the beautiful endpoint
  (maximal reuse, literal dataflow), but it rewrites both processors'
  memory interfaces and adds arbiter infrastructure a turn-based
  pipeline never uses. More glue, not less, at this scale.
- **Incremental checksum (RFC 1624 CSUMADJ)** — O(1) and what real
  routers do for TTL, but range-CSUM subsumes it functionally and
  performance is explicitly not a goal. Deferred, not disliked.
- **Software-only checksum** — purest RISC story, but ~30 instructions
  per header recompute makes every example program noticeably worse as
  a teaching artifact. The range primitive is protocol-agnostic
  arithmetic, which satisfies the actual requirement.
- **Egress-side checksum descriptor (xISA MAC-editor pattern)** — keeps
  the ISA pure but invents new hardcoded descriptor semantics split
  across two blocks, and grows the beyond-PP/MAP logic.
- **Runtime step-budget register** — turns the bounded-work invariant
  into a mutable knob, adds CSR + Sail state + config drift surface, no
  consumer. Raising the constant in the spec is the honest path.
- **Steps on the result strobe** — a cosim convenience masquerading as
  architecture; real per-packet descriptors don't carry instruction
  counts. Observability stays on the standalone components and in
  simulation peeks.
