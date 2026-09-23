# T2-6 — Scheduler ownership design note

**Status:** **approved** (all four decisions settled below). Phase 2 may proceed.
**Baseline:** `origin/main` at `bbbfa60`, 738 passed, `mypy` clean over 24 source files
(re-verified before this note).

T2-5 merged an unwired `Scheduler`. T2-6's build-plan scope is one frontend file,
but removing browser stepping without wiring a scheduler stops physics entirely.
This note settles *who owns a running worker and when it dies* before any of that
is implemented.

---

## The facts this is built on

Read out of the repo, not assumed:

1. `Session` holds `compressor_engine` and `pump_engine`
   (`app/engine/sessions.py`), so a Session needs **two** schedulers.
2. `app/main.py::load_session` creates a Session for **any** request without a
   valid cookie — including every `main.app.test_client()`. Four test files
   drive the app this way.
3. Exactly **one** test renders an HTML page: `test_pump_page_renders`
   (`tests/test_pump_api.py:113`). Every other app-level test hits `/api/*` only.
4. `/api/start` and `/api/stop` call the **device's** `start()`/`stop()`, never
   `Engine.start()`/`stop()`. The clock is therefore never paused: a running
   scheduler always advances `sim_time`, and the device ramps only while
   `device.running`.
5. `Session.compressor_state()` reads a snapshot **plus two live-device
   queries** — `temperature_at(spread)` and `characteristic(flow)`.
   `pump_state()` reads `characteristic(flow)`. Both take a *solved* argument.
6. `Scheduler.snapshot()` acquires `step_lock` when nothing has been published
   yet, so it **cannot** be called while holding `step_lock` — `threading.Lock`
   is not reentrant.
7. `SessionRegistry.end()` is called only from tests. Nothing in `app/` ends a
   session, and the registry has no expiry and no cap.

Fact 7 is the whole problem. Fact 3 is what makes the answer cheap.

---

## 1. Who creates each Scheduler?

`Session.__init__` creates two — `compressor_scheduler` and `pump_scheduler`,
one per Engine, named to match the engines they own — and **starts neither**.

Construction is inert: a `Scheduler` that has never been `start()`ed holds no
thread. So this adds no cost and no concurrency to any existing test, while
keeping ownership where the engines already live. No registry change is needed
to create them.

## 2. When does it start?

**When the HTML page that displays it is rendered.** `/compressor` starts
`compressor_scheduler`; `/pump` starts `pump_scheduler`. `start()` is
idempotent, so a page reload costs nothing.

Why this trigger:

- A page being served is the only real evidence a human is watching a plant.
  A bare `/api/state` from curl, a crawler or a test client is not.
- It satisfies both acceptance criteria directly: a browser that loads the page
  gets a plant that runs on its own, and closing the tab sends nothing, so
  nothing stops.
- It keeps the suite thread-free *by construction*, with exactly one exception
  (fact 3) rather than "every test client".
- A visitor who only opens the compressor page does not get a pump worker.

**Rejected: start both in `Session.__init__`.** That spawns two threads per test
client across four test files and turns request-level assertions such as
`load == approx(0.05)` into races. It would force a disabling flag or fixture to
exist purely to undo it.

**Known behaviour difference to accept:** a client that polls `/api/state`
without ever rendering the page sees a frozen plant. Today the only `/api/state`
consumer is the page that also renders, so nothing observes this. M16's console
rewrite is where it would be revisited.

## 3. When does it stop?

Three explicit paths, and nothing else:

- A new `Session.end()` stops both schedulers.
- `SessionRegistry.end()` calls `Session.end()` before dropping the session.
- Registry eviction (§4) calls the same `Session.end()`.

No `atexit`, no Flask teardown hook, no reliance on the daemon flag.
`Scheduler.stop()` joins its worker, so `Session.end()` returning means both
threads are genuinely dead — which is what the teardown test asserts.

## 4. What production path ends an abandoned Session?

**Today: nothing.** Settled: bound `SessionRegistry` by **capacity with
least-recently-used eviction**, swept inside `create`/`get_or_create`.

- The registry stamps each session with a monotonic last-touched time, refreshed
  on lookup.
- On create at capacity, it ends the least-recently-touched session first —
  `Session.end()`, which **stops and joins both scheduler workers** before the
  registry drops the session.
- `MAX_SESSIONS = 32` lands in `app/config.py` as a new appended section.

**32 is an operational guardrail, not a simulation constant.** It lives in
config so it can be changed without touching logic, and no physics depends on it.

**Why a cap rather than an idle timeout.** A cap is a *hard* bound on live
workers: never more than `2 * MAX_SESSIONS`, with no tuning and no timer. An
idle timeout only reclaims when a later request arrives to trigger a sweep — so
if every browser leaves, the last sessions run forever, which is precisely the
leak being closed. A timeout also needs a background sweeper thread, which is
one more thread to own.

### Scope boundary: T18-5 owns idle reclamation

**Idle-age expiry is deliberately excluded from T2-6.** `T18-5`
("Session lifecycle and config versioning") already owns
`app/engine/sessions.py` for exactly this, with acceptance criteria
"Idle sessions reclaimed" and "Idle session is reclaimed and memory released".
Adding timeout policy here would steal that task's scope.

T2-6's LRU cap is **only the bounded-resource safety mechanism** that makes
Scheduler ownership safe today. It is not session lifecycle management, and it
does not close out T18-5.

**Required test:** creating session 33 evicts the least-recently-used session,
and both of that session's scheduler workers are proven stopped.

