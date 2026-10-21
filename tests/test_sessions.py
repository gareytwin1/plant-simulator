import gc
import threading
import weakref

import pytest

from app import config
from app.engine.rng import SeededRNG
from app.engine.sessions import SessionRegistry


WAIT = 5.0


class FakeSession:
    """The least a registry needs of a session: it can be ended. `end()` takes
    `busy`, standing in for the step_lock a real session's scheduler holds
    while a step or command runs, so a test can hold a session busy."""

    def __init__(self):
        self.ended = 0
        self.busy = threading.Lock()

    def end(self):
        with self.busy:
            self.ended += 1


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_registry(**kwargs):
    return SessionRegistry(factory=FakeSession, **kwargs)


def registry_with_clock(idle_seconds=60.0, max_sessions=8):
    clock = FakeClock()
    registry = make_registry(max_sessions=max_sessions, monotonic=clock, idle_seconds=idle_seconds)

    return registry, clock


def test_create_builds_a_session_with_the_factory():
    registry = make_registry()

    session = registry.create("abc")

    assert isinstance(session, FakeSession)
    assert session.ended == 0


def test_the_factory_is_required():
    with pytest.raises(TypeError):
        SessionRegistry()


def test_get_returns_the_same_session_on_repeated_lookups():
    registry = make_registry()
    created = registry.create("abc")

    assert registry.get("abc") is created
    assert registry.get("abc") is created


def test_get_unknown_id_returns_none():
    assert make_registry().get("nope") is None


def test_get_or_create_creates_once_then_reuses():
    registry = make_registry()

    first = registry.get_or_create("abc")
    second = registry.get_or_create("abc")

    assert first is second
    assert len(registry) == 1


def test_two_ids_get_two_independent_sessions():
    registry = make_registry()

    session_a = registry.create("a")
    session_b = registry.create("b")
    registry.end("a")

    assert session_a is not session_b
    assert session_a.ended == 1
    assert session_b.ended == 0
    assert registry.get("b") is session_b


def test_end_releases_the_session_and_ends_it():
    registry = make_registry()
    session = registry.create("abc")

    registry.end("abc")

    assert session.ended == 1
    assert registry.get("abc") is None
    assert len(registry) == 0


def test_end_unknown_id_is_a_no_op():
    registry = make_registry()

    registry.end("nope")

    assert len(registry) == 0


def test_create_at_capacity_evicts_the_least_recently_touched_session():
    tick = iter(range(100))
    registry = make_registry(max_sessions=2, monotonic=lambda: next(tick))

    session_a = registry.create("a")  # touched at 0
    session_b = registry.create("b")  # touched at 1

    registry.get("b")  # touched at 2, so "a" is now the LRU session

    session_c = registry.create("c")  # over capacity, evicts "a"

    assert registry.get("a") is None
    assert registry.get("b") is session_b
    assert registry.get("c") is session_c
    assert len(registry) == 2
    assert session_a.ended == 1
    assert session_b.ended == 0


def test_touching_a_session_protects_it_from_eviction():
    tick = iter(range(100))
    registry = make_registry(max_sessions=2, monotonic=lambda: next(tick))

    session_a = registry.create("a")  # touched at 0
    registry.create("b")  # touched at 1

    registry.get("a")  # touched at 2, "b" is now the LRU session

    registry.create("c")  # evicts "b", not "a"

    assert registry.get("a") is session_a
    assert registry.get("b") is None
    assert registry.get("c") is not None


def test_registry_rejects_a_capacity_below_one():
    # Capacity of zero would make eviction pick a session from an empty
    # registry, so it is refused at construction rather than at create().
    with pytest.raises(ValueError):
        make_registry(max_sessions=0)


# Atomic admission (R7)


def constructing_together(parties):
    """A factory whose every construction waits until `parties` of them are
    under way, so racing admissions all build before any is admitted."""
    barrier = threading.Barrier(parties, timeout=WAIT)
    built = []

    def factory():
        session = FakeSession()
        built.append(session)
        barrier.wait()

        return session

    return factory, built


