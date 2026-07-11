# nanuk × SimBricks integration

The e2e demo: two QEMU Linux hosts with i40e NICs exchange traffic through
the Verilator'd nanuk parser core, wrapped as a SimBricks network component
with parser-gated flood forwarding (verdict accept ⇒ flood to other ports,
anything else ⇒ drop). The loaded parser program decides what the switch
passes — reload a different program, get a different switch.

## Recon findings (2026-07-11, SimBricks main @ shallow clone)

- **Precedent**: `sims/net/menshen/menshen_hw.cc` is the canonical
  "Verilator RTL as network component" integration (clocked main loop);
  `sims/net/switch/net_switch.cc` has the current port/connection API
  (`Prepare`/`ConnectAll`/`SimBricksBaseIfEstablish`) and the argv
  conventions the orchestrator's `SwitchNet.run_cmd` emits
  (`-S <sync> -E <lat> [-u] -s <sock>... -h <sock>...`). `nanuk_hw.cc`
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
  directly (commands mirror `rules.mk`).
- **Experiment template**: `experiments/minimal_net.py` is exactly the demo
  topology (2 × QEMU host + i40e NIC + EthSwitch + PingClient).
- macOS: image is amd64-only; runs under Rosetta. QEMU guests run TCG —
  slow boots, fine for ping.

## Files

- `nanuk_hw.cc` — the component (ports + clocked Verilator loop + parser-gated
  flood controller)
- `rules.mk` — in-tree build rules (for a full SimBricks checkout;
  `build_and_run.sh` compiles directly instead)
- `nanuk_demo.py` — the experiment (stock classes only)
- `nanuk_run.sh` — executable wrapper baking in the program path
- `build_and_run.sh` — end-to-end driver: assembles the parser program,
  exports Verilog, builds the component in the SimBricks container, runs the
  experiment, checks ping output
