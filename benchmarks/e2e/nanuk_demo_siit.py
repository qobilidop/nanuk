"""Nanuk SIIT e2e experiment: a v4-only QEMU guest and a v6-only QEMU guest
converse across address families through the Verilator'd Nanuk core running
the SIIT translator (examples/siit/{parse,translate}.asm).

Topology:  v4guest(eth0) --- port0 [nanuk_switch: siit] port1 --- v6guest(eth0)

The switch runs in middlebox flood mode (-x, staged by the wrapper): the
translate.asm MAP program rewrites the frame but leaves md[0] untouched
("egress stays the packaging's call"), so the two-port switch forwards each
translated frame out the far port. MACs pass through the translator
unchanged, so the v4 guest's static ARP entry points straight at the v6
NIC's pinned MAC -- no ARP/ND across the translator (ND/ARP are non-IP or
non-echo ICMPv6, dropped by the parser/translator anyway).

The v6 side runs a userspace IPv6 echo responder (siit_responder.py) on an
AF_PACKET raw socket instead of a kernel IPv6 stack: the SimBricks base
guest kernel (linux-5.15.93) is built `# CONFIG_IPV6 is not set`, so it has
no IPv6 stack -- `ip -6 addr add` returns "Operation not supported". AF_PACKET
works at layer 2 and needs no kernel IP stack, so the guest still speaks real
IPv6 on the wire and the SIIT core is exercised for real in both directions.

Addressing (frozen DEMO_SIIT):
  v4 guest   198.51.100.2   seen by the v6 side as 64:ff9b::c633:6402 (RFC 6052)
  v6 guest   2001:db8:1::c001  reached from the v4 side as 192.0.2.1 (EAMT)

Beat selection via the SIIT_BEAT env var (default "ping"):
  ping        v4 guest: ping -c 10 192.0.2.1            -> expect 10/10
  iperf_udp   v4 guest -> v6 guest iperf UDP through the translator
  iperf_tcp   v4 guest -> v6 guest iperf TCP through the translator
  ttl         v4 guest: ping -c 12 -t 1 192.0.2.1       -> 100% loss (dropped)

Run inside the SimBricks environment:
    python -m simbricks.local nanuk_demo_siit.py --verbose --repo /simbricks
"""

import os

from simbricks.orchestration import instantiation as inst
from simbricks.orchestration import simulation as sim
from simbricks.orchestration import system
from simbricks.orchestration.helpers import instantiation as inst_helpers
from simbricks.orchestration.helpers import simulation as sim_helpers

BEAT = os.environ.get("SIIT_BEAT", "ping")

# Pinned NIC MACs (QEMU randomizes otherwise); the guests' static neigh/ARP
# entries below reference these directly, since the translator passes MACs
# through unchanged.
V4_MAC = "02:6e:61:00:00:01"
V6_MAC = "02:6e:61:00:00:02"

# The translator grows the head by 20B on v4->v6 (IPv4 20B -> IPv6 40B). To
# keep translated frames within the peer link's 1500B MTU without
# fragmentation (fragments are dropped by design), the v4 guest runs a 1480B
# MTU: a max 1480B IPv4 frame becomes a 1500B IPv6 frame. TCP MSS clamps to
# 1440 from that MTU; UDP datagram size is capped explicitly on the client.
V4_MTU = 1480
V6_MTU = 1500

V6_ADDR = "2001:db8:1::c001"

# The responder source is staged next to this experiment (run_siit.sh copies
# both into the switch dir); embed it via a heredoc so the guest writes it out
# without needing a custom orchestration class (the local runtime JSON
# round-trips only stock classes).
_RESP_SRC = open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "siit_responder.py")
).read()
assert "SIIT_RESP_EOF" not in _RESP_SRC


def stage_responder():
    return ["cat > /tmp/siit_responder.py <<'SIIT_RESP_EOF'\n" + _RESP_SRC + "\nSIIT_RESP_EOF"]


