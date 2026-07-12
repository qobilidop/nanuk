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
spec/sail/   Sail ISA models (the source of truth: model/{parser,map}) and the
             generated golden-model emulators
sw/python/   The nanuk package, three descending abstraction levels: nanuk.lang
             (eDSL) -> nanuk.ir (protobuf IR, lowerings, interpreters, symex)
             -> nanuk.isa (assemblers, encodings, ISS). Plus its test suite
             (tests/; the golden-model pcap rig = tests/golden) and
             nanuk.testkit, the conformance machinery every suite shares.
hw/amaranth/ The RTL below the ISA: Amaranth parser + MAP cores
             (nanuk_amaranth), with cosim tests judging them against the ISS
             oracle and the Sail golden models.
examples/    Example programs: hand-written asm paired with its eDSL twin
demo/        The end-to-end SimBricks demo staging the examples on the RTL cores
docs/        Design docs, plans, and lab notes
```

## The demo

`demo/build_and_run.sh` runs the end-to-end demo: two QEMU Linux
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
./dev.sh cmake -S spec/sail -B spec/sail/build
./dev.sh cmake --build spec/sail/build

# Sail model tests + emulator smoke test
./dev.sh ctest --test-dir spec/sail/build --output-on-failure

# The SW suite: ISA, IR, eDSL, golden-model pcap rig, and playground bridge
./dev.sh bash -lc 'cd sw/python && uv sync && NANUK_COSIM=1 uv run pytest tests ../../web/py/tests'

# The HW suite: Amaranth cores (NANUK_COSIM=1 also runs RTL-vs-oracle cosim)
./dev.sh bash -lc 'cd hw/amaranth && uv sync && NANUK_COSIM=1 uv run pytest tests'
```

The first thing nanuk ever parsed: `examples/l2l3l4/parse.asm` — Ethernet,
802.1Q (incl. QinQ), IPv4 (incl. options), and UDP parsed by an
11-instruction ISA, verified against a scapy-generated pcap corpus on the
Sail golden model.

## License

[Apache-2.0](LICENSE)
