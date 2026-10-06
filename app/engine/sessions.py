"""
Per-session plant registry.

Every browser gets a plant of its own, so one visitor's actions are never
visible to another's. SessionRegistry creates, looks up and tears one down by
session id; what it holds is any session type that can be ended (`Endable`),
built by the `factory` it is given, so this module names no concrete session
type and never imports app.training. main.py passes TrainingSession.

A session owns a background Scheduler, so a browser's plant keeps running on
the server's own clock instead of a browser's setInterval. Constructing a
session starts nothing; only the route serving a page that displays the plant
starts it, and only the session's end() (called directly, by
SessionRegistry.end(), or by LRU eviction) stops it. It closes it, so a request
still holding an ended session cannot start a worker the registry no longer
counts. SessionRegistry bounds how many sessions - and so how many worker
threads - stay alive at once: past config.MAX_SESSIONS, create ends the
least-recently-touched session first, and a session idle for
config.SESSION_IDLE_SECONDS is reclaimed (T18-5). See
docs/T2-6_SCHEDULER_OWNERSHIP.md for the design this implements.

`lease` pins a session against the idle sweep while something - a console's
open stream - is using it (T16-8).
"""

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol

from app import config


class Endable(Protocol):
    """What the registry needs of a session: a way to end it."""

    def end(self) -> None: ...


class SessionRegistry[S: Endable]:
    """Bounded by capacity and by idle age.

    A page render starts a session's background scheduler worker
    (main.py), and nothing ever stops it on its own - a browser that navigates away sends
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
    more than max_sessions of them. `_lock` comes before every Scheduler
    lock (see scheduler.py). The registry is per-process.

    The cost: while a victim's worker is joined, every lookup waits, for
    as long as the step or command that worker is waiting behind. Steps
    and commands are short; that wait buys a victim that is dead, not
    dying, when create() returns. A request still holding a reclaimed
    session is safe: end() closed its schedulers.
    """

    def __init__(
        self,
        *,
        factory: Callable[[], S],
        max_sessions: int = config.MAX_SESSIONS,
        monotonic: Callable[[], float] = time.monotonic,
        idle_seconds: float = config.SESSION_IDLE_SECONDS,
    ) -> None:
        if max_sessions < 1:
            raise ValueError(f"max_sessions must be at least 1, got {max_sessions}")

        if idle_seconds <= 0:
            raise ValueError(f"idle_seconds must be positive, got {idle_seconds}")

        self._sessions: dict[str, S] = {}
        self._touched: dict[str, float] = {}
        self._holds: dict[str, int] = {}
        self._factory = factory
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
