# Core Redesign Plan 3/3: System/Demo Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The SimBricks demo rides the composed `nanuk_core` streaming face; `nanuk_switch.cc` shrinks to pure periphery per the spec.

**Architecture:** One `Vnanuk_core` instance per switch. The component streams each frame in with the ingress port id in md slot 0, collects the output stream (tail passthrough now lives inside the core), and fans out per md_out slot 0. Programs and tables load through the core's control port; the switch installs the system flood table (t3) at boot — flooding is periphery policy.

**Tech Stack:** Verilator 5 (devcontainer), SimBricks container toolchain, QEMU hosts.

## Global Constraints

- The nanuk_switch CLI (`-f/-m/-t/-s/-h/-S/-E/-u`) and the tables.txt format are unchanged — the beat scripts and experiment configs keep working as-is.
- Same drive-then-sample discipline as the Amaranth BFM: judge stream beats on the pre-edge snapshot the core acts on.
- Acceptance: `demo/run_beats12.sh` (flood / unicast / table-flip) and `demo/run_beat3.sh` (two-switch nanukproto tunnel) pass.

### Task 1: nanuk_switch.cc on the streaming face

**Files:**
- Modify: `demo/nanuk_switch.cc` (single `Vnanuk_core`; ctrl-port loading incl. `program_flood_table`; kIdle → kStream → kRun controller)
- Modify: `demo/build_component.sh` (export/verilate/link `nanuk_core` only)
- Modify: `demo/README.md`

- [ ] Rewrite the component: the window math, hdr/smd shuttle buses, delta readback, and tail splice all vanish — output = the collected stream verbatim.
- [ ] Component builds: `FORCE_BUILD=1 demo/build_component.sh`.
- [ ] Beats pass: `demo/run_beats12.sh`, then `demo/run_beat3.sh`.
- [ ] Commit: `feat(demo): nanuk_switch rides the composed core's streaming face`.
