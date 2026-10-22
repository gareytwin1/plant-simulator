# Spec: T18-11 - creating a session cannot evict another address's sessions beyond its fair share

> ALIGN artifact. WHAT/WHY end-state - never HOW. Durable contract; the
> `decompose` step decomposes this into milestones/issues without asking the user
> anything. Every section must be verifiable. No TODO/??? placeholders.

**Status:** aligned 21 October 2026, not started. Build-plan task T18-11 (M18),
Opus.

## Definitions

- **Owner** of a session: the `client_key` (`app/api/validate.py`) of the
  request that created it. Fixed for the session's life; a later request from
  another address using the same cookie does not change it. A session created
  outside a request (a test, a script) belongs to one shared owner that stands
  for "no address".
- **Count(A)**: how many live sessions owner A holds in the registry.
- **Leased**: a session with at least one open `SessionRegistry.lease` hold
  (an open console stream, T16-8).
- **Full pool**: the registry holds `MAX_SESSIONS` live sessions after the
  idle sweep that every create already runs.

## Objective

When an address creates a session in a full pool, the session ended to make
room belongs to the address that would hold the most sessions once the new one
is counted, with ties going against the creating address, so an address that
keeps minting sessions ends only its own once it holds its fair share.

## Why

Today `SessionRegistry.create` ends the least-recently-touched session in the
whole registry (`_evict_least_recently_touched`, `app/engine/sessions.py`). A
cookieless client draws only from its address bucket (20 requests a second)
and each request mints a real session, so one address can cycle all 32 slots
in a couple of seconds and end every other trainee's plant that is not being
polled that second: background tabs (polled about once a minute), trainees
between pages, anyone reading a debrief with the console stream closed. The
victim loses their run with no warning. T18-10's per-address ceiling bounds
request rate, not session minting, so the gap survived it (roborev 1153,
technical debt in `.workspace/memory/project_state.md`). Trainees sharing a
server with a misbehaving or malicious client feel it.

## Acceptance criteria

1. **Reproduced first, end to end.** Before any fix, an HTTP-level test (Flask
   test client against `app.main`, distinct `REMOTE_ADDR` per client)
   demonstrates the bug: trainee T at address 10.0.0.1 holds a session by
   cookie and goes idle; a client at 10.0.0.2 sends `MAX_SESSIONS` cookieless
   requests; T's next request is served by a new session (its
   `plant_session_id` cookie changes). The test fails on `main` for the reason
   stated and passes after the fix. A unit-level reproduction alone does not
   satisfy this criterion.
2. **Victim rule.** In a full pool, a create by address X ends exactly one
   session, chosen as follows, and the choice is covered by unit tests on
   `SessionRegistry` with an injected clock:
   - Weight(A) is Count(A) for every owner A with a live session, plus 1 for X.
   - The victim owner is the owner with the highest weight among owners that
     hold a live session. On a tie, X is the victim owner if X is among the
     tied owners; otherwise the victim is the least-recently-touched session
     among the tied owners' sessions, chosen by the same lease rule below. A
     test covers X holding sessions yet outside the tie (X weight 2, Y and Z
     weight 3): Y or Z pays, never X.
   - Within the victim owner, the victim is its least-recently-touched
     unleased session, else its least-recently-touched leased session. A lease
     never moves the victim to another owner.
3. **Fairness invariant.** A create by X ends another owner Y's session only
   when Y holds more sessions than X will hold after the create, or X holds no
   session before it. A property-style test drives random sequences of creates
   from a handful of addresses (fixed seed, `SeededRNG` or a literal list, never
   `random`) and asserts the invariant on every eviction.
4. **Churner scenarios**, each a test:
   - 32 addresses hold one session each; a 33rd address creates 100 sessions.
     Exactly one other address's session is ended; every other eviction is the
     churner's own.
   - Address B holds 31 sessions, address X holds 1; X creates 50 more. B is
     never reduced below X's count after the create, and the run ends with both
     at 16.
5. **Lone-address parity.** When every session has one owner, the sequence of
   evicted session ids for any sequence of creates, touches, leases and
   releases is identical to today's global-LRU rule (prefer the
   least-recently-touched unleased session; a leased one only when every
   session is leased). A test runs one scripted sequence through both rules
   and compares the evicted ids.
6. **Owner is fixed.** A test shows that a session created from address A and
   later used (cookie replayed) from address B still counts toward A.
