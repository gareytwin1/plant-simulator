"""
Per-session plant registry.

main.py used to hold one module-global compressor and pump, so every
browser shared the same plant and one visitor's actions were visible to
everyone else's. A Session bundles a fresh device instance of each kind;
SessionRegistry creates, looks up and tears one down by session id, so
concurrent browsers never see each other's state.

Since T2-6, a Session also owns a background Scheduler per engine —
compressor_scheduler and pump_scheduler — so a browser's plant keeps
running on the server's own clock instead of a browser's setInterval.
Session.__init__ constructs both and starts neither; only the route
serving the page that displays a machine starts its scheduler, and only
Session.end() (called directly, by SessionRegistry.end(), or by LRU
eviction) stops them. It closes them, so a request still holding an ended
session cannot start a worker the registry no longer counts.
SessionRegistry bounds how many sessions - and so how many worker threads -
stay alive at once: past config.MAX_SESSIONS, create ends the
least-recently-touched session first, and a session idle for
config.SESSION_IDLE_SECONDS is reclaimed (T18-5). See docs/T2-6_SCHEDULER_OWNERSHIP.md
for the design this implements.

The registry holds any session type that can be ended (`Endable`): the legacy
Session here, with two workers, or a TrainingSession (app.training.session),
with one. It takes a factory for the type it builds and defaults to Session,
so this module never imports app.training. `lease` pins a session against the
idle sweep while something - a console's open stream - is using it (T16-8).
"""

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol, overload

from app import config
from app.engine.engine import Engine
from app.engine.scheduler import Scheduler
from app.equipment.compressor import GasCompressor
from app.equipment.pump import CentrifugalPump
from app.plant.loader import load_plant
from app.statetypes import JSONValue, StateRow


# The standalone pages each run one machine between two fixed battery limits.
# Equal boundary pressures are the legacy pages' own defaults, so an idle
# machine sits at zero flow exactly as it always did.
COMPRESSOR_PLANT: dict[str, Any] = {
    "nodes": [
        {"id": "N-201", "boundary": True, "pressure": 750.0, "domain": "gas"},
        {"id": "N-202", "boundary": True, "pressure": 750.0, "domain": "gas"},
    ],
    "equipment": [
        {"tag": "K-101", "type": "compressor", "node_in": "N-201", "node_out": "N-202", "design": {}},
    ],
}

PUMP_PLANT: dict[str, Any] = {
    "nodes": [
        {"id": "N-101", "boundary": True, "pressure": 50.0, "domain": "liquid"},
        {"id": "N-102", "boundary": True, "pressure": 50.0, "domain": "liquid"},
    ],
    "equipment": [
        {"tag": "P-101", "type": "pump", "node_in": "N-101", "node_out": "N-102", "design": {}},
    ],
}


class Session:
    """One browser's plant: a compressor and a pump, each on an Engine of its
    own. They are separate plants because they sit in separate flow domains
    and the pages step them independently.
    """

    def __init__(
        self,
        compressor_plant: dict[str, Any] = COMPRESSOR_PLANT,
        pump_plant: dict[str, Any] = PUMP_PLANT,
    ) -> None:
        self.compressor_engine = _engine_for(compressor_plant)
        self.pump_engine = _engine_for(pump_plant)

        compressor = self.compressor_engine.equipment["K-101"]
        pump = self.pump_engine.equipment["P-101"]

        assert isinstance(compressor, GasCompressor)
        assert isinstance(pump, CentrifugalPump)

        self.compressor = compressor
        self.pump = pump

        # Created here, started only by the route that renders the page
        # displaying the matching engine (main.py). An unstarted Scheduler
        # holds no thread, so this adds no cost to a plain API request.
        self.compressor_scheduler = Scheduler(self.compressor_engine)
        self.pump_scheduler = Scheduler(self.pump_engine)

    def end(self) -> None:
        """Close both schedulers, stopping and joining any worker. Permanent:
        a later start() is a no-op. Safe to call on a session whose
        schedulers were never started."""
        self.compressor_scheduler.close()
        self.pump_scheduler.close()

    def compressor_state(self) -> StateRow:
        """The compressor page's row: the device's own state plus what the
        solver put on its branch and nodes.

        The page carries no valve: T7-2 retired the compressor's own valve
        state, and this plant has no valve branch to read one from. The
        reference plant config/plants/olefins_lite.yaml is where K-101
        discharges through a real ControlValve.

        temperature_at(suction, discharge) and characteristic(flow) are
        live-device queries over a solved value, so they cannot move into
        get_state(). One step_lock acquisition covers the snapshot read and
        both queries, so all three come from the same coherent step even
        while the scheduler is running concurrently.
        """
        with self.compressor_scheduler.step_lock:
            snapshot = self.compressor_scheduler.snapshot_locked()

            flow = _flow_of(snapshot.streams)
            suction = _pressure_at(snapshot.nodes, "N-201")
            discharge = _pressure_at(snapshot.nodes, "N-202")

            return {
                **snapshot.equipment["K-101"],
                "pressure": discharge,
                "suction_pressure": suction,
                "discharge_pressure": discharge,
                "spread": discharge - suction,
                "temperature": self.compressor.temperature_at(
                    suction,
                    discharge,
                ),
                "flow": flow,
                "compressor_pressure_rise": self.compressor.characteristic(flow),
            }

    def pump_state(self) -> StateRow:
        with self.pump_scheduler.step_lock:
            snapshot = self.pump_scheduler.snapshot_locked()

            flow = _flow_of(snapshot.streams)
            suction = _pressure_at(snapshot.nodes, "N-101")
            discharge = _pressure_at(snapshot.nodes, "N-102")

            return {
                **snapshot.equipment["P-101"],
                "flow": flow,
                "suction_pressure": suction,
                "discharge_pressure": discharge,
                "spread": discharge - suction,
                "pump_pressure_rise": self.pump.characteristic(flow),
            }