def v4_config():
    return [
        "sysctl -w net.ipv4.conf.all.rp_filter=0",
        "sysctl -w net.ipv4.conf.eth0.rp_filter=0",
        "ip addr flush dev eth0",
        f"ip link set dev eth0 mtu {V4_MTU}",
        "ip link set dev eth0 up",
        "ip addr add 198.51.100.2/24 dev eth0",
        # 192.0.2.1 (the v6 guest via EAMT) is off-link; route it out eth0 and
        # pin its L2 address to the v6 guest's NIC (the translator is L2-clean).
        "ip route add 192.0.2.0/24 dev eth0",
        f"ip neigh replace 192.0.2.1 lladdr {V6_MAC} dev eth0 nud permanent",
        "ip addr show dev eth0",
        "ip route",
    ]


def v6_config():
    # No kernel IPv6 config is possible (CONFIG_IPV6=n). The interface only
    # needs to be up with the peer-side MTU; the userspace responder owns L3.
    return [
        "ip addr flush dev eth0",
        f"ip link set dev eth0 mtu {V6_MTU}",
        "ip link set dev eth0 up",
    ] + stage_responder()


# The v6 side runs the userspace IPv6 responder in the background, then idles.
# (One extra second lets it bind the AF_PACKET socket before traffic starts.)
_run_responder = [
    f"python3 /tmp/siit_responder.py eth0 {V6_ADDR} &",
    "sleep 1",
]
v6_idle = _run_responder + ["sleep infinity"]

# The two guests boot independently (no cross-host barrier), so before the
# counted ping we poll until the v6 responder answers -- then all 10 land.
# Each poll attempt is itself one ICMP echo translated v4->v6 at the switch
# (a "grew" frame, same as the counted beats), whether or not it gets a
# reply -- so it prints the attempt count (SIIT_WARMUP_PINGS) to the guest
# console. Beat 2's report subtracts that count from the switch's `grew`
# counter so the reported translated-frame number attributes only to iperf.
_wait_up = (
    "i=0; until ping -c1 -W1 192.0.2.1 >/dev/null 2>&1; do "
    "i=$((i+1)); [ $i -ge 45 ] && break; done; "
    # +1 corrects for the successful ping (untried in `i`) on the happy path;
    # in the exhausted-45-attempts path (no v6 responder ever answers) it
    # over-reports the actual attempt count by 1 -- harmless: that path only
    # feeds a beat that fails on the connectivity check anyway.
    "echo SIIT_WARMUP_PINGS=$((i+1))"
)

# --- per-beat client (v4 guest) command tails -------------------------------
if BEAT == "ping":
    v4_beat = ["sleep 3", _wait_up, "ping -c 10 192.0.2.1"]
    v6_beat = v6_idle
elif BEAT == "ttl":
    # TTL=1: the translator refuses hop-limit <= 1 (RFC 7915) with a silent
    # DROP -- no ICMP error is generated -- so the v4 guest sees 100% loss.
    v4_beat = ["sleep 5", "ping -c 12 -t 1 192.0.2.1"]
    v6_beat = v6_idle
elif BEAT == "iperf_udp":
    # Real iperf UDP client over the translator's growing direction. -l 1400:
    # IPv4 20+8+1400=1428 <= v4 MTU; translated IPv6 40+8+1400=1448 <= v6 MTU.
    # The v6 responder receives the stream (no kernel IPv6 iperf server is
    # possible), so the client reports its own real transferred throughput.
    #
    # Rate: -b 100k (~9 datagrams/sec at -l 1400) is deliberately slow. At the
    # original -b 5m (~443 datagrams/sec), the switch's own frame counters
    # only ever saw ~12% of what iperf sent (guest sent 1353, switch grew=164
    # incl. warmup): nanuk_switch's rx_queue is bounded (RX_QUEUE_MAX) and
    # drains only as fast as the Verilator core can be simulated in real
    # time, so a guest send rate above that drain rate silently overflows the
    # queue ("rx queue full, dropping frame" -- these never reach frames_in,
    # so they are invisible to the switch's own dropped counter too). Slowing
    # the send rate well below the drain rate lets what iperf reports sending
    # reconcile against what the switch counts (run_siit.sh gates on it: the
    # switch's translated count, net of the connectivity-poll warmup, must be
    # >= 0.9x iperf's own "Sent N datagrams"). The v6 side has no real iperf
    # server (see above), so the client's UDP close handshake is never acked
    # and it retries for ~10 rounds after the main transfer -- each an
    # un-paced extra datagram, so the switch's count is expected to run
    # somewhat *above* iperf's reported send count, not below it; that is
    # the switch confirming more than the claim, never less.
    v4_beat = ["sleep 3", _wait_up, "iperf -c 192.0.2.1 -u -b 100k -l 1400 -i 1 -t 5"]
    v6_beat = v6_idle
