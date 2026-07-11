# nanuk

Building a programmable packet processor from chip to programming language. 🐻‍❄️

nanuk is an educational project: a packet-parser ISA (inspired by
[xISA](https://xsightlabs.com/wp-content/uploads/2025/03/XISA_Public-.pdf))
specified formally in [Sail](https://github.com/rems-project/sail), with a
generated golden-model emulator, an assembler, a Python eDSL that compiles
to a protobuf IR, and an Amaranth RTL core cosimulated against the spec —
demonstrated end to end by pushing real traffic through the core in a
SimBricks network simulation. A Tiny Tapeout chip is deferred to future
work. See [docs/superpowers/specs/](docs/superpowers/specs/) for the full
design.

## Layout

```
spec/     Sail ISA spec (the source of truth), emulator CLI, assembler, harness
lang/     Python eDSL (compiles protocol declarations + parse graphs to the IR)
compiler/ protobuf nanuk IR: schema, validation, IR -> assembly lowering, interpreter
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

# RTL, eDSL, and IR test suites (NANUK_COSIM=1 also runs the
# RTL-vs-golden-model cosimulation)
./dev.sh bash -lc 'cd hw && uv sync && NANUK_COSIM=1 uv run pytest tests'
./dev.sh bash -lc 'cd lang && uv sync && NANUK_COSIM=1 uv run --group dev pytest tests'
./dev.sh bash -lc 'cd compiler && uv sync && NANUK_COSIM=1 uv run --group dev pytest tests'
```

The first thing nanuk ever parsed: `examples/l2l3l4/parse.asm` — Ethernet,
802.1Q (incl. QinQ), IPv4 (incl. options), and UDP parsed by an
11-instruction ISA, verified against a scapy-generated pcap corpus on the
Sail golden model.

## License

[Apache-2.0](LICENSE)