def _engine_for(config_dict: dict[str, Any]) -> Engine:
    return Engine.from_plant(load_plant(config_dict))


def _flow_of(streams: Mapping[str, Mapping[str, JSONValue]]) -> float:
    # The single-device plants have exactly one stream.
    (stream,) = streams.values()

    return _number(stream["flow"])


def _pressure_at(nodes: Mapping[str, Mapping[str, JSONValue]], node_id: str) -> float:
    return _number(nodes[node_id]["pressure"])


def _number(value: JSONValue) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)

    return float(value)


class Endable(Protocol):
    """What the registry needs of a session: a way to end it."""

    def end(self) -> None: ...


class SessionRegistry[S: Endable]:
    """Bounded by capacity and by idle age.

    A page render starts up to two background scheduler workers on a legacy
    session (Session.__init__/main.py), a training session runs one, and
    nothing ever stops them on its own - a browser that navigates away sends
    nothing. Two mechanisms keep that bounded. Idle age (T18-5):
    reclaim_idle() ends every session last touched `idle_seconds` or more ago,
    and get() and create() run it under `_lock` before they look anything up,
    so there is no reaper thread and the sweep follows the injected clock.
    Capacity: past max_sessions, create() ends the least-recently-touched
    session (stopping and joining its workers) before admitting a new one. On
    a server that receives no requests at all, abandoned workers wait for the
    next one.

    A session in use is leased (T16-8). `lease(session_id)` counts a hold
    under `_lock`: reclaim_idle() skips a held session, and releasing the
    last hold touches it, so its idle clock starts at the release. Capacity is
    the backstop: create() prefers the least-recently-touched session that is
    not held, and ends a held one only when every session is, so the
    registry never exceeds max_sessions. The holder then finds its session
    ended.

    Admission is atomic. create() builds its session outside `_lock`, then
    under it either returns the entry another thread already admitted
    (ending the unstarted loser) or reclaims, evicts, inserts and touches.
    Reclaim, eviction and end() close the victim while holding `_lock`, so
    once create() returns the victim's workers are dead, and there are never
    more than 2 * max_sessions of them. `_lock` comes before every Scheduler
    lock (see scheduler.py). The registry is per-process.

    The cost: while a victim's worker is joined, every lookup waits, for
    as long as the step or command that worker is waiting behind. Steps
    and commands are short; that wait buys a victim that is dead, not
    dying, when create() returns. A request still holding a reclaimed
    session is safe: end() closed its schedulers.
    """

    @overload
    def __init__(
        self: "SessionRegistry[Session]",
        max_sessions: int = ...,
        monotonic: Callable[[], float] = ...,
        idle_seconds: float = ...,
    ) -> None: ...

    @overload
    def __init__(
        self,
        max_sessions: int = ...,
        monotonic: Callable[[], float] = ...,
        idle_seconds: float = ...,
        *,
        factory: Callable[[], S],
    ) -> None: ...

    def __init__(
        self,
        max_sessions: int = config.MAX_SESSIONS,
        monotonic: Callable[[], float] = time.monotonic,
        idle_seconds: float = config.SESSION_IDLE_SECONDS,
        *,
        factory: Callable[[], Any] | None = None,
    ) -> None:
        if max_sessions < 1:
            raise ValueError(f"max_sessions must be at least 1, got {max_sessions}")

        if idle_seconds <= 0:
            raise ValueError(f"idle_seconds must be positive, got {idle_seconds}")

        self._sessions: dict[str, S] = {}
        self._touched: dict[str, float] = {}
        self._holds: dict[str, int] = {}
        # Resolved at call time, so the default always builds this module's Session.
        self._factory: Callable[[], Any] = factory if factory is not None else lambda: Session()
        self._max_sessions = max_sessions
        self.idle_seconds = idle_seconds
        self._monotonic = monotonic
        self._lock = threading.Lock()

    def create(self, session_id: str) -> S:
        session: S = self._factory()

        with self._lock:
            self._reclaim_idle()
            existing = self._sessions.get(session_id)

            if existing is not None:
                session.end()
                self._touch(session_id)
                return existing

            if len(self._sessions) >= self._max_sessions:
                self._evict_least_recently_touched()

            self._sessions[session_id] = session
            self._touch(session_id)
            return session

    def get(self, session_id: str) -> S | None:
        with self._lock:
            self._reclaim_idle()
            session = self._sessions.get(session_id)

            if session is not None:
                self._touch(session_id)

            return session

    def peek(self, session_id: str) -> S | None:
        """The live session for `session_id`, or None. Read-only: it neither
        refreshes the session's idle timer nor sweeps idle sessions, so a
        monitoring probe cannot keep a plant alive or end anyone else's. A
        session past `idle_seconds` is still returned until the next get()
        or create() sweeps it."""
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create(self, session_id: str) -> S:
        session = self.get(session_id)

        return session if session is not None else self.create(session_id)

    @contextmanager
    def lease(self, session_id: str) -> Iterator[None]:
        """Hold `session_id`'s session against the idle sweep for the life of
        the `with` block. Nothing is taken until the block is entered, so a
        caller may build the lease and never enter it.

        Leases count, so overlapping holders each keep the session alive.
        Leaving the last one touches the session: its idle clock starts at the
        release, not at its last lookup. A lease on a session that is not in
        the registry, or that was ended and replaced meanwhile, holds nothing.
        Capacity eviction still ends a leased session when every session is
        leased.
        """
        with self._lock:
            session = self._sessions.get(session_id)

            if session is not None:
                self._holds[session_id] = self._holds.get(session_id, 0) + 1

        try:
            yield
        finally:
            if session is not None:
                with self._lock:
                    self._release(session_id, session)

    def reclaim_idle(self) -> int:
        """End every session idle for `idle_seconds` or more, and return how
        many. get() and create() already do this; call it directly to sweep
        without a lookup. A leased session is never idle."""
        with self._lock:
            return self._reclaim_idle()

    def end(self, session_id: str) -> None:
        with self._lock:
            self._end(session_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    # The helpers below expect the caller to hold _lock.

    def _touch(self, session_id: str) -> None:
        self._touched[session_id] = self._monotonic()

    def _release(self, session_id: str, session: S) -> None:
        # Only the session the lease was taken on: an ended one took its holds
        # with it, and a new session under the same id owns its own.
        if self._sessions.get(session_id) is not session:
            return

        holds = self._holds[session_id] - 1

        if holds > 0:
            self._holds[session_id] = holds
            return

        del self._holds[session_id]
        self._touch(session_id)

    def _end(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        self._touched.pop(session_id, None)
        self._holds.pop(session_id, None)

        if session is not None:
            session.end()

    def _reclaim_idle(self) -> int:
        now = self._monotonic()
        idle = [
            session_id
            for session_id, touched in self._touched.items()
            if now - touched >= self.idle_seconds and session_id not in self._holds
        ]

        for session_id in idle:
            self._end(session_id)

        return len(idle)

    def _evict_least_recently_touched(self) -> None:
        candidates = [session_id for session_id in self._touched if session_id not in self._holds]
        lru_id = min(candidates or self._touched, key=self._touched.__getitem__)
        self._end(lru_id)
