"""
Background scheduler — physics advances on the server's clock, not a browser's.

One Scheduler owns one Engine and steps it on a worker thread at a fixed
wall-clock cadence, whether or not anything is asking for a snapshot. That is
what gradual upsets and unattended trips need, and it is why a browser is
never the thing that moves time.

Wall time appears here and nowhere else: the scheduler reads a monotonic
clock only to decide when the next step is due. Each step hands the Engine
the same fixed dt — the nominal wall seconds per step, which the
SimulationClock scales by its speed — so a slow host makes the simulation
run slowly, never differently. dt is never stretched to catch up.

Cadence is scheduled against an intended deadline, not by sleeping an
interval after each step, so the time a step takes does not accumulate as
drift. A step is *slow* when it finishes after the next deadline has already
arrived. That is logged as a warning carrying the interval, the step's
duration and the overrun. No physics step is skipped or merged: an overrun
smaller than one interval is caught up by running the next step at once; one
of a whole interval or more re-anchors the schedule to now, so a stall
(a suspended laptop) cannot release a burst of steps. The wall time not
simulated in that case is reported in the warning, never hidden.

Pause belongs to the clock and the scheduler has no flag of its own. A
paused clock applies zero elapsed time, so the scheduler keeps ticking at
its cadence and the steps change nothing; resuming needs no catch-up.

Concurrency. The Engine has no locking and a snapshot read walks live
devices, so nothing may read or mutate it while a step runs. `step_lock` is
held for the duration of engine.step() and nothing else — never across
logging or waiting. Consumers read `snapshot()`, which returns the last
Snapshot the worker published: immutable (C4) and built inside the lock, so
it can never be a half-finished step. Anything else that mutates the engine
while the worker runs, such as an operator action, takes `step_lock` first.

Failure. If engine.step() raises, the worker logs the traceback, records the
exception on `error` and stops. It does not restart; a step that failed
once, on identical inputs, would fail again. Starting the scheduler again is
an explicit decision for whoever owns it.

Shutdown is explicit. stop() sets an event the worker waits on, so it wakes
at once rather than sleeping out its interval, and joins the thread. The
worker is a daemon only as a backstop against a forgotten stop().
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

from app import config
from app.engine.snapshot import Snapshot


logger = logging.getLogger(__name__)


class Steppable(Protocol):
    """What the scheduler needs of an engine. Structural, so the scheduler
    depends on the Engine's public interface and nothing more."""

    def step(self, dt: float) -> Snapshot: ...

    def snapshot(self) -> Snapshot: ...


class Cadence:
    """Deadline arithmetic, kept apart from the thread so it is testable
    without one. Times are seconds on whatever monotonic clock the caller
    reads; this class never reads a clock itself."""

    def __init__(self, interval: float, start: float) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")

        self.interval = interval
        self.deadline = start

    def delay(self, now: float) -> float:
        return max(0.0, self.deadline - now)

    def complete(self, started: float, finished: float) -> float | None:
        """Record a finished step and move to the next deadline.

        Returns the overrun in seconds if the step was slow — it finished
        after the next deadline had already arrived — otherwise None.
        """
        next_deadline = self.deadline + self.interval

        if finished <= next_deadline:
            self.deadline = next_deadline

            return None

        overrun = finished - next_deadline

        # Under a whole interval behind, the next step runs at once and the
        # schedule heals. Further behind than that, the missed slots are not
        # replayed as a burst.
        self.deadline = finished if overrun >= self.interval else next_deadline

        return overrun


class Scheduler:
    def __init__(
        self,
        engine: Steppable,
        step_seconds: float = config.SIMULATION_STEP_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if step_seconds <= 0:
            raise ValueError(f"step_seconds must be positive, got {step_seconds}")

        self.engine = engine
        self.step_seconds = step_seconds
        self.step_lock = threading.Lock()
        self.error: BaseException | None = None

        self._monotonic = monotonic
        self._lifecycle = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: Snapshot | None = None

    @property
    def running(self) -> bool:
        thread = self._thread

        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the worker. A no-op if one is already running."""
        with self._lifecycle:
            if self.running:
                return

            self.error = None
            self._stopping = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(self._stopping,),
                name="engine-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the worker and join it. Safe to call again, or when never
        started."""
        with self._lifecycle:
            thread = self._thread

            if thread is None:
                return

            if thread is threading.current_thread():
                raise RuntimeError("the scheduler cannot be stopped from its own worker")

            self._stopping.set()
            thread.join()
            self._thread = None

    def snapshot(self) -> Snapshot:
        """The last published snapshot, or a fresh one if nothing has been
        published yet. Never a step in progress."""
        latest = self._latest

        if latest is not None:
            return latest

        with self.step_lock:
            return self.engine.snapshot()

    def _run(self, stopping: threading.Event) -> None:
        cadence = Cadence(self.step_seconds, self._monotonic())

        while not stopping.wait(cadence.delay(self._monotonic())):
            started = self._monotonic()

            try:
                with self.step_lock:
                    self._latest = self.engine.step(self.step_seconds)
            except Exception as exc:
                self.error = exc
                logger.exception("engine step failed, scheduler stopping")

                return

            finished = self._monotonic()
            overrun = cadence.complete(started, finished)

            if overrun is not None:
                logger.warning(
                    "slow simulation step: interval %.3fs, step took %.3fs, "
                    "%.3fs past the next deadline",
                    self.step_seconds,
                    finished - started,
                    overrun,
                )
