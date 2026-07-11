# nanuk

Building a programmable packet processor from chip to programming language. 🐻‍❄️

nanuk is an educational project: a packet-parser ISA (inspired by
[xISA](https://xsightlabs.com/wp-content/uploads/2025/03/XISA_Public-.pdf))
specified formally in [Sail](https://github.com/rems-project/sail), with a
generated golden-model emulator, an assembler, and — in later stages — a
Python eDSL, a protobuf IR, an RTL implementation cosimulated against the
spec, and a Tiny Tapeout capstone. See
[docs/superpowers/specs/](docs/superpowers/specs/) for the full design.

## Layout

```
spec/     Sail ISA spec (the source of truth), emulator CLI, assembler, harness
lang/     Python eDSL (compiles protocol declarations + parse graphs to asm)
hw/       Amaranth RTL core (cosimulated against the spec) + SimBricks demo
examples/ Parser programs
guide/    Lab notes and decision records
docs/     Design docs and plans
```

## The demo

`hw/simbricks/build_and_run.sh` runs the end-to-end demo: two QEMU Linux
hosts exchange real traffic through the Verilator'd nanuk parser core
inside SimBricks — `ping` works because the loaded parser program accepts
the frames. Load `examples/drop_all/parse.asm` instead and the network
goes dark: the parser program is the switch's forwarding policy.

## Quickstart

Requires Docker and the [devcontainer CLI](https://github.com/devcontainers/cli).

```bash
# Build the dev container (Sail toolchain + Python)
devcontainer build --workspace-folder .
devcontainer up --workspace-folder .

# Build the Sail model, emulator, and tests
./dev.sh cmake -B build
./dev.sh cmake --build build

# Sail model tests + emulator smoke test
./dev.sh ctest --test-dir build --output-on-failure

# Assembler + golden-model pcap tests (incl. the L2/L3/L4 demo corpus)
./dev.sh bash -lc 'cd spec/python && uv sync && uv run pytest'
```

The stage-1 demo: `examples/l2l3l4/parse.asm` — Ethernet, 802.1Q (incl.
QinQ), IPv4 (incl. options), and UDP parsed by an 11-instruction ISA,
verified against a scapy-generated pcap corpus on the Sail golden model.

## License

[Apache-2.0](LICENSE)
