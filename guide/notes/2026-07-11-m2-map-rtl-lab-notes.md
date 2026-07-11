# Lab notes: M2 — the MAP in silicon-shaped form, and the table becomes the policy

*2026-07-11. Covers the MapCore Amaranth RTL, its cosim/fuzz verification
against the M1 Sail model, and the SimBricks demos where forwarding policy
moved from code to table state.*

## MapCore: the EXT lesson, applied from birth

NanukCore learned the hard way (stage 4) that wide combinational datapaths
produce pathological Verilator output. MapCore was designed sequential from
the start: LD/ST stream bytes over the window memory port (n ≤ 8 cycles),
LOOKUP scans table entries one per cycle (≤ 64), CSUMUPD streams the IPv4
header through a 24-bit accumulator. Result: 4.9k lines of Verilog, same
ballpark as the parser's 5.2k, compiled without drama. `steps` still counts
instructions, not cycles, so the cosim contract is cycle-agnostic.

All 22 unit tests (mirroring every spec/map-test assertion) passed on the
first pysim run; the full cosim rig (three demo programs × corpus × ingress
ports, tunnel round-trip, composed PP→MAP vs run_pipeline) passed with zero
divergences on its first run too. Writing the Sail model first — and
pinning both sides to the same golden vectors — continues to pay for
itself.

## What differential fuzzing caught that nothing else did

The new MAP fuzz leg (random packets × random tables through map_l2fwd,
plus raw random programs) failed 7/25 cases immediately: every SEND on a
packet ≥ 256 bytes error-halted in RTL but sent in the golden model.
Root cause: `plen_min.as_signed()` — a 9-bit value of 256 reinterpreted as
−256 in the send-range comparison. The demo corpus never has a 256+-byte
packet; the corpus-driven cosim was green while the bug sat there. Lesson
recorded: **Amaranth `.as_signed()` on a value that uses its full width is
a reinterpretation, not a conversion — negate in a wider signed signal
instead.** A second, smaller find: the RTL driver's frame lacked the >256B
tail-passthrough rule that run_map applies (harness contract, not core
behavior — now mirrored).

## SimBricks: the composed pipeline in the switch

nanuk_hw now instantiates BOTH Verilator models. Per frame: PP parses
(existing flow), and on accept the controller wires the PP's output
contract straight into the MAP's input ports (hdr offsets/present, SMD,
ingress = rx port), zero-fills headroom + window, starts the MAP, then
reads the possibly-rewritten frame back byte-by-byte and transmits to every
port in the egress bitmap. Tables load from a text file (same line format
as the M1 emulator ctx) and hot-reload on mtime change between frames.

Gotchas that cost a run each:

- **QEMU randomizes NIC MACs per boot.** Harvest-then-rerun cannot work
  across runs; the NIC *simulator* has a settable `mac` (survives the
  orchestration's JSON round-trip), so the demos pin
  `02:6e:61:00:00:01/02` and table files are written ahead of time.
- **`flooded=0` was the wrong assertion.** The ARP broadcast legitimately
  floods; the honest claim is "the bulk (every ICMP echo) is unicast."
  Asserting exact zero would have required suppressing correct behavior.
- **macOS bash 3.2** has no `declare -A`. Again. (Plain variables.)

## The beats

1. **Flood (beat 1):** empty tables → LOOKUP misses → `md_flood`; ping
   0% loss through the composed RTL.
2. **The table is the policy (beat 2):** L2 FDB entries → ping with
   `sent=24 flooded=1` (only the ARP broadcast); then host1's MAC mapped
   to the wrong port → 100% loss. Same silicon, same PP program, same MAP
   program — only table state changed. This is the M2 thesis demonstrated.
3. **Tunnel (beat 3):** host0 – sw_encap – sw_decap – host1, two nanuk
   switches with a direct SimBricks net-to-net link. Encap pushes the 22B
   nanukproto outer header on host1-bound frames, decap strips it:
   `delta_pos=11` / `delta_neg=11`, ping 10/10, outer MAC visible only on
   the inter-switch wire. Orchestration note: `SwitchNet.run_cmd` already
   emits `-h` for listen sockets; only its inherited
   `supported_socket_types` forbade net-net links — a one-line class patch
   in the experiment module (in-process JSON round-trip keeps it) unlocked
   switch-to-switch topologies.

## Scoreboard

- hw suite: 113 tests (22 MapCore unit, 5 cosim suites, 25+20 fuzz legs,
  61 pre-existing) — green.
- Whole repo local CI-equivalent: 374 pytest + 12 ctest + 2 Sail
  type-checks — green.
- SimBricks: beats 1+2+3 asserted by script (`run_beats12.sh`,
  `run_beat3.sh`), logs under `hw/simbricks/out/`.

## Next

M3 per the extension design doc: eDSL + IR grow tables/actions/MAP
programs; playground grows MAP support; re-check "IR closed under
optimization" against table ops; the IR interpreter's cost model extends to
the MAP lowering.
