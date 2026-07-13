# Nanuk — Core Interface Redesign

**Date:** 2026-07-12
**Status:** Approved design; implementation staged as three plans (see
Migration). Revises ISA v0 for both processors (the mutable dev-phase
contract).
**Siblings:** [Naming doctrine](2026-07-12-naming-doctrine.md) ·
[Deparser/editor doctrine](2026-07-12-deparser-editor-doctrine.md) ·
[MAP extension design](2026-07-11-map-extension-design.md)

## Goals

1. The Nanuk core's interfaces are clear and minimal.
2. The core is general: no hardcoded system semantics. Per packet, the
   core is the pure function **(frame, md) → (verdict, error, md′,
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

## The central abstraction: two windows, both edited in place

The core transforms exactly two things, the same way:

- **The frame window** — the packet's first `WINDOW` bytes (plus
  `HEADROOM`), written once at ingress, edited in place by MAP,
  drained with the head delta applied. Untouched bytes pass through.
- **The metadata window** — one `md[MD_SLOTS × 16b]` vector, loaded
  from the input sideband at ingress, readable and writable in place by
  **both** processors (LDMD/STMD), presented as `md_out` on the result
  strobe. Untouched slots pass through.

A core running no-op programs is the identity function on
(frame, metadata). Everything the core does is explicit, programmed
edits to these two windows — the computation-graph reading is literal.

The one *typed* channel beside them is the hdr map
(`hdr_present/hdr_offset`, PP → MAP only): it is the parse result that
LD/ST addressing consumes structurally, so it cannot be opaque slots.
Everything opaque is metadata.

Pass-through has a sharp edge, accepted consciously: an MD slot a MAP
program forgets to write emerges holding its inbound value (e.g.
`md_out[0]` inherits the ingress id if never overwritten). The
mitigation lives at the language layer — the eDSL's `send(egress=…)`
sugar always emits the STMD — plus the system's slot conventions. This
is the same behavior the frame itself has: untouched content flows
through.

## The external face

One module: `NanukCore` (`nanuk_core`), three interfaces. Signal names
follow AXI-Stream conventions (`tvalid/tready/tlast`) purely for
recognizability — the semantics are the standard ones.

**Data plane in** — byte stream `(tdata[8], tvalid, tready, tlast)`
carrying the full frame (payload included), plus `md_in[MD_SLOTS × 16b]`
sampled at the first beat into the metadata window. Frame length is
learned by counting to `tlast`; there is no plen port. `tready` is the
flow control: it stays low while a packet is in flight, making the
turn-based, one-packet-at-a-time behavior contractual rather than
accidental.

**Data plane out** — the mirror: a byte stream for the edited frame,
plus one **result strobe** per packet: `result_valid` presenting
`verdict[2]` (0 sent / 1 drop / 2 error), `error[8]`, and
`md_out[MD_SLOTS × 16b]` (the metadata window's final state — defined
for all verdicts, including drop and error). Drop and error strobe with
no output stream.

`error` is a stage nibble plus a code nibble. Stage 0 = PP, 1 = MAP
(code = that stage's ISA error code), and stage 2 = **the core
itself**: code 1 = frame overflow — the input stream exceeded
`MAX_FRAME`, the core consumed the stream to `tlast`, ran nothing, and
strobed verdict = error. Hardware defines the overflow case; "the
system shouldn't send that" is not a semantics.

**Control plane** — one address-mapped, write-only port
`(ctrl_sel, ctrl_addr, ctrl_data, ctrl_we)`; `sel` picks PP imem, MAP
imem, table config, or table add. Quasi-static contract: write only
between packets. Write-only is a deliberate deviation from prior art
(real control planes have readback for state audit); simulation peeks
and the conformance suites cover today's needs, and the trigger for
adding readback is the first need to audit live table state.

Parameters (mirroring `params.sail` where architectural):
`HEADROOM = 32`, `WINDOW = 256`, `MD_SLOTS = 8` (LDMD/STMD's 4-bit
field addresses up to 16 without an encoding change), `STEP_BUDGET =
256` (per processor, build-time — the bounded-work-by-construction
guarantee is an architectural invariant, not a runtime knob), and
RTL-only `MAX_FRAME` (default 2048) sizing the tail buffer.

**Steps are not interface.** Executed-instruction counts remain outputs
of the standalone processor components (where step-exact cosim runs) and
simulation-peekable in the core, but they left the core's contract: real
per-packet descriptors don't carry instruction counts; debug counters
belong to management interfaces. Counter width is
`⌈log2(STEP_BUDGET + 1)⌉` — on watchdog exhaustion steps *equals* the
budget, so 256 needs 9 bits.

## ISA v0 changes

**PP (11 → 12 instructions):**

- **LDMD (new).** Reads a metadata-window slot into a register — same
  encoding as MAP's. Parsing can now depend on system metadata (e.g.
  port-based parsing off the ingress convention).
