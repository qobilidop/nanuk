# The Switch

*In this chapter we wrap the core in a switch and push real traffic through it — two
unmodified Linux hosts, exchanging pings through our own RTL inside a full-system
network simulation. We will see how little the switch actually is (it is "pure
periphery"), watch a table become a forwarding policy you can change mid-ping, and see
broadcast flooding implemented not as silicon but as a table entry. This is rung three
of the ladder from the introduction — system-level, end-to-end — and it is where the
project stops being a model and starts being a network device.*

The core from the last chapter is a pure function: frame and metadata in, verdict and
edited frame and metadata out. It has no notion of a port, a broadcast, or a MAC-address
table. That is by design — Chapter 4 spent a whole redesign evicting exactly those
notions from the ISA. So to make a *switch*, we have to put that policy *back* — but on
the outside, in periphery around the core, where it belongs. This chapter is about how
thin that periphery gets to be once the core carries no system semantics.

## SimBricks, and RTL as a network component

The end-to-end simulation runs in SimBricks, a full-system network simulator that stitches
together heterogeneous simulators — QEMU for hosts, a NIC model, and network components —
into one synchronized, deterministic run. Our job is to make the Nanuk core a *network
component*: a thing you can drop between two hosts' NICs in place of an ordinary Ethernet
switch.

We did not build that integration from scratch. Two existing SimBricks examples donated the
pattern: one showed how to run a Verilator'd RTL model as a network component with a clocked
main loop, and another supplied the current port-and-connection API and the command-line
conventions the orchestrator emits. Our switch component combines both, plus a flag to load
the program. The Verilog is verilated into a C++ model, the switch component clocks it at 250
MHz, and per clock it synchronizes its ports, polls for arriving frames, drives the model
through a falling-edge/rising-edge pair, and advances simulated time. The whole thing runs
locally — no cloud required — because the SimBricks runtime can round-trip an experiment
description through JSON and execute it in a local runtime. That JSON round-trip is a recurring
design pressure: the experiment scripts have to use stock, serializable orchestration classes,
which shapes how a few things get done.

## The switch is pure periphery

Here is the whole switch, in one sentence: it streams each frame into the core with the ingress
port id stamped into metadata slot zero, collects the possibly-rewritten output frame, and fans
it out to the ports named in the egress bitmap the program left in metadata slot zero. That is
it. That is the switch.

Everything policy-shaped lives in two places, and neither is the core. The *slot-zero convention*
is the contract between the switch and whatever program is loaded: ingress port id goes in on slot
zero, egress port bitmap comes out on slot zero. The switch stamps the ingress id on the way in;
after the core strobes its result with a "sent" verdict, the switch reads slot zero, ANDs it down
to the available ports, and transmits the frame to each port in the bitmap. A "drop" verdict drops;
an "error" verdict is logged and dropped. The head-delta from a tunnel push or pop is *invisible*
to the switch — it just sees, at readback, that the frame grew or shrank, and forwards whatever came
out. The controller inside the switch is a tiny three-state machine: idle, stream the frame in one
byte per cycle with "last" on the final byte, then collect the output stream and fan it out. The
switch never even backpressures the core.

The programs and tables load through the core's write-only control port, and this is where "pure
periphery" earns the phrase. An earlier version of the switch did far more: it instantiated the two
processors as *separate* Verilator models and performed the parser-to-match-action handoff itself, in
C++ — a couple hundred lines of frame-copying, metadata-shuttling, delta-math glue. The core redesign
from Chapter 4 pulled all of that *into the RTL*: the switch now instantiates a single composed core
that does the handoff internally, and the C++ glue that used to shuttle data between two models simply
deleted itself. The double frame load, the headroom arithmetic, the metadata shuttle buses, the delta
readback, the tail splice — all gone, replaced by "stream in, collect stream out, stamp slot zero, fan
out." The periphery got thin precisely because the core got general.

## The table is the policy — demonstrated

Chapter 4 made the claim; here it becomes something you can watch happen. The demo topology is two
Linux hosts, each with a NIC, both wired to one Nanuk switch, with one host pinging the other. The
programs — a parser and a match-action program — never change across the demo. Only *table state*
changes, and the switch's behavior changes with it.

The tables hot-reload. The switch watches its table file's modification time and, *only between frames*
so a packet never sees a half-programmed table, reloads it when it changes. That single mechanism turns
the demo into three beats.

In the first beat, the tables are empty. Every forwarding lookup misses, falls through to flooding, and
the ping works at zero percent loss — the switch behaves like a dumb hub because that is what an empty
forwarding table means.

In the second beat, we install real L2 forwarding entries mapping each host's MAC to its port. Now the
ping is *unicast*: the traffic is delivered directly, and the flood counter shows exactly one flooded
frame — the ARP broadcast that legitimately has to flood — while every ICMP echo goes straight to its
port. Then, the demonstration: we remap one host's MAC to the *wrong* port. One hundred percent packet
loss. Same silicon, same parser program, same match-action program — *only the table changed*, and the
network went dark. That is the thesis made physical: the table is the forwarding policy, and reprogramming
it mid-ping reprograms the switch.

The third beat is the tunnel. Two Nanuk switches sit back to back, directly wired. The first pushes a
22-byte outer header onto frames bound for the far host — the invented "nanukproto" tunnel from the ISA
design — and the second strips it. The ping runs cleanly, ten for ten, and the outer header is visible
*only* on the wire between the two switches. Two switches, running the same core, speaking a protocol no
commercial switch has ever heard of, because we invented it and wrote a program to parse and build it.

## Flood as a table

The nicest detail in the whole switch is how broadcast flooding works, because it works the way Chapter 4
discovered it should: **flooding is not special-cased anywhere in the datapath — it is one more exact-match
table lookup.** The core has no notion of "broadcast." The switch has no flood logic in its fan-out. Instead,
the switch's control plane installs, at boot, a *system flood table*: one entry per ingress port, mapping it
to "every port but this one." The match-action program's forwarding logic falls through, on a lookup miss, to
a second lookup against that flood table, keyed by the ingress id the system placed in metadata slot zero.

So the L2 forwarding program is small and complete: look up the destination MAC; on a hit, send to the port
the table returned; on a miss, look up the ingress port in the flood table and send to that bitmap; and if
even the flood table is unconfigured, drop — fail closed. Flooding is *table content installed by the
periphery*, not a semantic of the core. Change the topology, reinstall the flood table, same program. The core
has no idea broadcast exists; broadcast is an ordinary table entry plus a program fall-through.

There is a second forwarding mode worth mentioning, because it shows the periphery owning policy from the other
direction. A pure translator or middlebox program — the IPv4-to-IPv6 translator of Chapter 13, say — rewrites
the packet but makes no forwarding decision, leaving slot zero untouched. For those, the switch has a
"middlebox flood" mode: ignore the program's egress decision and flood all-but-ingress itself, so a two-port
bump-in-the-wire translator delivers to the far side. Egress, in the end, stays the packaging's call — the core
edits, the periphery routes.

## Where this bit us

The full-system demo is where the gap between "the RTL is correct" and "the demo runs" gets paved with a
surprising number of small, real potholes, and the honest ones are worth keeping.

QEMU randomizes each NIC's MAC address on every boot. That quietly breaks the entire "install a forwarding table
keyed by MAC" plan, because you cannot write the table entries ahead of a run if you do not know the MACs until
the guests boot — and harvesting them after boot cannot survive across runs. The fix exploits a seam in the
orchestration: the NIC *simulator* has a settable MAC that survives the experiment's JSON round-trip, so the demos
pin the MACs to known values and write the tables ahead of time. It is a small thing that cost a run to figure out,
and it is the kind of thing no amount of RTL correctness prevents.

The flood assertion taught a lesson about honest metrics. The tempting thing to assert in beat two is "zero frames
flooded" — a clean, satisfying number. But the ARP broadcast *legitimately* floods, so asserting exact zero would
have meant suppressing correct behavior to make a test pass. The honest claim is "the *bulk* is unicast — every ICMP
echo — and exactly one ARP floods." When a metric tempts you to break correct behavior to hit a round number, the
metric is wrong, not the behavior.

And the switch-to-switch tunnel topology was blocked, briefly, by nothing more than an inherited allow-list: the switch
component already knew how to open a listening socket for a network-to-network link, but a base class forbade such links
by default. A one-line patch in the experiment module — which the JSON round-trip happily carried — unlocked
switch-to-switch topologies. A reminder that at the system-integration layer, half the battle is not protocol or timing
but the accreted defaults of the tools you are composing.

We have now climbed three rungs of the ladder. The parser and the match-action processor are specified in Sail, built in
Amaranth, proven conformant by cosimulation, composed into a core, wrapped in a switch, and pushing real traffic between
real Linux hosts — with the table, not the silicon, as the policy. What we have been writing by hand this entire time,
though, is *assembly*. Part III climbs back up the stack to fix that: an assembler and a simulator, an intermediate
representation, and the language we actually wanted to write these programs in all along.
