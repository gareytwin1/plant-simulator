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
eviction) stops them. SessionRegistry bounds how many sessions — and so how
many worker threads — stay alive at once: past config.MAX_SESSIONS, create
ends the least-recently-touched session first. See
docs/T2-6_SCHEDULER_OWNERSHIP.md for the design this implements.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

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
        """Stop and join both scheduler workers. Safe to call on a session
        whose schedulers were never started."""
        self.compressor_scheduler.stop()
        self.pump_scheduler.stop()

    def step_compressor(self) -> None:
        self.compressor_engine.step(config.SIMULATION_STEP_SECONDS)

    def step_pump(self) -> None:
        self.pump_engine.step(config.SIMULATION_STEP_SECONDS)

    def compressor_state(self) -> StateRow:
        """The compressor page's row: the device's own state plus what the
        solver put on its branch and nodes.

        The discharge valve is state only until T7-1 gives it a branch of its
        own, so it drops nothing and valve_pressure_drop reports zero.

        temperature_at(spread) and characteristic(flow) are live-device
        queries over a solved value, so they cannot move into get_state().
        One step_lock acquisition covers the snapshot read and both queries,
        so all three come from the same coherent step even while the
        scheduler is running concurrently.
        """
        with self.compressor_scheduler.step_lock:
            snapshot = self.compressor_scheduler.snapshot_locked()

            flow = _flow_of(snapshot.streams)
            suction = _pressure_at(snapshot.nodes, "N-201")
            discharge = _pressure_at(snapshot.nodes, "N-202")
            spread = discharge - suction

            return {
                **snapshot.equipment["K-101"],
                "pressure": discharge,
                "suction_pressure": suction,
                "discharge_pressure": discharge,
                "spread": spread,
                "temperature": self.compressor.temperature_at(spread),
                "flow": flow,
                "compressor_pressure_rise": self.compressor.characteristic(flow),
                "valve_pressure_drop": 0.0,
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


class SessionRegistry:
    """Bounded by capacity, not by idle age.

    A page render starts up to two background scheduler workers on a
    session (Session.__init__/main.py), and nothing today ever stops them
    on its own — a browser that navigates away sends nothing. Capacity with
    least-recently-touched eviction is the safety mechanism that keeps that
    bounded: past max_sessions, create() ends the LRU session (stopping and
    joining its workers) before admitting a new one. This is deliberately
    not idle-age expiry — T18-5 owns reclaiming idle sessions.
    """

    def __init__(
        self,
        max_sessions: int = config.MAX_SESSIONS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_sessions < 1:
            raise ValueError(f"max_sessions must be at least 1, got {max_sessions}")

        self._sessions: dict[str, Session] = {}
        self._touched: dict[str, float] = {}
        self._max_sessions = max_sessions
        self._monotonic = monotonic

    def create(self, session_id: str) -> Session:
        if len(self._sessions) >= self._max_sessions:
            self._evict_least_recently_touched()

        session = Session()
        self._sessions[session_id] = session
        self._touch(session_id)
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)

        if session is not None:
            self._touch(session_id)

        return session

    def get_or_create(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = self.create(session_id)
        else:
            self._touch(session_id)
        return session

    def end(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        self._touched.pop(session_id, None)

        if session is not None:
            session.end()

    def __len__(self) -> int:
        return len(self._sessions)

    def _touch(self, session_id: str) -> None:
        self._touched[session_id] = self._monotonic()

    def _evict_least_recently_touched(self) -> None:
        lru_id = min(self._touched, key=self._touched.__getitem__)
        self.end(lru_id)
