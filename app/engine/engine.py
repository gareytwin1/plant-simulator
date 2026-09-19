"""
Simulation engine — advances equipment and publishes snapshots.

Owns the clock and a tag-keyed collection of equipment. Each step
integrates every device by the same elapsed simulated time — never
touching flow or pressure, per the Equipment contract (C1) — then
publishes one immutable Snapshot (C4), the only thing the rest of the
system reads.

There is no plant-wide algebraic solve yet: that arrives with the
network solver (app/plant/topology.py, T4-2). Until then the snapshot's
solver section reports a trivial converged placeholder, and the
equipment section reflects only what integrate(dt) moved — a device's
own flow and pressure, where it still computes them standalone through
its own step(), do not change from stepping through the engine.

start()/stop() and the snapshot's running field delegate entirely to the
clock's pause/resume. There is deliberately no second "is it running"
flag to keep in sync with the clock's own — a stopped engine is exactly
a paused clock.

Per-device speed multipliers (e.g. GasCompressor.simulation_speed) are
not consulted here: the clock is the sole speed authority for anything
driven through the Engine. A device's own simulation_speed only matters
on the legacy path where the device still drives its own step() directly
(see test_engine.py's simulation_speed test for what that means).

dt is always injected by the caller, never defaulted from config or read
from a wall clock — determinism depends on the caller owning time.
"""

from app.engine.clock import SimulationClock
from app.engine.snapshot import build_snapshot


class Engine:
    def __init__(self, equipment=()):
        self.clock = SimulationClock()
        self.equipment = {}

        for device in equipment:
            self.add_equipment(device)

    def add_equipment(self, device):
        """Register a device, keyed by its own tag.

        The only way to add equipment, so the dict key and device.tag can
        never diverge.
        """
        self.equipment[device.tag] = device

    def start(self):
        self.clock.resume()

    def stop(self):
        self.clock.pause()

    def step(self, dt):
        """Advance the clock by dt, integrate every device by the elapsed
        simulated time the clock actually applied, and publish the
        resulting snapshot.

        While stopped (the clock paused), the clock applies zero elapsed
        time, and integrate(0) is a no-op per the Equipment contract — so
        stepping a stopped engine changes nothing.
        """
        elapsed = self.clock.step(dt)

        for device in self.equipment.values():
            device.integrate(elapsed)

        return self.snapshot()

    def snapshot(self):
        equipment_state = {
            tag: device.get_state()
            for tag, device in self.equipment.items()
        }

        clock_state = self.clock.get_state()

        return build_snapshot(
            sim_time=clock_state["sim_time"],
            speed=clock_state["speed"],
            running=not clock_state["paused"],
            equipment=equipment_state,
        )
