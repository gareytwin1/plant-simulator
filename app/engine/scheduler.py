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
published Snapshot: immutable (C4) and built inside the lock, so it can never
be a half-finished step.

Everything that touches the engine goes through one of three paths, and each
publishes what it leaves behind. The worker steps and publishes. command()
applies an operator action and republishes without advancing time, so a
response read straight after it sees the action. step_once() is the manual
step: it is refused (returns None) while the worker runs or once the
scheduler is closed, and otherwise steps and publishes. A worker stopped by
an error does not count as running, so a manual step is still allowed.

Lock order: `_lifecycle`, then `step_lock`, and a session-registry lock,
where one guards the sessions that own schedulers, comes before both. Never
take an earlier lock while holding a later one. The worker and command()
take only `step_lock`; step_once() takes `_lifecycle` then `step_lock`, so it
cannot interleave with start(), stop() or close(). A request handler may
hold a registry lock only while resolving its session, never around a
command or a step.

Failure. If engine.step() raises, the worker logs the traceback, records the
exception on `error` and stops. It does not restart; a step that failed
once, on identical inputs, would fail again. Starting the scheduler again is
an explicit decision for whoever owns it.

Shutdown is explicit. stop() sets an event the worker waits on, so it wakes
at once rather than sleeping out its interval, and joins the thread. The
worker is a daemon only as a backstop against a forgotten stop(). close()
stops the worker and marks the scheduler closed for good: after it, start()
is a no-op and a manual step is refused.
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
        self._closed = False

    @property
    def running(self) -> bool:
        thread = self._thread

        return thread is not None and thread.is_alive()

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> None:
        """Start the worker. A no-op if one is already running or the
        scheduler is closed."""
        with self._lifecycle:
            if self._closed or self.running:
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
            self._stop()

    def close(self) -> None:
        """Stop the worker and mark the scheduler closed, permanently: start()
        is a no-op and a manual step is refused from then on. Waits for a
        manual step in progress to finish."""
        with self._lifecycle:
            self._closed = True
            self._stop()

    def command(self, action: Callable[[], object]) -> Snapshot:
        """Apply an operator action and publish the result without advancing
        time. Returns the snapshot it published."""
        with self.step_lock:
            action()
            self._latest = self.engine.snapshot()

            return self._latest

    def step_once(self) -> Snapshot | None:
        """Step the engine once by hand and publish the result. Refused, and
        returns None without stepping, while the worker runs or once the
        scheduler is closed."""
        with self._lifecycle:
            if self._closed or self.running:
                return None

            with self.step_lock:
                self._latest = self.engine.step(self.step_seconds)

                return self._latest

    def _stop(self) -> None:
        # The caller holds _lifecycle.
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

    def snapshot_locked(self) -> Snapshot:
        """The last published snapshot, or a fresh one built directly if
        nothing has been published yet.

        Contract: the caller already holds `step_lock`. This method must
        never acquire it itself — `step_lock` is a `threading.Lock`, not
        reentrant, and acquiring it here would deadlock the caller. Callers
        that also need a live-device query (one that takes a solved value,
        such as `characteristic(flow)`) take `step_lock` once, call this
        instead of `snapshot()`, and make those queries inside the same
        `with` block so the whole read comes from one coherent step.
        """
        latest = self._latest

        if latest is not None:
            return latest

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
