# Lab notes: the core interface redesign — two windows, one identity function

*2026-07-12. The arc from "I want the core's interfaces minimal and general"
to a merged streaming NanukCore: spec
(`docs/superpowers/specs/2026-07-12-core-interface-design.md`), three
implementation plans, ~30 commits. Every gate green: Sail 12, SW 390
(cosim), HW 126 (cosim), playground 11 + build, demo beats 1–3.*

## The design conversation, compressed

- **Most "hardcoded semantics" lived in the ISA, not the RTL.** SEND's
  `& 0xF` egress bitmap, LDMD's ingress/flood fields, CSUMUPD's IHL
  parsing — the eviction had to be a full-vertical ISA v0 revision, not an
  RTL cleanup. PP needed zero eviction (parsing was already generic);
  everything system-shaped had accreted on the MAP side.
- **The metadata design improved twice by requirement pull.** Draft 1:
  egress bitmap → opaque `md_out` slots. Draft 2 (review): PP should read
  and write metadata too — which collapsed the separate PP→MAP SMD file
  and the md_out register into ONE `md[8×16b]` vector that flows through
  the core like the frame does: loaded at ingress, edited in place by both
  processors, drained at egress. A no-op core is the identity function on
  (frame, metadata). The two-space draft is in the spec's rejected list:
  it *looks* better-isolated but is strictly more moving parts.
- **Flooding became a lookup table.** `flood = ~(1 << ingress) & 0xF`
  needed shift-by-register the MAP lacks — and the right answer wasn't an
  ALU op but table 3: `{ingress → flood bitmap}`, installed by the
  switch's control plane. Change the topology, reinstall the table, same
  program. The project's thesis in miniature, discovered under duress.
- **CSUM kept the arithmetic, lost the protocol.** Range ones-complement
  sum into a register (length from a register); the program zeroes and
  stores the field itself via `andi`/`shli`/`csum`/`st`. RFC 1624
  incremental update was considered and rejected: range-CSUM subsumes it
  except for performance, which is explicitly a non-goal.
- **Steps left the interface.** Real per-packet descriptors don't carry
  instruction counts; debug counters belong to management planes. The
  cosim observability moved to the standalone components and simulation
  peeks. (Also caught: representing "steps == budget" needs
  ⌈log₂(257)⌉ = 9 bits — the counter equals the budget on exhaustion.)

## Implementation lessons

- **Slot conventions met reality fast.** The spec's first draft reserved
  md slots 1–3 for the system; the l2l3l4 parser demo writes five values
  and immediately clobbered slot 0 (ingress) — the conformance suites
  caught it as flood lookups keying on DMAC bytes. Revision: slot 0 is
  the ONLY system slot; 1–7 are program-pair space. One system slot is
  the honest minimum.
- **Amaranth shared memories want ports at construction time.** The
  composed core owns one window memory; PP takes a read port
  (`pktmem=`, `pkt_base=32`) and MAP its read+write ports (`winmem=`) in
  their `__init__`s — ports cannot be added once the memory elaborates.
  The fill FSM writes through MAP's existing driver-load port; the drain
  reads through MAP's readback port at `HEADROOM − delta`. The headroom
  edit "logic" is one subtractor, as the deparser/editor doctrine
  predicted.
- **Drive-then-sample, everywhere, or off-by-one.** Both the Amaranth
  stream BFM and the Verilator switch loop initially sampled `tready`
  post-edge and lost the first byte. The discipline: judge every beat on
  the same pre-edge snapshot the DUT commits.
- **`set -e` + command substitution eats failures.** Beat 3's checker
  grepped stats fields (`delta_pos=`) the new periphery no longer prints;
  `VAR=$(grep …)` under `set -e` killed the script silently, and a
  `| tail` on the caller masked the exit code. The periphery now counts
  frames that grew/shrunk — it can't see the head delta at all, which is
  the point.
- **PEP 263 trap:** a Python heredoc whose first line comment contains
  `word: token` parses as an encoding declaration. Cost one silent no-op
  edit before detection.
- **The old `send r0, delta` encodings decode as the new bare SEND**
  (r0 = 0b000 where must-be-zero bits landed) — harmless by construction,
  and pinned by decode tests on both sides of the tripwire.

## What the demo migration deleted

`nanuk_switch.cc`: the double frame load, the 288-byte window fill with
headroom math, the hdr/smd shuttle buses, the `HEADROOM − delta` readback,
and the tail splice — all replaced by stream-in / collect-stream-out plus
md slot 0 stamping and fan-out. The switch also *gained* one duty that
belongs to it: installing the system flood table at boot. Periphery owns
policy; the core owns nothing but programs.

## State at close

Merged to main as one `--no-ff` arc. ISA v0 now: PP 12 instructions
(gained LDMD), MAP 15 (CSUM/STMD/ANDI/SHLI in; egress/flood/IPv4 out).
`nanuk_core` is the third exported Verilog module and what the demo runs.
Next moves on the table: `hw/sv` against the same core contract, the
Tiny Tapeout packaging of `nanuk_core`, and the eDSL's metadata story in
teaching material.
