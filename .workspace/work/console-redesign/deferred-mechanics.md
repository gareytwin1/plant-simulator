# Console redesign: deferred mechanics

Recorded 9 October 2026 during `/align` for the console redesign. The user put
look and feel first; everything below waits until the design tasks are
complete. Each item was verified against `main` at `d1163db`; re-verify before
acting on it.

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

## Already recorded elsewhere

- One schematic for every scenario, and one controller in the plant:
  [project_state.md](../../memory/project_state.md), "Open decisions with no
  owner", items 3 and 4. More loops need `controllers.pv` widened beyond a
  node id first.
- Checkpoint D's remaining gate (PIC-101 rejects an injected disturbance in
  AUTO): project_state.md, "Next by leverage".