- **STMD (redefined, same encoding).** Writes 16-bit units MSB-first
  into the metadata window — no longer a private PP→MAP SMD file. What
  PP writes, MAP (and, if MAP leaves it, the world) reads: slots are
  the one opaque channel.

**MAP (12 → 15 instructions):**

- **LDMD remap, simplified.** Fields 0–7: metadata-window slots (what
  the system provided, as edited by PP so far). Fields 8–15: reserved
  (illegal). Deleted: ingress (old 8 — now a slot by system
  convention), flood (old 9 — becomes a lookup table), hdr_present
  (old 10 — no program ever read it).
- **STMD (new).** Same instruction as PP's: writes metadata-window
  slots. Symmetric: each processor edits the same vector in its turn.
- **SEND loses its register.** `send delta`: verdict = sent, record head
  delta, halt. The delta immediate and its range check
  (−plen < delta ≤ HEADROOM) are unchanged — head deltas remain
  compile-time constants (variable-length encap is out of scope, an
  inherited and accepted limit). The old register field is must-be-zero,
  so stale encodings fault as illegal instead of silently changing
  meaning.
- **CSUMUPD → CSUM, de-protocolized.** `csum rd, hdr, off, rl`:
  RFC 1071 ones-complement checksum (folded, complemented) of window
  bytes `[base+off, base+off+r[rl])` into `rd`. No IHL parsing, no
  skipped bytes, no write-back — the program zeroes the old field and
  stores the result itself. Window violation if the range escapes
  `[0, win_limit)`; `len = 0` yields `0xFFFF`. What stays hardcoded is
  the *arithmetic family* (ones-complement, shared by IPv4/TCP/UDP/
  ICMP), not any protocol's layout; a CRC-flavored protocol would need
  new hardware, and that line is drawn deliberately.
- **ANDI, SHLI (new).** The generic ALU ops programs need once the
  conveniences die: IPv4 recompute is `ld` → `andi 0x0F` → `shli 2` →
  `csum` → `st`. SHLI mirrors PP's SHL-by-immediate; ANDI mirrors ADDI's
  shape. ADD reg,reg was considered and deferred — no current program
  needs it.

Verdicts, error codes, EXT/ADV/SETHDR, LD/ST/LOOKUP/branches: all
unchanged.

## Internal architecture: one shared window (× two)

A single frame-window memory (`HEADROOM + WINDOW` bytes) is the only
copy of the head; a tail buffer (RTL-only) holds bytes ≥ WINDOW
untouched; one `MD_SLOTS × 16b` register file is the metadata window.
Five phases, strictly turn-based: **FILL → PP_RUN → HANDOFF → MAP_RUN →
DRAIN**, with early exits to the result strobe on PP/MAP drop or error
(and from FILL on frame overflow).

- **Fill** streams the frame in once, loads the metadata file from
  `md_in` at the first beat, latches plen at `tlast`.
- **PP reads via `pkt_base`** — a new construction parameter (default 0;
  the core instantiates `pkt_base = HEADROOM`). One adder, below the
  ISA: the Sail model doesn't know it exists. Standalone instantiations
  and existing per-processor cosim are unchanged.
