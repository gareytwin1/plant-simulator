# Spec: T15-5 - each scenario declares the tags relevant to its fault, and scoring counts actions on any other tag as unnecessary

> ALIGN artifact. WHAT/WHY end-state - never HOW. Durable contract; the
> `decompose` step decomposes this into milestones/issues without asking the user
> anything. Every section must be verifiable. No TODO/??? placeholders.

**Status:** implemented and merged (T15-5, PR #188, merge `91e6703`). This
spec records the pre-implementation contract; "Why" and "Environment"
describe `main` as it was before the merge.

## Objective

Every scenario declares the set of tags whose operation bears on its fault, and
a completed run's `unnecessary_actions` and `time_to_recognise_s` are computed
against that set instead of treating every action as relevant.

## Why

Before T15-5, `app/scenarios/runner.py::_every_action_relevant` returned `True`
for every action, so `unnecessary_actions` was always 0 and `time_to_recognise_s` ran to
the first action of any kind. A trainee who works every control in the plant
scores the same on these two criteria (weights 10 and 5 of 101 + 100 in
`config/scoring.yaml`) as one who makes only the right moves, and the debrief's
"Actions that were not needed" line (`static/js/debrief.js:53`) can never fire.
That undercuts the operator-training goal: the score should reward diagnosis,
not activity. The trainee and the instructor reading the debrief feel it.

## Definitions

- **Relevant tag set** of a scenario: the tags it declares, exactly; nothing is
  derived or inferred from plant configuration, device class or port names.
- **Relevant action**: an `ActionEvent` whose `tag` is in the running
  scenario's relevant tag set. Granularity is tag only: every action on a
  relevant tag is relevant, whatever the action or value.
- **Unnecessary action**: any logged action that is not relevant.
- **Guarded variable**: a `<tag>.<field>` named in any of the scenario's
  objective `success` or `failure` conditions.

## Authoring rule (what goes in a scenario's list)

A scenario's relevant tag set is the union of:

1. **Fault path** - the device the fault acts on (the malfunction's
   `target_tag`, or the device an initial-condition override trips or sticks)
   and the loop configured to drive it, if any.
2. **Recovery** - every tag the documented correct response (the scenario's
   `description` diagnosis path, scripted in `tests/test_scenario_pack.py`)
   operates, and the loop driving each, if any.
3. **Protective** - every final control element that directly changes a
   guarded variable (a machine or valve feeding or drawing the vessel or node
   that variable belongs to, or the valve in the line whose flow it is), and
   the loop driving each, if any.
4. **Interlocks** - every interlock that latches during the scenario's
   no-response run (the run `test_each_scenario_fails_with_no_response`
   executes), so a reset of a trip the fault itself causes is never penalised.

The resulting lists for the ten shipped scenarios (items 1-3; item 4 is added
by evidence from the no-response run, see acceptance criterion 4):

| Scenario | Plant | Relevant tags (items 1-3) |
|---|---|---|
| pump_trip | olefins_lite | P-101, LV-101 |
| compressor_trip | olefins_lite | K-101, FV-201, PV-101, PIC-101 |
| loss_of_feed | olefins_lite | P-101, LV-101 |
| blocked_drain | olefins_lite | LV-101, P-101 |
| restricted_discharge | olefins_lite | FV-201, K-101, PV-101, PIC-101 |
| stuck_drain_valve | olefins_lite | LV-101, P-101 |
| separator_drain_restriction | separator | LV-301, LIC-301, FV-301, FIC-301, XV-301 |
| separator_vent_restriction | separator | PV-301, PIC-301, FV-301, FIC-301, XV-301 |
| separator_feed_signal_loss | separator | FV-301, FIC-301, XV-301, LV-301, LIC-301 |
| separator_drain_signal_loss | separator | LV-301, LIC-301, FV-301, FIC-301, XV-301 |

Derivation notes: V-101 level is fed by P-101 and drawn by LV-101; V-101
pressure is drawn by K-101 and PV-101 (PV-101 is driven by PIC-101); N-204 and
B-FV-201 flow sit on the K-101 / FV-201 series branch. V-301 level is fed
through XV-301 and FV-301 (FIC-301) and drawn by LV-301 (LIC-301); V-301
pressure is drawn by PV-301 (PIC-301) and fed by the gas that arrives with the
liquid feed (XV-301, FV-301, FIC-301).

## Acceptance criteria

1. **Schema.** `config/schema/scenario.schema.json` requires the relevant-tag
   field on every scenario: a non-empty array of unique tag strings.
   `tests/test_scenario_schema.py` (or its successor) has a case proving that a
   document without the field, with an empty array, or with a repeated tag is
   rejected.
2. **Load-time validation.** Loading a scenario through `ScenarioRunner.load` /
   `load_config` raises the same error type the runner already raises for an
   invalid scenario, naming the offending tag, when the list:
   - names a tag that is not an equipment tag, loop tag or interlock tag on the
     scenario's plant; or
   - names a device that a loop on that plant drives without also naming that
     loop.
   One test per case. **Atomicity:** on any of these errors no run is created,
   the runner's phase and any previously loaded run are unchanged, and nothing
   is journalled - verified by asserting the runner's state before and after
   the failed load is identical.
3. **All ten scenarios declare lists** matching the table above plus
   criterion 4's interlocks, and every one loads.
4. **Interlock evidence.** A test runs each scenario with no response (as
   `test_each_scenario_fails_with_no_response` does), collects the interlock
   tags that latched (`PlantRuntime.trip_records()`), and asserts each is in
   that scenario's declared list. The declared lists include exactly those
   interlocks and no others. Outcome: no interlock latches in any of the ten
   no-response runs (each fails on an objective first), so no shipped list
   names an interlock.
5. **Correct play is clean.** For every scenario in
   `tests/test_scenario_pack.py::CORRECT_RESPONSES`, the scripted run still
   succeeds and its `RunMetrics.unnecessary_actions == 0`.
6. **Wrong play is counted.** For every scenario, a run that performs its
   scripted correct response plus one action on a tag on the same plant that
   is not in its list (an action the endpoint accepts) has
   `unnecessary_actions == 1`, and the score's `unnecessary_actions` penalty is
   `pytest.approx(1 / 5)` under the shipped `config/scoring.yaml`.
7. **Recognition uses the same predicate.** A test on at least one scenario
   whose fault raises an alarm shows: an irrelevant action at or after the
   first alarm, followed later by a relevant one, gives `time_to_recognise_s`
   equal to the relevant action's time minus the first alarm, not the
   irrelevant one's. A run whose only post-alarm actions are irrelevant has
   `time_to_recognise_s is None`.
8. **The placeholder is gone.** `_every_action_relevant` no longer exists in
   `app/`, and no other code path treats every action as relevant in a scored
   run. The runner's module docstring and `app/scoring/metrics.py`'s
   docstring describe where relevance now comes from.
9. **Config version.** `config/VERSION` is `5.0.0` (MAJOR bump: the same
   actions can now score differently), and `tests/test_config_version_guard.py`
   passes with its fixture updated by the project's own procedure.
10. **Nothing else moves.** `python -m pytest -q` and `python -m mypy` are
    green on the branch; no file under `tests/fixtures/golden/` changes.
11. **Build plan.** T15-5 exists in `docs/BUILD_PLAN.html` under M15 with this
    scope, and a follow-up task for naming unnecessary actions in the debrief
    (see Out of scope) is added in the same docs commit, status Not Started.

## In scope

- The scenario schema field, its load-time validation, and the relevance
  predicate the runner passes to `PlantRuntime.metrics`.
- Relevant-tag lists for all ten files in `config/scenarios/`.
- Tests for criteria 1-7.
- Docstring updates in `app/scenarios/runner.py` and `app/scoring/metrics.py`.
- `config/VERSION` bump to 5.0.0.
- The T15-5 and follow-up task entries in `docs/BUILD_PLAN.html`.

## Out of scope

- Showing which actions were unnecessary in the run result or the debrief
  (follow-up task, recorded in the build plan by criterion 11).
- Action-level granularity (e.g. "P-101 start relevant, P-101 stop not").
- Effect-based relevance ("moved a variable toward target") and behaviour
  patterns (reversals, repeats, flip-flopping).
- Any change to weights, scales or penalties in `config/scoring.yaml`.
- Deriving relevance from plant configuration (device-implies-loop or the
  reverse); the loop check in criterion 2 only refuses, it never adds.
- Acknowledgements and clock controls (pause, speed): they are not in the
  `ActionLog` and stay out of scoring.
- Leaderboard separation by version (T19-1).

## Constraints

- **C8 change, deliberate.** The scenario schema is a frozen contract; this
  task is the change to it. No other contract changes: `compute_metrics`
  keeps its signature and `app/scoring` never imports `app/scenarios`.
- **Described, never inferred.** Relevance comes only from the scenario's own
  list. No `isinstance` check, port name or device class decides it
  (`tests/test_port_name_guard.py` stays green).
- **Determinism.** The predicate is a pure function of the action and the
  loaded scenario; no clock, no `random`.
- **Golden traces must not move** (`.claude/rules/testing.md`). Relevance is
  scoring-only; if a golden moves, that is a bug.
- **Recordings made before this change are not expected to replay**: the
  scenario document gains a field and the result digest changes. Accepted as
  part of the MAJOR bump; nothing in the app persists recordings.
- **Spine and ownership** per `DEVELOPMENT.md#file-ownership`: if
  `app/scenarios/runner.py` or the schema is spine-locked, the task takes the
  lock.
- Type hints on all new and modified production code; no em dashes in docs.

## Environment / prior art

- `app/scoring/metrics.py::compute_metrics(actions, alarms, trackers, is_relevant, ...)`
  already takes the predicate and feeds both `unnecessary_actions` and
  `_time_to_recognise`.
- `app/training/runtime.py::PlantRuntime.metrics(is_relevant, outcome)` passes
  it through; `ScenarioRunner` was the single call site, passing
  `_every_action_relevant`.
- `app/scoring/actionlog.py::ActionEvent.tag` is the field relevance reads.
  Only accepted actions are logged (`app/api/action.py:240`,
  `PlantRuntime.act` for interlock resets), so a refused action is never
  counted either way.
- Loop-driven devices refuse device actions (`app/api/action.py` module
  docstring); loops are bound in each plant's `controllers` (olefins_lite:
  PIC-101 -> PV-101; separator: FIC-301 -> FV-301, LIC-301 -> LV-301,
  PIC-301 -> PV-301). Interlocks: olefins_lite LSHH-101, LSLL-101, PSHH-101;
  separator LSHH-301, LSLL-301, PSHH-301.
- `tests/test_scenario_pack.py` holds `CORRECT_RESPONSES` and the `run()`
  helper that criteria 4-6 build on.
- Prior art for a scoring-shape change: T15-3 / the outcome-criterion task
  (BUILD_PLAN.html entry with "Shape decided by Opus on 2026-10-14") bumped
  config MAJOR for the same reason.
- `app/scoring/store.py::personal_best` compares only results with the same
  config major, so criterion 9 keeps old and new personal bests apart.

## Existing surface (must not break)

- Every scenario still loads, arms without moving the plant, succeeds under
  its scripted response, fails with no response, and the separator scenarios
  replay bit-identically (`tests/test_scenario_pack.py`).
- `tests/test_scenario_replay.py`, `tests/test_scenario_runner.py`,
  `tests/test_scenario_api.py`, `tests/test_history_api.py`,
  `tests/test_scoring_*.py` stay green (fixtures that build scenario documents
  inline gain the field).
- `RunMetrics.as_dict`, `Score.as_dict` and `ScoreStore` row shapes are
  unchanged.
- The debrief keeps working unchanged; its existing "Actions that were not
  needed" line now fires when the penalty is non-zero.

## Open questions

- None. Field name and error wording are implementation choices for the plan,
  constrained by the criteria above.
