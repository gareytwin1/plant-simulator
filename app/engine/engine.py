"""
Simulation engine — advances equipment and publishes snapshots.

Owns the clock and the set of equipment under simulation. Each step
integrates every device by the same elapsed simulated time — never
touching flow or pressure, per the Equipment contract (C1) — then
publishes one immutable Snapshot (C4), the only thing the rest of the
system reads.

There is no plant-wide algebraic solve yet: that arrives with the
network solver (app/plant/topology.py, T4-2). Until then the snapshot's
solver section reports a trivial converged placeholder, and the
equipment section reflects only what integrate(dt) moved — a device's
own flow and pressure, where it still computes them standalone, do not
change from stepping through the engine.

dt is always injected by the caller, never defaulted from config or read
from a wall clock — determinism depends on the caller owning time.
"""

from app.engine.clock import SimulationClock
from app.engine.snapshot import build_snapshot


class Engine:
    def __init__(self, equipment=None):
        self.clock = SimulationClock()
        self.running = True
        self.equipment = dict(equipment or {})

    def add_equipment(self, device):
        self.equipment[device.tag] = device

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def step(self, dt):
        """Advance the clock by dt, integrate every device by the elapsed
        simulated time, and publish the resulting snapshot.

        The elapsed time handed to integrate() is read back from the
        clock's own advance rather than recomputed from speed and pause
        here, so a paused or sped-up clock cannot disagree with what the
        equipment experiences.
        """
        before = self.clock.sim_time
        self.clock.step(dt)
        elapsed = self.clock.sim_time - before

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
            running=self.running,
            equipment=equipment_state,
        )
