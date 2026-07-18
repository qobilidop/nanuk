# The Playground

**What you'll understand:** how Nanuk put its *actual* compiler in a browser tab —
not a JavaScript reimplementation, but the same Python wheel the tests run — and why
that "no rewrite, no third implementation" rule is the same single-source-of-truth
doctrine that governs the Sail spec. You'll see three synchronized panes tied
together by one cost model, a step-scrubber that diffs two levels of execution and
flags any divergence as a bug, presets baked from committed bytes, and a licensing
boundary held by construction rather than by promise.

Everything in this book so far has been something you *read about*. The playground
is where a learner *does* it: type an eDSL program, watch it become IR and assembly,
feed it a packet, and scrub through the execution one instruction at a time. Try it —
[the SIIT translator](https://qobilidop.github.io/nanuk/play/?program=siit),
[the invented tunnel protocol](https://qobilidop.github.io/nanuk/play/?program=nanukproto),
or [L2 forwarding](https://qobilidop.github.io/nanuk/play/?program=map_l2fwd). The
design challenge was to make it interactive *without* forking the truth.

## The actual repo code, in the browser

The foundational rule is stated as doctrine: *"the playground executes the actual
repo code — no rewrite, no third implementation; the single-source-of-truth
principle extends to the website."* This is not a convenience; it's a correctness
guarantee. A JavaScript reimplementation of the interpreter would be a *fourth* thing
that could drift from the Sail spec, and drift silently, because nobody diffs the
website against the emulator. So the playground runs the Python package unmodified,
via Pyodide (CPython compiled to WebAssembly).

The build is deliberately plain: the same `uv build --wheel` that produces the
package for PyPI produces the wheel the browser loads, written into the web assets
next to a small `bridge.py`. In the browser, micropip installs that wheel (pulling
protobuf from PyPI as its one dependency) and loads the bridge, which imports the
*real* modules — the parser interpreter, the two instruction-set simulators — the
same classes pytest imports. That `bridge.py` runs both under the test suite in CI
*and* inside Pyodide via `runPython`, so the glue itself is tested on the ground
before it flies in the browser. When the SIIT work grew the language a new ALU op
and a constant primitive (Chapters 12–13), the wheel the browser loads grew with it,
from 53,439 to 55,717 bytes — the tell that the playground runs the same IR the core
does, not a snapshot of it.

## Three panes, one cost model

The playground shows three synchronized CodeMirror panes: the eDSL (editable), the IR
(read-only), and the assembly (read-only). Edit the eDSL and the other two
regenerate. Hover over a parser state in any pane and the corresponding lines
highlight in all three — Compiler-Explorer-style *provenance*.

The provenance is where the architecture is elegant. The cross-pane correspondence
isn't computed by fuzzy text matching; it's computed by the *same cost model* that
governs the lowering and the interpreter's step counting. The bridge walks the eDSL's
syntax tree for state functions and finds their line ranges; it finds the assembly's
label blocks and their instruction bodies; and the IR ranges come from the bridge's
own deterministic renderer. Then it emits, per state, a bundle tying the three line
ranges together — plus an op-level IR-to-assembly mapping that follows *exactly the
same per-op emission counts* the lowering uses (a dispatch case is two instructions,
a re-anchor mark is zero, and so on).

The lab notes call this *"one cost model, three jobs"*: the same emission counts drive
the lowering, the interpreter's step parity, and the UI's highlighting. That's the
deep payoff of the cost-model discipline from Chapter 8. Because the interpreter's
step count *is* the lowering's instruction count, and because the highlighting is
computed from the same counts, the three panes stay aligned by construction — an op
that lowers to two instructions highlights two assembly lines, always, because the
alternative would require the cost model to disagree with itself. (A small UI touch
worth noting: panes only scroll to a hover when they are *not* the one under the
cursor, so hovering never yanks the text out from under you.)

## The step-scrubber: two levels, diffed live

The playground's second version turned it from a viewer into a debugger, and the
design goal is the differential methodology made interactive: *"run a packet, then
scrub through the execution step by step, watching the same moment at two levels at
once."* The two levels are the IR interpreter and an assembly-level
instruction-set simulator — the ISS of Chapter 7, described in the v2 design as the
*"fourth implementation"* of Nanuk semantics, now running live in the browser
alongside the interpreter.

You scrub with a transport bar and a slider (or the arrow keys, or a play loop), and
at every step two state cards show you the same instant at both levels: the IR card
shows the current state, op, and named values; the assembly card shows the program
counter, the four registers, the cursor, and the effects. The alignment is free
because *"the step counter is the shared clock"* — the interpreter mirrors the
lowering's cost model instruction-for-instruction, so the two traces line up by step
index with no fuzzy matching.

And then the crucial part: the two levels are *diffed*, and a disagreement is a bug.
A badge reads "levels agree" in green, or — clickable, jumping you to the offending
step — "levels diverged at step N ... this is a Nanuk bug" in red. The diff compares
*architectural* state at each step boundary: for the parser, the cursor, header
presence, header offsets, and metadata; for the MAP, the window writes and lookups.
What it pointedly does *not* diff is register contents — *"display-only, never
diffed: the value-to-register correspondence is the lowering's choice, not
semantics."* That's the same distinction the parity tests draw (Chapter 9): compare
the semantics that must match, never the scheduling that legitimately differs. The
playground turns the whole verification story of the book into something a learner
can *watch*: two independent implementations of the same machine, stepping in
lockstep, with a red badge waiting to fire the instant they disagree. (For a composed
MAP run, the trace is two-phase — a baked parser phase feeding the MAP phase — and the
byte under the parser's cursor lights up in the packet-hex panel as it advances.)

## Presets baked from committed bytes

A playground needs example packets, and packet-crafting normally means a library like
scapy. Nanuk refuses to ship one, so presets are *baked* offline. A generator script
runs in the development container (where scapy lives), builds the example packets, and
emits only their hex bytes into a JSON file the browser fetches. *"scapy runs at
generation time; only hex strings ship."*

The SIIT presets go one better: they're read *straight from the committed conformance
vectors* — the byte-exact, scapy-free vectors from Chapter 13 — so no packet-crafting
library touches the SIIT half of the corpus at all. The five SIIT presets
(`udp46_len25_ttl64`, `udp64_len25_ttl64`, `edge_eamt_dst_46`, `icmp46_len25_ttl64`,
`neg_v4_ttl_expired`) are frozen vector bytes, not freshly generated ones. In the
browser, a preset is just a named hex string scoped to the programs it belongs to;
click a chip and the packet loads and runs, and a URL like `?preset=qinq` seeds it at
load. Presets are *committed data*, not runtime computation — which means the browser
never needs the library that made them.

## The scapy boundary, held by construction

That preset design isn't only ergonomics; it's how a licensing boundary stays intact.
scapy is GPL-2.0, and Nanuk is Apache-2.0 — formally incompatible. Using scapy to
*build test packets in the dev environment* triggers no distribution obligation, but
shipping it inside a distributed artifact would. So the rule is absolute and
structural: *"scapy never enters a distributed artifact,"* and *"the playground
honors this by construction."*

Held by *construction* is the operative phrase. The boundary isn't a code-review note
that someone might miss; it's enforced by what the wheel physically contains. The
shipped wheel carries the IR, ISA, and lang modules and nothing else — no scapy, no
solver, no test kit — and its only declared dependency is protobuf. Anything the
browser would otherwise need from the test kit is *baked as Python constants* in the
bridge, with a tripwire test holding those constants identical to the test kit's
output. The SIIT EAMT tables, for instance, are hard-coded in the bridge with a
comment explaining exactly why: *"the wheels never ship testkit (the scapy
boundary)."* The presets are hex, the tables are constants, the wheel is
dependency-minimal — three independent mechanisms all pointing the same way, so that
"scapy never ships" is a fact about the artifact rather than a hope about the process.
When a licensing rule is a property you can `grep` the wheel to verify, it can't quietly
decay.

## Where this bit us

The playground's honest scars are the ones that reveal *what kind* of failure a
faithful mirror produces. The SIIT landing exposed that the IR renderer had never had
a case for the reg-reg ALU ops — because SIIT was the *first* MAP program to use them
— so the IR pane, the assembly pane, and the two-level trace would have *silently
desynced* the moment anyone ran SIIT through the debugger. Not a crash: a renderer that
drops an op class fails by drawing a *plausible but wrong* picture. That's the
dangerous failure mode for a teaching tool, because a learner has no way to know the
picture is lying. It was caught, a case added, and a check confirmed that one MAP op
still lowers to exactly one assembly instruction so the provenance partition the
scrubber depends on stays aligned.

The related bite is the bridge's program *dispatch*: because it execs arbitrary source,
it can't know a program's *name*, only its *shape*, so it fingerprints the compiled MAP
by its table signature and picks the SIIT rig on a match. That's a genuine tradeoff, not
a clean design — a future MAP with a colliding signature would be mis-routed silently —
and the mitigation is characteristic of this project: a guard test compiles every bridge
program and asserts no two share a signature, so the day a collision arrives, a test
fails *at that commit* instead of the browser quietly running the wrong rig years later.
Both finds share the playground's central lesson: a tool whose whole value is showing
you the truth fails most dangerously when it shows you something *plausible* instead, so
the defenses have to be aimed not at crashes but at silent, believable wrongness.