def run_together(target, args_list):
    results = [None] * len(args_list)

    def run(i, args):
        results[i] = target(*args)

    threads = [threading.Thread(target=run, args=(i, a)) for i, a in enumerate(args_list)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(WAIT)
        assert not thread.is_alive()

    return results


def test_concurrent_creates_at_capacity_leave_one_session_and_end_the_rest():
    ids = [f"s{i}" for i in range(8)]
    factory, built = constructing_together(len(ids))
    registry = SessionRegistry(factory=factory, max_sessions=1)

    run_together(registry.create, [(i,) for i in ids])

    assert len(registry) == 1
    (admitted,) = [registry.get(i) for i in ids if registry.get(i) is not None]
    assert [s for s in built if s.ended == 0] == [admitted]


def test_concurrent_get_or_create_of_one_id_returns_one_object():
    n = 8
    factory, built = constructing_together(n)
    registry = SessionRegistry(factory=factory)

    results = run_together(registry.get_or_create, [("abc",)] * n)

    assert all(result is results[0] for result in results)
    assert registry.get("abc") is results[0]
    assert len(registry) == 1
    assert [s for s in built if s.ended == 0] == [results[0]]


def test_eviction_while_the_victim_is_busy_completes_after_it_is_released():
    # A route mid-command on the victim holds its step_lock. Eviction waits for
    # it, then leaves the victim ended, and does not deadlock.
    registry = make_registry(max_sessions=1)
    victim = registry.create("a")
    victim.busy.acquire()

    evictor = threading.Thread(target=registry.create, args=("b",))
    evictor.start()
    evictor.join(0.2)

    assert evictor.is_alive()
    assert victim.ended == 0

    victim.busy.release()
    evictor.join(WAIT)

    assert not evictor.is_alive()
    assert victim.ended == 1
    assert registry.get("a") is None
    assert registry.get("b") is not None


def test_registry_ends_a_session_once_however_it_leaves():
    registry = make_registry(max_sessions=1)
    session = registry.create("a")

    registry.create("b")
    registry.end("a")

    assert session.ended == 1


# Idle reclaim (T18-5)


def test_idle_session_is_reclaimed_and_ended():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("abandoned")

    clock.advance(60.0)

    assert registry.reclaim_idle() == 1
    assert len(registry) == 0
    assert session.ended == 1


def test_session_idle_for_less_than_the_timeout_is_kept():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    kept = registry.create("kept")

    clock.advance(59.9)

    assert registry.reclaim_idle() == 0
    assert registry.get("kept") is kept


def test_a_touched_session_is_not_reclaimed_while_a_stale_one_is():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    busy = registry.create("busy")
    registry.create("stale")

    clock.advance(40.0)
    registry.get("busy")
    clock.advance(40.0)

    assert registry.reclaim_idle() == 1
    assert registry.get("busy") is busy
    assert len(registry) == 1


def test_get_of_an_idle_id_finds_it_gone():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    registry.create("abandoned")

    clock.advance(61.0)

    assert registry.get("abandoned") is None


def test_create_reclaims_idle_sessions_before_admitting_a_new_one():
    registry, clock = registry_with_clock(idle_seconds=60.0, max_sessions=2)
    old_a = registry.create("a")
    old_b = registry.create("b")

    clock.advance(61.0)
    fresh = registry.create("c")

    assert len(registry) == 1
    assert registry.get("c") is fresh
    assert old_a.ended == 1 and old_b.ended == 1


def test_create_of_an_idle_id_returns_a_new_session_not_the_reclaimed_one():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    old = registry.create("abc")

    clock.advance(61.0)
    fresh = registry.create("abc")

    assert fresh is not old
    assert old.ended == 1
    assert fresh.ended == 0


def test_reclaimed_session_is_released_from_memory():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    reference = weakref.ref(registry.create("abandoned"))

    clock.advance(61.0)
    registry.reclaim_idle()
    gc.collect()

    assert reference() is None


def test_the_default_idle_timeout_comes_from_config():
    assert make_registry().idle_seconds == config.SESSION_IDLE_SECONDS


@pytest.mark.parametrize("idle_seconds", [0, -1.0])
def test_registry_rejects_a_non_positive_idle_timeout(idle_seconds):
    with pytest.raises(ValueError, match="idle_seconds"):
        make_registry(idle_seconds=idle_seconds)


def test_reclaim_waits_out_a_busy_session_without_inverting_the_lock_order():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("abc")
    reclaimed = threading.Event()

    def reclaim():
        registry.reclaim_idle()
        reclaimed.set()

    clock.advance(61.0)

    with session.busy:
        thread = threading.Thread(target=reclaim)
        thread.start()
        # The registry lock is taken before the session's: reclaim holds it
        # and waits behind the busy session, it does not skip or deadlock.
        assert not reclaimed.wait(0.2)

    assert reclaimed.wait(WAIT)
    thread.join(WAIT)
    assert len(registry) == 0
    assert session.ended == 1


# Read-only lookup (T18-7)


def test_peek_returns_the_live_session_and_none_for_unknown_or_reclaimed_ids():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("a")

    assert registry.peek("a") is session
    assert registry.peek("unknown") is None

    clock.advance(61.0)
    registry.reclaim_idle()

    assert registry.peek("a") is None


def test_peek_does_not_move_the_idle_clock():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    registry.create("probed")

    for _ in range(5):
        clock.advance(20.0)
        registry.peek("probed")

    assert registry.reclaim_idle() == 1
    assert registry.peek("probed") is None


def test_peek_returns_a_session_past_the_timeout_until_something_sweeps():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("stale")

    clock.advance(61.0)

    assert registry.peek("stale") is session
    assert len(registry) == 1


def test_peek_does_not_end_other_idle_sessions():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    stale = registry.create("stale")
    registry.create("probed")

    clock.advance(30.0)
    registry.get("probed")
    clock.advance(31.0)

    assert registry.peek("probed") is not None
    assert len(registry) == 2
    assert stale.ended == 0


# Leases (T16-8)


def test_the_loser_of_a_racing_create_is_ended():
    built = []

    def factory():
        built.append(FakeSession())

        return built[-1]

    registry = SessionRegistry(factory=factory)
    first = registry.create("abc")

    assert registry.create("abc") is first
    assert len(built) == 2
    assert built[0] is first
    assert first.ended == 0
    assert built[1].ended == 1


def test_a_leased_session_survives_the_idle_sweep():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("abc")

    with registry.lease("abc"):
        clock.advance(600.0)

        assert registry.reclaim_idle() == 0
        assert registry.get("abc") is session

    assert session.ended == 0


def test_a_released_session_is_reclaimable_once_idle_counted_from_the_release():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    session = registry.create("abc")

    with registry.lease("abc"):
        clock.advance(600.0)

    # Untouched for 600 s, but its idle clock started at the release.
    clock.advance(59.0)
    assert registry.reclaim_idle() == 0

    clock.advance(1.0)
    assert registry.reclaim_idle() == 1
    assert session.ended == 1


def test_overlapping_leases_each_keep_the_session_alive():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    registry.create("abc")
    first, second = registry.lease("abc"), registry.lease("abc")

    with first:
        with second:
            pass

        clock.advance(600.0)

        assert registry.reclaim_idle() == 0

    clock.advance(60.0)

    assert registry.reclaim_idle() == 1


def test_building_a_lease_takes_nothing_until_it_is_entered():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    registry.create("abc")

    registry.lease("abc")
    clock.advance(60.0)

    assert registry.reclaim_idle() == 1


def test_a_lease_is_released_when_its_block_raises():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    registry.create("abc")

    with pytest.raises(RuntimeError):
        with registry.lease("abc"):
            raise RuntimeError("stream broke")

    clock.advance(60.0)

    assert registry.reclaim_idle() == 1


def test_a_lease_on_an_unknown_session_holds_nothing_and_does_not_leak_a_hold():
    registry, clock = registry_with_clock(idle_seconds=60.0)

    with registry.lease("nope"):
        pass

    session = registry.create("nope")
    clock.advance(60.0)

    assert registry.reclaim_idle() == 1
    assert session.ended == 1


def test_a_lease_does_not_hold_the_replacement_of_the_session_it_was_taken_on():
    registry, clock = registry_with_clock(idle_seconds=60.0)
    old = registry.create("abc")
    lease = registry.lease("abc")

    with lease:
        registry.end("abc")
        new = registry.create("abc")

    # Leaving the lease neither touched nor counted against the new session.
    clock.advance(60.0)

    assert old.ended == 1
    assert registry.reclaim_idle() == 1
    assert new.ended == 1


def test_eviction_prefers_a_session_that_is_not_leased():
    registry, clock = registry_with_clock(max_sessions=2)
    leased = registry.create("leased")
    clock.advance(1.0)
    spare = registry.create("spare")

    with registry.lease("leased"):
        clock.advance(1.0)
        registry.create("third")

    assert leased.ended == 0
    assert spare.ended == 1
    assert registry.peek("leased") is leased


def test_eviction_past_capacity_still_ends_a_leased_session_when_all_are_leased():
    registry, clock = registry_with_clock(max_sessions=1)
    leased = registry.create("leased")

    with registry.lease("leased"):
        registry.create("newcomer")

        assert leased.ended == 1
        assert len(registry) == 1

    # Leaving the lease of an ended session is harmless.
    assert registry.peek("newcomer") is not None


def test_ending_a_leased_session_by_id_ends_it():
    registry, _ = registry_with_clock()
    session = registry.create("abc")

    with registry.lease("abc"):
        registry.end("abc")

    assert session.ended == 1
    assert len(registry) == 0


# ---- fair-share eviction (T18-11) ----


class LoggedSession(FakeSession):
    """A FakeSession that records, in `log`, the id it was admitted under once it ends."""

    def __init__(self, log):
        super().__init__()
        self.log = log
        self.id = None

    def end(self):
        super().end()
        self.log.append(self.id)


def logged_registry(max_sessions, idle_seconds=1e9):
    clock = FakeClock()
    ended = []
    registry = SessionRegistry(
        factory=lambda: LoggedSession(ended),
        max_sessions=max_sessions,
        monotonic=clock,
        idle_seconds=idle_seconds,
    )

    def create(session_id, owner=""):
        clock.advance(1.0)
        session = registry.create(session_id, owner)
        session.id = session_id
        return session

    return registry, clock, ended, create


def test_a_full_registry_evicts_from_the_owner_holding_the_most():
    registry, _, ended, create = logged_registry(max_sessions=4)
    create("a1", "A")
    create("b1", "B")
    create("b2", "B")
    create("b3", "B")

    # a1 is the oldest, but B holds the most.
    create("c1", "C")

    assert ended == ["b1"]


def test_the_new_session_counts_toward_its_creator_so_a_tie_costs_the_creator():
    registry, _, ended, create = logged_registry(max_sessions=4)
    create("b1", "B")
    create("a1", "A")
    create("b2", "B")
    create("a2", "A")

    # A would hold 3 against B's 2, so A pays even though b1 is older.
    create("a3", "A")

    assert ended == ["a1"]


def test_an_owner_one_short_of_the_top_pays_for_its_own_new_session():
    registry, _, ended, create = logged_registry(max_sessions=3)
    create("b1", "B")
    create("b2", "B")
    create("a1", "A")

    # A would hold 2, tying B: a tie goes against the creator.
    create("a2", "A")

    assert ended == ["a1"]


def test_a_creator_holding_nothing_takes_the_oldest_session_among_the_tied_owners():
    registry, _, ended, create = logged_registry(max_sessions=3)
    create("a1", "A")
    create("b1", "B")
    create("c1", "C")
    registry.get("a1")

    create("d1", "D")

    assert ended == ["b1"]


def test_a_creator_holding_nothing_prefers_an_unleased_session_among_the_tied_owners():
    registry, _, ended, create = logged_registry(max_sessions=2)
    create("a1", "A")
    create("b1", "B")

    with registry.lease("a1"):
        create("c1", "C")

    assert ended == ["b1"]


def test_within_the_chosen_owner_an_unleased_session_goes_before_a_leased_one():
    registry, _, ended, create = logged_registry(max_sessions=3)
    create("a1", "A")
    create("a2", "A")
    create("b1", "B")

    with registry.lease("a1"):
        create("a3", "A")

    assert ended == ["a2"]


def test_a_lease_never_moves_the_victim_to_another_owner():
    registry, _, ended, create = logged_registry(max_sessions=3)
    create("b1", "B")
    create("a1", "A")
    create("a2", "A")

    with registry.lease("a1"), registry.lease("a2"):
        create("a3", "A")

    assert ended == ["a1"]
    assert registry.peek("b1") is not None


def test_a_churner_among_single_session_owners_ends_one_other_session_then_only_its_own():
    registry, _, ended, create = logged_registry(max_sessions=32)
    for i in range(32):
        create(f"t{i}", f"10.0.0.{i}")

    for i in range(100):
        create(f"x{i}", "10.0.1.1")

    assert len(registry) == 32
    assert [session_id for session_id in ended if session_id.startswith("t")] == ["t0"]
    assert sum(registry.peek(f"t{i}") is not None for i in range(32)) == 31


def test_a_one_session_owner_creating_against_a_big_one_levels_off_at_an_even_split():
    registry, _, ended, create = logged_registry(max_sessions=32)
    for i in range(31):
        create(f"b{i}", "B")
    create("x0", "X")

    for i in range(1, 51):
        create(f"x{i}", "X")
        x_count = sum(registry.peek(f"x{j}") is not None for j in range(i + 1))
        b_count = sum(registry.peek(f"b{j}") is not None for j in range(31))
        assert b_count >= x_count

    assert (x_count, b_count) == (16, 16)


def test_no_create_ends_a_session_of_an_owner_holding_no_more_than_the_creator_will():
    rng = SeededRNG(1811)
    registry, _, ended, create = logged_registry(max_sessions=12)
    owners = ["A", "B", "C", "D", "E"]
    owner_of = {}

    for n in range(2000):
        owner = rng.choice(owners)
        live = [session_id for session_id in owner_of if registry.peek(session_id) is not None]
        before = {o: sum(owner_of[s] == o for s in live) for o in owners}
        ended.clear()

        create(f"s{n}", owner)
        owner_of[f"s{n}"] = owner

        for victim in ended:
            victim_owner = owner_of[victim]
            if victim_owner != owner:
                assert before[owner] == 0 or before[victim_owner] > before[owner] + 1, (n, before, owner, victim)


def test_with_one_owner_eviction_matches_the_global_least_recently_touched_rule():
    # The rule before T18-11, as a model: the least-recently-touched unleased
    # session, or the least-recently-touched one when every session is leased.
    registry, clock, ended, create = logged_registry(max_sessions=4)
    touched = {}
    holds = {}
    expected = []
    leases = {}
    rng = SeededRNG(7)

    for n in range(400):
        live = list(touched)
        step = rng.randint(0, 3)

        if step == 0 or not live:
            if len(touched) >= 4:
                unheld = [s for s in touched if not holds.get(s)]
                victim = min(unheld or touched, key=touched.__getitem__)
                expected.append(victim)
                del touched[victim]
                holds.pop(victim, None)
                for lease in leases.pop(victim, []):
                    lease.__exit__(None, None, None)
            create(f"s{n}", "only")
            touched[f"s{n}"] = clock()
        elif step == 1:
            session_id = rng.choice(live)
            clock.advance(1.0)
            registry.get(session_id)
            touched[session_id] = clock()
        elif step == 2:
            session_id = rng.choice(live)
            lease = registry.lease(session_id)
            lease.__enter__()
            leases.setdefault(session_id, []).append(lease)
            holds[session_id] = holds.get(session_id, 0) + 1
        else:
            held = [s for s in live if holds.get(s)]
            if held:
                session_id = rng.choice(held)
                clock.advance(1.0)
                leases[session_id].pop().__exit__(None, None, None)
                holds[session_id] -= 1
                if holds[session_id] == 0:
                    del holds[session_id]
                    touched[session_id] = clock()

    assert ended == expected
    assert len(expected) > 50


def test_a_session_made_outside_a_request_shares_one_owner_with_every_other_such_session():
    registry, _, ended, create = logged_registry(max_sessions=3)
    create("a1", "A")
    registry.create("n1").id = "n1"
    registry.create("n2").id = "n2"

    registry.create("n3").id = "n3"

    assert ended == ["n1"]


def test_a_failed_create_ends_nothing():
    calls = []

    def factory():
        calls.append(1)
        if len(calls) > 2:
            raise RuntimeError("factory failed")
        return FakeSession()

    registry = SessionRegistry(factory=factory, max_sessions=2)
    first = registry.create("a", "A")
    second = registry.create("b", "B")

    with pytest.raises(RuntimeError):
        registry.create("c", "C")

    assert (first.ended, second.ended) == (0, 0)
    assert len(registry) == 2


def test_concurrent_creates_from_many_owners_never_exceed_capacity():
    ids = [f"s{i}" for i in range(8)]
    factory, built = constructing_together(len(ids))
    registry = SessionRegistry(factory=factory, max_sessions=3)

    run_together(registry.create, [(session_id, f"owner{i}") for i, session_id in enumerate(ids)])

    assert len(registry) == 3
    assert sum(session.ended == 0 for session in built) == 3