elif BEAT == "iperf_tcp":
    # Aspirational, unexercised: the v6 guest kernel lacks CONFIG_IPV6 (no
    # kernel TCP/IPv6 stack), so there is nothing to terminate a TCP iperf
    # server on the v6 side -- see the module docstring and run_siit.sh.
    raise SystemExit(
        "nanuk_demo_siit: SIIT_BEAT=iperf_tcp is not runnable on this guest "
        "image (CONFIG_IPV6=n on the v6 side); documented, not implemented."
    )
else:
    raise SystemExit(f"nanuk_demo_siit: unknown SIIT_BEAT={BEAT!r}")


sys = system.System()

distro_disk_image = system.DistroDiskImage(sys, "base")

# NIC model: E1000, not i40e. The translator changes frame length in place, and
# the i40e_bm behavioral model delivered the shrunk v6->v4 return frames to the
# guest as all-zeros (verified: the switch emits a byte-perfect 98B IPv4 reply,
# but i40e RX hands the guest 98 zero bytes; grow/v4->v6 frames were fine). The
# E1000 model delivers both directions intact -- ping is 10/10. (The datapath
# is identical either way; this is purely a peripheral NIC-model choice.)

# --- v4-only guest (host0, switch port 0) -----------------------------------
host0 = system.E1000LinuxHost(sys)
host0.add_disk(distro_disk_image)
host0.add_disk(system.LinuxConfigDiskImage(sys, host0))

nic0 = system.IntelE1000NIC(sys)
nic0.add_ipv4("198.51.100.2")
host0.connect_pcie_dev(nic0)

client_app = system.GenericRawCommandApplication(host0, v4_config() + v4_beat)
client_app.wait = True
host0.add_app(client_app)

# --- v6-only guest (host1, switch port 1) -----------------------------------
host1 = system.E1000LinuxHost(sys)
host1.add_disk(distro_disk_image)
host1.add_disk(system.LinuxConfigDiskImage(sys, host1))

nic1 = system.IntelE1000NIC(sys)
nic1.add_ipv4("192.0.2.254")  # placeholder; flushed in v6_config (v6-only guest)
host1.connect_pcie_dev(nic1)

server_app = system.GenericRawCommandApplication(host1, v6_config() + v6_beat)
host1.add_app(server_app)

# --- the Nanuk switch -------------------------------------------------------
switch0 = system.EthSwitch(sys)
switch0.connect_eth_peer_if(nic0._eth_if)
switch0.connect_eth_peer_if(nic1._eth_if)

simulation = sim_helpers.simple_simulation(
    sys,
    compmap={
        system.FullSystemHost: sim.QemuSim,
        system.IntelE1000NIC: sim.E1000NIC,
        system.EthSwitch: sim.SwitchNet,
    },
)

# Point the switch simulator at the SIIT wrapper (bakes in prog/map/tables and
# the -x middlebox flood flag). Executable is serialized in toJSON, so this
# survives the local runtime's JSON round-trip.
for s in simulation.all_simulators():
    if isinstance(s, sim.SwitchNet):
        s._executable = "sims/net/nanuk/nanuk_run_siit.sh"

# Pin the NIC MACs (QEMU randomizes them otherwise) so the static neigh/ARP
# entries above resolve to the right peer.
_nic_macs = {nic0: V4_MAC, nic1: V6_MAC}
for s in simulation.all_simulators():
    if isinstance(s, sim.E1000NIC):
        for comp in s.components():
            if comp in _nic_macs:
                s.mac = _nic_macs[comp]

instantiation = inst_helpers.simple_instantiation(simulation)
fragment = inst.Fragment()
fragment.add_simulators(*simulation.all_simulators())
instantiation.fragments = [fragment]

instantiations = [instantiation]
