# nanuk

Building a programmable packet processor from chip to programming language. 🐻‍❄️

**[Landing page](https://qobilidop.github.io/nanuk/)** ·
**[Playground](https://qobilidop.github.io/nanuk/play/)** — the eDSL, IR,
and assembly, live in your browser, with a step-scrubber debugger that
walks each packet through the IR interpreter and an instruction-set
simulator in lockstep.

nanuk is an educational project: a programmable packet-processing
pipeline of two sibling ISAs (inspired by
[xISA](https://xsightlabs.com/wp-content/uploads/2025/03/XISA_Public-.pdf))
— a **parser** engine that classifies packets and a **match-action**
engine whose lookup tables ARE the forwarding policy. Both are specified
formally in [Sail](https://github.com/rems-project/sail) with generated
golden-model emulators, assemblers, a Python eDSL compiling to a protobuf
IR (with step-exact interpreters and a Z3 symbolic executor), and Amaranth
RTL cores cosimulated against the specs — demonstrated end to end by
pushing real traffic through the composed pipeline in a SimBricks network
simulation: table-driven forwarding, live policy swaps, and a two-switch
tunnel speaking an invented protocol. A Tiny Tapeout chip is deferred to
future work. See [docs/superpowers/specs/](docs/superpowers/specs/) for
the full design.

## Layout

```
spec/     Sail ISA specs (the source of truth: parser-model/ + map-model/), emulators, pcap rig
spec/isa/ nanuk-isa: dependency-free assemblers, encodings, and instruction-set simulators
lang/     Python eDSL (parser programs and match-action programs -> the IR)
compiler/ protobuf nanuk IR: schema, validation, lowerings, interpreters, symbolic executor
hw/       Amaranth RTL cores (cosimulated against the specs) + SimBricks demos
examples/ Parser + match-action programs (hand-written asm and eDSL pairs)
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
