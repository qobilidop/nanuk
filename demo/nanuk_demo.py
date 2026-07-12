"""nanuk e2e demo experiment: two QEMU Linux hosts with i40e NICs connected
through the Verilator'd nanuk parser switch (parser-gated flood forwarding).

Based on SimBricks' experiments/minimal_net.py, with the switch simulator's
executable pointed at a wrapper script that launches nanuk_hw with the demo
parser program. Uses only stock orchestration classes so JSON round-trips
(used by the local runtime) keep working.

Run inside the SimBricks environment:
    python -m simbricks.local nanuk_demo.py --verbose --repo /simbricks
"""

from simbricks.orchestration import instantiation as inst
from simbricks.orchestration import simulation as sim
from simbricks.orchestration import system
from simbricks.orchestration.helpers import instantiation as inst_helpers
from simbricks.orchestration.helpers import simulation as sim_helpers

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

switch0 = system.EthSwitch(sys)
switch0.connect_eth_peer_if(nic0._eth_if)
switch0.connect_eth_peer_if(nic1._eth_if)

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

# Point the switch simulator at the nanuk component (wrapper script bakes in
# the parser program path). Executable is serialized in toJSON, so this
# survives the local runtime's JSON round-trip.
for s in simulation.all_simulators():
    if isinstance(s, sim.SwitchNet):
        s._executable = "sims/net/nanuk/nanuk_run.sh"

# Pin the NIC MACs (QEMU randomizes them otherwise) so table files can be
# written ahead of the run — deterministic keys for the L2 FDB.
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