Note the request layer reading a monotonic clock is consistent with the rules:
CLAUDE.md bans wall-clock reads *inside a model*, and `scheduler.py` already
reads `time.monotonic()` for cadence. No model gains a clock here.

## 5. How are schedulers disabled in deterministic tests?

They are **never started**, because only a page render starts one. No flag, no
fixture, no monkeypatching for the four API test files.

The single exception, `test_pump_page_renders`, must end what it started:
`main.sessions.end(session_id)` in a `finally`. That is a two-line change, local
and explicit. The alternative — an autouse `conftest.py` fixture ending every
session — is a much larger footprint, and CLAUDE.md records that `conftest.py`
defines no fixtures today.

Two guard tests are worth adding beyond the required list:

- After a full `/api/state` → `/api/start` → `/api/step` sequence, assert
  `session.compressor_scheduler.running is False`. This is what stops a later
  task quietly reintroducing autostart and making the suite flaky.
- Assert `threading.active_count()` returns to its pre-session value after
  teardown.

## 6. How do manual-step routes behave while a scheduler is active?

Keep `/api/step` and `/api/pump/step` as explicit debug and test controls, and
make them **refuse while the matching scheduler is running** — HTTP 409 with a
short JSON reason. While it is stopped, which is every existing test, they step
exactly as they do now.

- Not retired: eight call sites across three test files depend on them, and a
  hand-stepped engine is the only way those tests stay deterministic.
- Not a silent success no-op — forbidden, and it would be a lie.
- Not "step anyway under `step_lock`": that injects an out-of-band step into a
  cadence that owns time, which is the thing T2-6 exists to stop.

I do not think this is unclear enough to block on, but if you would rather they
take `step_lock` and step regardless, say so now.

## 7. How do state routes avoid live-device read races?

Fact 5 means the live-device reads cannot be eliminated: `temperature_at(spread)`
and `characteristic(flow)` both take a *solved* argument the device does not know
by contract, so neither can move into `get_state()`. They must be synchronized
instead.

One `step_lock` acquisition covers the whole read:

```python
with self.compressor_scheduler.step_lock:
    snapshot = self.compressor_scheduler.snapshot_locked()
    # characteristic(flow), temperature_at(spread), response — all from this
    # one coherent state
```

Fact 6 is the trap: calling `Scheduler.snapshot()` while holding `step_lock`
deadlocks before the first publish. Settled: `scheduler.py` gains a narrow,
explicitly-named accessor **`snapshot_locked()`** rather than a generic
`published` property, so its contract is visible at the call site.

`snapshot_locked()` contract: **the caller already holds `step_lock`, and this
method must never acquire it again.** With nothing published yet it may build
the current Engine snapshot directly, because the caller's lock already covers
that read.

Three rules follow, and they are what Phase 2 enforces:

1. State reads based entirely on published data use `scheduler.snapshot()` and
   take no external lock.
2. State reads that still need live-device queries take `step_lock` **once** and
   use `snapshot_locked()` plus those queries inside it.
3. **No code calls `scheduler.snapshot()` while already holding `step_lock`.**

Ordinary consumers that need no live-device reads keep using
`scheduler.snapshot()` unchanged.

The one-step straddle is **rejected**: it would publish a response mixing values
from two different steps, and a narrow accessor is cleaner than either that or a
reentrant lock.

Everything else in those two methods is snapshot-only and gets no lock.

### Writes

These mutate live equipment and each takes the relevant `step_lock` around the
mutation only: `/api/start`, `/api/stop`, `/api/load`,
`/api/pump/start`, `/api/pump/stop`, `/api/pump/speed`, and the two easily-missed
HTML redirect routes `/start` and `/stop`.

Each then calls a state method that takes the lock itself, so the mutation and
the response read are **two separate acquisitions**. Do not wrap both in one, and
do not make the lock reentrant to allow it.

---

## Explicitly not done here

- No Flask startup or shutdown hooks, and no `atexit`. Ownership hangs off
  Session lifecycle only.
- No daemon-thread reliance for cleanup.
- No second pause flag — a paused clock applies zero time and the scheduler
  keeps ticking.
- No attempt at reloader process duplication. `flask --app app.main run` does not
  reload, so the manual check is single-process; `app.run(debug=True)` under
  `__main__` does duplicate, but with a page-render trigger only the child serves
  requests and so only the child starts workers. If the manual check contradicts
  this, I stop.

## Scope flags carried into Phase 2

- **File-list correction:** `static/pump.js` independently steps physics through
  `/api/pump/step` on the same 1-second timer. The plan names only
  `compressor.js`. Both must change, or one browser-driven engine survives. To be
  called out in the PR.
- `app/engine/sessions.py` is **spine** — take the lock.
- `app/main.py` is the **highest-conflict** file — one branch at a time, released
  immediately after merge.
- `app/config.py` gains `MAX_SESSIONS` in a new appended section.
- `app/engine/scheduler.py` gains `snapshot_locked()` (§7).

## Decisions settled

1. **Abandonment policy** — capacity with LRU eviction, `MAX_SESSIONS = 32` in
   `app/config.py`. **No idle-age expiry**: T18-5 owns that.
2. **Start trigger** — start on page render, and only the scheduler for the
   equipment page actually rendered. Never both because a Session exists.
3. **Manual-step routes** — kept, 409 while the matching scheduler runs. Not a
   successful no-op, and never an extra step taken beside the scheduler.
4. **Scheduler change** — add `snapshot_locked()` with the explicit
   caller-holds-the-lock contract. The one-step straddle is rejected.

None of these change the Engine or Snapshot contracts.
