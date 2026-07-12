"""Nanuk M2 beat-3 experiment: two hosts, TWO Nanuk switches in series —
host0 - sw_encap - sw_decap - host1. sw_encap runs the tunnel_push MAP
program (nanukproto L2-in-L2 encap for DMACs in its tunnel table); sw_decap
runs parse_tunnel + tunnel_pop (strip the outer header, flood). Ping
traffic host0 -> host1 crosses the switch-switch link encapsulated.

Per-switch programs/tables come from NANUK_DIR-style directories baked into
two wrapper scripts (nanuk_run_encap.sh / nanuk_run_decap.sh, staged by
run_beat3.sh).

Run inside the SimBricks environment:
    python -m simbricks.local nanuk_demo_tunnel.py --verbose --repo /simbricks
"""

from simbricks.orchestration import instantiation as inst
from simbricks.orchestration import simulation as sim
from simbricks.orchestration import system
from simbricks.orchestration.helpers import instantiation as inst_helpers
from simbricks.orchestration.helpers import simulation as sim_helpers
from simbricks.orchestration.instantiation import socket as inst_socket
from simbricks.orchestration.system.eth import EthInterface

# SwitchNet.run_cmd already emits -h for listen sockets; only the
# supported_socket_types it inherits from NetSim forbids net-to-net links.
# Widen it so two Nanuk switches can peer (nanuk_switch implements listening
# via NetListenPort). Class-level patch: survives the local runtime's
# in-process JSON round-trip.
sim.SwitchNet.supported_socket_types = lambda self, interface: {
    inst_socket.SockType.CONNECT,
    inst_socket.SockType.LISTEN,
}

sys = system.System()

distro_disk_image = system.DistroDiskImage(sys, "base")

host0 = system.I40ELinuxHost(sys)
host0.add_disk(distro_disk_image)
host0.add_disk(system.LinuxConfigDiskImage(sys, host0))

nic0 = system.IntelI40eNIC(sys)
nic0.add_ipv4("10.0.0.1")
host0.connect_pcie_dev(nic0)

host1 = system.I40ELinuxHost(sys)
host1.add_disk(distro_disk_image)
host1.add_disk(system.LinuxConfigDiskImage(sys, host1))

nic1 = system.IntelI40eNIC(sys)
nic1.add_ipv4("10.0.0.2")
host1.connect_pcie_dev(nic1)

sw_encap = system.EthSwitch(sys)
sw_encap.connect_eth_peer_if(nic0._eth_if)      # encap port 0: host0

sw_decap = system.EthSwitch(sys)
sw_decap.connect_eth_peer_if(nic1._eth_if)      # decap port 0: host1

# Switch-to-switch link: an interface on sw_encap (port 1), connected from
# sw_decap (its port 1).
link_if = EthInterface(sw_encap)
sw_encap.add_if(link_if)
sw_decap.connect_eth_peer_if(link_if)

ping_client_app = system.PingClient(host0, nic1._ip)
ping_client_app.wait = True
host0.add_app(ping_client_app)
host1.add_app(system.Sleep(host1, infinite=True))

simulation = sim_helpers.simple_simulation(
    sys,
    compmap={
        system.FullSystemHost: sim.QemuSim,
        system.IntelI40eNIC: sim.I40eNicSim,
        system.EthSwitch: sim.SwitchNet,
    },
)

# Point each switch simulator at its wrapper (which bakes in the per-switch
# program/table directory). Order: match the system objects.
_wrappers = {
    sw_encap: "sims/net/nanuk/nanuk_run_encap.sh",
    sw_decap: "sims/net/nanuk/nanuk_run_decap.sh",
}
for s in simulation.all_simulators():
    if isinstance(s, sim.SwitchNet):
        for comp in s.components():
            if comp in _wrappers:
                s._executable = _wrappers[comp]

# Pin the NIC MACs (QEMU randomizes them otherwise); distinct from the
# tunnel's outer MACs (02:4e:4b:...) by design.
_nic_macs = {nic0: "02:6e:61:00:00:01", nic1: "02:6e:61:00:00:02"}
for s in simulation.all_simulators():
    if isinstance(s, sim.I40eNicSim):
        for comp in s.components():
            if comp in _nic_macs:
                s.mac = _nic_macs[comp]

instantiation = inst_helpers.simple_instantiation(simulation)
fragment = inst.Fragment()
fragment.add_simulators(*simulation.all_simulators())
instantiation.fragments = [fragment]

instantiations = [instantiation]
