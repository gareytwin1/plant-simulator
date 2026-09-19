"""
Simulation clock: deterministic time with speed control and pause.

Tracks simulated time, not wall-clock time. The simulation advances by
injected dt (seconds per step), scaled by a speed multiplier and frozen
by pause. This guarantees bit-identical replay: same seed, same config,
same sequence of step(dt) calls, identical time and state forever.

There is deliberately no call to time.time() anywhere inside a model,
because wall-clock time is the enemy of reproducibility. Simulation time
arrives only through step(dt), making runs replayable from their initial
state and seed.
"""

from typing import TypedDict


class ClockState(TypedDict):
    sim_time: float
    speed: float
    paused: bool


class SimulationClock:
    """Sim time with speed control and pause.

    The engine calls step(dt) once per iteration. dt is the nominal wall
    time for one step (typically 1.0 second), and speed scales it: at 10x
    speed, step(1.0) advances sim_time by 10 seconds. Pause freezes sim_time
    until resume() is called, without affecting the step counter or the speed.

    Pausing and resuming are state, not commands — their effect is local to
    the current step(dt) call. The caller owns the decision to pause.
    """

    def __init__(self) -> None:
        self.sim_time = 0.0
        self.speed = 1.0
        self.paused = False

    def step(self, dt: float) -> float:
        """Advance simulated time by dt * speed, unless paused.

        dt is in seconds. With speed=1.0, step(1.0) advances sim_time by 1.0.
        With speed=10.0, it advances by 10.0. If paused, sim_time does not
        change but the step still happens (the clock is frozen, not broken).

        Returns the simulated time actually applied (0.0 if paused), so a
        caller that needs to move something else by the same elapsed time
        doesn't have to re-derive it from sim_time before and after.
        """
        if self.paused:
            return 0.0

        elapsed = dt * self.speed
        self.sim_time += elapsed

        return elapsed

    def set_speed(self, speed: float) -> None:
        """Set the speed multiplier.

        speed=1.0 is real time. speed=10.0 runs 10x faster. speed=0.5 runs
        at half speed. The change takes effect immediately on the next step().
        """
        self.speed = speed

    def pause(self) -> None:
        """Pause the simulation.

        The next step(dt) call will not advance sim_time. Resume to unfreeze.
        """
        self.paused = True

    def resume(self) -> None:
        """Resume after a pause.

        The next step(dt) call will advance sim_time again.
        """
        self.paused = False

    def get_state(self) -> ClockState:
        """Return the clock's current state.

        Flat dict for inspection and serialization. It is a TypedDict
        rather than a plain state row because the engine reads sim_time
        and speed straight out of it and needs their types, not a union.
        """
        return {
            "sim_time": self.sim_time,
            "speed": self.speed,
            "paused": self.paused,
        }
