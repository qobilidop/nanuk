# Nanuk × SimBricks integration

The e2e demo: two QEMU Linux hosts with i40e NICs exchange traffic through
the Verilator'd Nanuk core (nanuk_core: the composed PP→MAP datapath
behind its streaming face), wrapped as a SimBricks network
component. The parser program gates what enters (verdict ≠ accept ⇒ drop),
the MAP program + tables decide forwarding (lookup hit ⇒ egress bitmap,
miss ⇒ program's choice, e.g. flood). The loaded programs and tables ARE
the switch policy — reload different ones, get a different switch. The
demo programs are assembled from the repo-root `examples/`.

## Recon findings (2026-07-11, SimBricks main @ shallow clone)

- **Precedent**: `sims/net/menshen/menshen_hw.cc` is the canonical
  "Verilator RTL as network component" integration (clocked main loop);
  `sims/net/switch/net_switch.cc` has the current port/connection API
  (`Prepare`/`ConnectAll`/`SimBricksBaseIfEstablish`) and the argv
  conventions the orchestrator's `SwitchNet.run_cmd` emits
  (`-S <sync> -E <lat> [-u] -s <sock>... -h <sock>...`). `nanuk_switch.cc`
  combines both, plus `-f <prog.bin>` / `$NANUK_PROG` for the program.
- **Local execution**: the new cloud-first flow is NOT required —
  `python -m simbricks.local <experiment.py> --repo /simbricks` runs fully
  locally (`LocalSimpleRuntime`). Experiment scripts expose module-level
  `instantiations`; the runtime JSON-round-trips the simulation, so the
  experiment uses only stock orchestration classes and points
  `SwitchNet._executable` (serialized) at `nanuk_run.sh`, which bakes in the
  program path.
- **Runtime image**: `simbricks/simbricks-local:latest` (amd64) contains the
  built tree at `/simbricks` (sims, static libs `libnetwork.a`/`libbase.a`,
  headers), verilator/make/g++, QEMU, the guest disk image
  (`images/output-base/base`), and the installed `simbricks.*` Python
  packages. No top-level Makefile in the image ⇒ we invoke verilator/g++
  directly (see `build_component.sh`).
- **Experiment template**: `experiments/minimal_net.py` is exactly the demo
  topology (2 × QEMU host + i40e NIC + EthSwitch + PingClient).
- macOS: image is amd64-only; runs under Rosetta. QEMU guests run TCG —
  slow boots, fine for ping.

## Files

- `nanuk_switch.cc` — the component (ports + clocked Verilator loop driving both
  cores: PP verdict gates, MAP verdict + tables forward, head-delta applied
  at readback; `-x` opt-in middlebox mode floods all-but-ingress instead of
  reading `md[0]`, for programs that rewrite but take no forwarding decision)
- `nanuk_demo.py` / `nanuk_demo_tunnel.py` / `nanuk_demo_siit.py` — the
  experiments (stock classes only; single switch / two switches with a
  nanukproto tunnel between them / a v4-only and a v6-only guest either side
  of the SIIT translator)
- `nanuk_run.sh` — executable wrapper selecting per-switch prog/map/tables
- `siit_responder.py` — userspace AF_PACKET ICMPv6 echo responder + received-
  frame classifier for the SIIT scenario's v6 guest (the SimBricks base guest
  kernel has `CONFIG_IPV6=n`, so no kernel IPv6 stack exists to answer with)
- `build_component.sh` — exports Verilog (nanuk-export), verilates natively,
  compiles + links `out/nanuk_switch` in the SimBricks container
- `build_and_run.sh` — e2e smoke: build component, assemble programs from
  `examples/`, run the ping experiment, check output
- `run_beats12.sh` / `run_beat3.sh` — the M2 demo beats (table-is-the-policy;
  tunnel push/pop)
- `run_siit.sh` — the SIIT beats (ping / iperf UDP / TTL=1 negative gate /
  iperf TCP) across the v4↔v6 boundary, reconciled against the switch's own
  counters and the receiver's per-datagram log
- `build_guest_kernel.sh` — rebuilds the image's guest kernel with
  `CONFIG_IPV6=y` (one-config-flag delta, cached in `out/`); the iperf TCP
  beat boots it so the v6 guest has a real kernel TCP/IPv6 stack

NIC model note: the SIIT scenario uses **E1000**, not i40e — the `i40e_bm`
behavioral model delivers shrunk (v6→v4) frames to the guest as all-zeros
(root-caused in the SIIT arc; the datapath is identical either way).
