# Nanuk × SimBricks integration

The e2e demo: two QEMU Linux hosts with i40e NICs exchange traffic through
the Verilator'd Nanuk core (PP→MAP), wrapped as a SimBricks network
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
  at readback)
- `nanuk_demo.py` / `nanuk_demo_tunnel.py` — the experiments (stock classes
  only; single switch / two switches with a nanukproto tunnel between them)
- `nanuk_run.sh` — executable wrapper selecting per-switch prog/map/tables
- `build_component.sh` — exports Verilog (nanuk-export), verilates natively,
  compiles + links `out/nanuk_switch` in the SimBricks container
- `build_and_run.sh` — e2e smoke: build component, assemble programs from
  `examples/`, run the ping experiment, check output
- `run_beats12.sh` / `run_beat3.sh` — the M2 demo beats (table-is-the-policy;
  tunnel push/pop)