7. **Nothing else changes.** `MAX_SESSIONS` stays 32, `SESSION_IDLE_SECONDS`
   stays 1800, the rate limiter and address ceiling are untouched, an evicted
   trainee still silently gets a fresh plant on the next request, `peek` still
   neither touches nor sweeps, and `create` for an id that already exists still
   returns the existing session without evicting. Existing tests in
   `tests/test_sessions.py`, `tests/test_session_isolation.py`,
   `tests/test_session_load.py` and the rate-limit tests pass unmodified, except
   where a test calls `create` in a way the new owner parameter requires a
   mechanical update; any such edit is listed in the PR. `owner` defaults to
   the shared owner, so `tests/test_session_load.py` needs no edit.
8. **Atomicity.** A create never ends a session and then fails to admit the
   new one: if choosing the victim or building the session raises, no session
   has been ended and the registry's contents are unchanged. Victim choice and
   eviction happen under the registry lock in the same critical section as the
   insert, so two concurrent creates never both evict for one free slot and the
   pool never exceeds `MAX_SESSIONS`.
9. **Gates.** `python -m pytest -q` and `python -m mypy` green; no golden trace
   moves; no change under `config/` (so no `config/VERSION` bump).

## In scope

- Recording each session's owner at creation and choosing eviction victims by
  the rule above, in `app/engine/sessions.py`.
- Passing the creating request's `client_key` into session creation from
  `app/main.py`'s `load_session`.
- Updating the `SessionRegistry` docstring, the `app/config.py` session
  comment (append-only file: edit the comment in place only if the text
  becomes false; otherwise leave it), and the eviction descriptions in
  `README.md`, `docs/ARCHITECTURE.md` and the `tests/test_scheduler_ownership.py`
  module docstring, which name the old LRU rule.
- Removing the technical-debt entry from `project_state.md` at close-out and
  adding T18-11 to `docs/BUILD_PLAN.html` and the live artifact.

## Out of scope

- A creation-rate bucket or any other change to rate limiting (the user's
  explicit exclusion).
- Any change to `MAX_SESSIONS`, `SESSION_IDLE_SECONDS`, or idle-reclaim
  behaviour.
- A user-facing notice of eviction; the silent fresh plant stays.
- Sharing session accounting across processes; the registry stays
  per-process (gunicorn runs one worker).
- A run id in the snapshot, ReliefValve, or any other carried debt.

## Constraints

- `app/engine/sessions.py` is spine: the task holds the spine lock from start
  to merge. `app/main.py` has its own lock, also held. Both are free as of 21
  October 2026.
- `sessions.py` must not import Flask or `app.api`: the owner arrives as a
  plain string from the caller, keeping the registry request-agnostic.
- Determinism rules apply to tests: no `random`, no wall clock; the registry's
  injected `monotonic` drives every timing assertion.
- Lock order is unchanged: the registry lock is still taken before any
  Scheduler lock.
- Type hints required in changed production code; no em dashes in prose.

## Environment / prior art

- `app/engine/sessions.py`: `SessionRegistry` with `_sessions`, `_touched`,
  `_holds`, `create`, `get`, `peek`, `lease`, `_evict_least_recently_touched`.
  Its class docstring documents admission atomicity and the lease backstop the
  new rule must keep.
- `app/main.py`: `load_session` mints a session for a request with no live
  cookie; `_live_session_id` and `validate.install` compute the client address
  through `client_key` with `config.API_TRUSTED_PROXIES`.
- `app/api/validate.py::client_key`: the address the rate limit keys on
  (T18-8, T18-10); the owner must be the same key so proxies are handled once.
- `docs/T2-6_SCHEDULER_OWNERSHIP.md`: the registry's original design.
- T18-5 (idle reclaim) and T16-8 (leases) are the precedents for registry
  changes; T18-10 recorded this gap.

## Existing surface (must not break)

- Session isolation (`tests/test_session_isolation.py`): one browser never
  sees another's plant.
- Idle reclaim and lease semantics (`tests/test_sessions.py`).
- Health endpoints: `create_health_blueprint` reads `len(sessions)` and
  `peek`; neither may create, touch or sweep.
- Operator cookie handling in `load_session` (`g.operator_id`) and run
  recording through `ScoreStore`.
- The rate limiter's session-or-address keying (T18-10).

## Open questions

- None. Decisions taken with the user on 21 October 2026: guarantee is
  "cannot evict others beyond fair share"; fair share with no new tunable;
  ownership beats leases; owner fixed at creation; creation-rate bucket,
  notices, pool values and multi-process accounting all out of scope; tracked
  as T18-11 in M18.