- **Handoff** is a one-cycle latch of the hdr map only — PP's
  hdr_present/hdr_offset wire into MAP's base-address inputs. Metadata
  needs no handoff: both processors address the same register file in
  their turn.
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
hdr-map latch, phase FSM, control decode. No copy engine, no arbiter,
no SMD shuttle, no egress interpretation, no checksum fixup, no
metadata parsing.

## System conventions (nanuk_switch owns these)

The evicted semantics become documented conventions of the packaged
device, not the core. The metadata window is one shared namespace, so
the switch's convention covers all eight slots:

| Slot | nanuk_switch convention |
|---|---|
| `md[0]` | in: ingress port id · out: egress port bitmap (0 = drop-by-policy) |
| `md[1..7]` | program-pair space (in: zero · out: ignored by the switch) |

Slot 0 is deliberately dual-use: the program consumes the ingress id
and overwrites the slot with its egress decision — making the
pass-through edge (forgetting to write it would "forward toward
ingress") loud in the conventions table and guarded by the eDSL sugar.
PP/MAP program pairs are co-designed artifacts, so every other slot is
theirs. (Implementation revised the first draft here: it reserved
`md[1..3]` for the system, but nothing used them and the reservation
starved the richest PP demo — l2l3l4 records DMAC + TCI + dport, five
slots. One system slot is the honest minimum; the tunnel examples' tag
stays at slot 5.)

**The flood table.** The switch control plane installs table 3 (its
"system table" convention, kw = 16, aw = 16) with
`{ingress → flood bitmap}` entries. Programs `ldmd` slot 0, `lookup`
table 3, `stmd` slot 0. Flooding is now table content installed by the
system — change the topology, reinstall the table, same program. The
thesis in miniature: the table IS the policy.

All four MAP examples migrate (l2fwd, ttl, tunnel push/pop). The eDSL
gains `md[i]` read/write and `csum` primitives; `send(egress=…)` stays
as sugar compiling to `stmd md[0]` + `send` — sugar at the language
layer, over a generic ISA.

A future `nanuk_nic` packaging defines different conventions
(md[0]-out = RSS queue, say) over the same core netlist — the
ARM-Cortex/Corundum shape the project design names as the goal.

## Migration and testing

Authority-chain order, each stage gated by its existing suite:

1. **Sail** (`spec/sail/model/{pp,map}/`): PP gains LDMD and the shared
   metadata window; MAP's six instruction changes; regen both emulators;
   extend model tests.
2. **IR schema**: Send loses egress, MapLoad ids remap, metadata
   store/load and Csum ops added on both programs' op sets as needed.
   An `[ir-breaking]` commit — the hatch exists for this.
3. **Python vertical**: ISS, IR interp + lowering, eDSL; gated by the
   ISS-vs-emulator conformance suites and the golden pcap rig.
4. **Examples + playground**: the four programs (the flood-table l2fwd
   is the better teaching artifact), presets, bridge table installs.
5. **RTL**: `pkt_base`; both processors' instruction changes; new
   `core.py` (`NanukCore`) with fill/drain/sequencer and the shared
   metadata file. Per-processor cosim continues gating PP and MAP; a
   new core-level suite drives the stream face with a small BFM against
   the chained ISS oracle (`interp` → `interp_map`, which testkit
   already composes).
6. **Demo**: `nanuk_switch.cc` keeps only SimBricks plumbing, md[0]
   stamping, md[0] fan-out, flood-table install. Acceptance: the
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
- **Two metadata spaces (private PP→MAP SMD + system md_in/md_out)** —
  the first-draft shape of this very design. Superseded by the single
  in-place metadata window once PP needed read access: one mechanism,
  less hardware (no SMD shuttle or separate md_out register), a simpler
  LDMD map, and the identity-function default. Kept here because the
  two-space version *looks* better-isolated but is strictly more moving
  parts.
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
- **Zero-init md_out (explicit-outputs-only)** — safer against
  forgot-to-write bugs (md[0] = 0 = drop), but it breaks the two-window
  symmetry (the frame passes through; why doesn't metadata?) and
  forfeits the identity-function default. The eDSL sugar guards the
  edge instead.
