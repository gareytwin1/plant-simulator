# Console redesign: deferred mechanics

Recorded 9 October 2026 during `/align` for the console redesign. The user put
look and feel first; everything below waits until the design tasks are
complete. Each item was verified against `main` at `d1163db`; re-verify before
acting on it.

**Now build-plan tasks (9 October 2026):** milestone M20 in
[BUILD_PLAN.html](../../../docs/BUILD_PLAN.html). Item 1 is T20-13, item 2
T20-14, item 3 T20-1, item 4 T20-3 and T20-13, item 5 T20-15 and T20-16,
item 6 T20-7, item 7 T20-2, T20-4, T20-5, T20-17 and T20-18, and item 8
T20-6, T20-7 and T20-10. The layout port is T20-8 to T20-12. The task
entries supersede the notes below where they differ.

## Gaps the redesign exposes

1. **The console cannot complete most scenarios.** C5 (`app/api/action.py`
   `ACTIONS`) accepts `start`, `stop` and `set_speed_target` on a
   `CentrifugalPump`, `start`, `stop` and `set_load_target` on a
   `GasCompressor`, and `set_position_target` on a `ControlValve`. The console
   sends none of them: its only process controls are the loop faceplates
   (`static/js/faceplate.js`). Yet `pump_trip` and `stuck_drain_valve` tell the
   trainee to restart P-101 with a speed target, and `compressor_trip` to start
   K-101 with a load target. Recommendation: equipment faceplates post these
   device actions through C5 for real. No contract change; it is wiring and
   presentation. A device a loop drives refuses device actions by design, so
   its faceplate routes to the loop.
2. **No pause or resume over HTTP.** `app/api/scenario.py` has load, start,
   abort, unload and result; the clock's pause is visible only in
   `/api/health`. The ribbon's simulation Start/Stop therefore maps to
   Start run and the two-click Abort run. A true pause is a new C5 route and
   its own task.
3. **Units per trend point.** `GET /api/trend/points` carries descriptors and
   limits but no units; only the SVG's `data-unit` attributes know units.
   Trend windows must show units, so this is a backend task (Opus).
4. **Tripped-equipment state is not in the snapshot (C4).** A tripped machine
   shows as STOP; the purple TRIP token exists in `tokens.css` but nothing can
   drive it. A C4 change, its own task.
5. **Recent scenarios and achievements have no data source.** Nothing stores a
   completed run beyond the live session's `GET /api/scenario/result`. The
   prototype shows an honest empty state; history needs a persistence decision.
6. **Pipe flow direction.** `static/graphics/plant.svg` lines 16-19 show
   direction only through moving dash patterns. The redesign replaces them with
   a static direction mark; the graphic's `data-flow-dir` binding already
   supplies the direction.

7. **K-101 runs backwards at the normal operating point** (-10 SCFM, gas from
   its discharge back into V-101). This is deliberate: the
   `olefins_lite.yaml` header derives it as ADR 0002 section 7.3's "K-101
   reversing", because V-101 has no vapour inlet, so K-101 must backflow to
   balance PV-101's vent. The prototype's static flow arrows make it visible,
   and a trainee would read normal operation as abnormal.

   **User's proposed model (9 October 2026):** a two-phase feed into a drum or
   separator, with three basic loops on one vessel - flow control on the feed,
   level control on the bottoms drain, and pressure control on the overhead.
   This gives the vessel a vapour source, so the overhead flows forward, and
   replaces the single-loop plant (open decision 4 below) with the three loops
   an operator expects.

   **Decided by the user:** the simple schematic is the bare drum and its three
   valves - no pump and no compressor. Keep the pump, the compressor, their
   code, `olefins_lite` and the scenarios built on them untouched; they are
   for future scenarios. So the separator is a new plant file beside
   `olefins_lite`, not a change to it.

   Constraints to settle first (Opus):
   - ADR 0002 reserves `phase: mixed` and rejects it at load, because splitting
     a two-phase stream is a flash calculation and D6 rules flash out. The
     nearest model inside D6 is the feed entering as one liquid and one vapour
     inlet port with a configured, fixed split, which needs an ADR decision.
     Two separate feeds (liquid and gas) need none.
   - `controllers.pv` resolves only a node id, so the level and flow loops
     need it widened to a device or stream point first (Known technical debt).
   - A second plant means scenarios run on more than one plant, which is open
     decision 3: the console graphic must follow the loaded plant instead of
     the hard-wired `plant.svg`.

8. **Console display decisions from the prototype review (9 October 2026).**
   The user accepted the prototype, now frozen in `prototype/console-redesign/`; these differ
   from production and need porting:
   - Two limits a side, named HI/HIHI and LO/LOLO by position. Production
     names a band by severity step (`isa_band`, `static/js/graphic.js`,
     `trends.js`), so a high-high that trips reads HIHIHI.
   - Alarm palette yellow, then orange, then red: the first limit (low
     priority) is yellow, not blue. Proposed tokens are in the prototype's
     `prototype.css` `:root`.
   - AUTO is the normal mode for every loop unless a scenario says otherwise.
     Scenarios are therefore faults a loop in AUTO cannot correct (restriction,
     lost signal), not valve moves. The prototype's `record_sample.py` runs
     FIC-301 and LIC-301 beside the engine until `controllers.pv` is widened
     (item 7).
   - The controller faceplate has MAN/AUTO, setpoint and output only; no
     tuning.
   - Schematic: diaphragm actuators with short stems, a wave liquid surface
     with no animation, thin lines (pipes 2, outlines 1-1.5) and smaller
     readings (12px), leaving room for larger plants.

## Already recorded elsewhere

- One schematic for every scenario, and one controller in the plant:
  [project_state.md](../../memory/project_state.md), "Open decisions with no
  owner", items 3 and 4. More loops need `controllers.pv` widened beyond a
  node id first.
- Checkpoint D's remaining gate (PIC-101 rejects an injected disturbance in
  AUTO): project_state.md, "Next by leverage".
