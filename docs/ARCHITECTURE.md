# Architecture

How Plant Simulator is put together — what runs today, what it is being built
toward, and who owns which piece of state.

The two are kept strictly separate in this document. Blurring them has already
misled sessions into assuming the network solver exists. **It does not.**

For current status and task-level detail see
[PROJECT_STATE.md](PROJECT_STATE.md); for the rules that constrain changes see
[../CLAUDE.md](../CLAUDE.md).

---

## 1. Current runtime

This is what actually happens when someone loads the app today.

```text
Browser (vanilla JS, 1 s poll)
  │   GET  /compressor            GET  /pump
  │   GET  /api/state             GET  /api/pump/state
  │   POST /api/start|stop|step   POST /api/pump/start|stop|step
  │   POST /api/valve|load        POST /api/pump/speed
  ▼
Flask routes  ·  app/main.py
  │   @before_request resolves a session cookie
  ▼
SessionRegistry → Session  ·  app/engine/sessions.py
  │   one GasCompressor + one CentrifugalPump per browser session
  ▼
Device  ·  app/equipment/{compressor,pump}.py
  │
  └── device.step(dt)                         ← LEGACY standalone path
        ├── integrate(dt)                     ← slow state: load / speed / valve
        └── _calculate_operating_point()      ← device solves its OWN operating
              point against its OWN upstream_boundary_pressure and
              downstream_boundary_pressure, and writes its own
              flow / suction_pressure / discharge_pressure
  ▼
get_state()  →  JSON  →  browser display
```

**Infrastructure that exists but is not on this request path yet:**

```text
SimulationClock   app/engine/clock.py      sim time, speed, pause
Engine            app/engine/engine.py     integrate() cadence + snapshot publishing
Snapshot (C4)     app/engine/snapshot.py   immutable read contract
Equipment (C1)    app/equipment/base.py    the interface both devices implement
EquipmentRegistry app/equipment/registry.py tag → device
Topology (C2)     app/plant/topology.py    Node / Branch / Stream / Topology
Plant config (C3) config/schema/plant.schema.json + app/plant/validate.py
```

`Engine` works and is fully tested, but nothing in the Flask request path calls
it. When it *is* driven (in tests), it calls `integrate()` only:

```text
Engine.step(dt)
  ├── elapsed = SimulationClock.step(dt)      # 0.0 while paused
  ├── for every device: device.integrate(elapsed)
  └── build_snapshot(...)  →  Snapshot
```

It does **not** compute flow or pressure — there is no plant-wide solve yet, so
a device's flow and pressure do not change when stepped through the Engine. The
snapshot's `solver` section is a trivial converged placeholder, and its `nodes`,
`streams`, `controllers`, `envelope` and `alarms` sections are deliberately
empty.

## 2. Target architecture

Where this is going once the network solver (T4-2, milestone M4) lands. **None
of the solver path below exists today.**

```text
Browser / API client
  │   (C5: one action endpoint — POST /api/action {target, action, value})
  ▼
Session
  ▼
Engine.step(dt)
  │
  ├── 1. SimulationClock             → elapsed simulated time
  │
  ├── 2. integrate slow state        → every device advances load, speed,
  │                                     valve stroke, level, metal temperature
  │                                     (never flow, never pressure)
  │
  ├── 3. plant network solver        → app/engine/network.py (T4-2)
  │        iterates, calling each device's pure characteristic(flow)
  │        as many times as convergence needs
  │
  ├── 4. topology owns the result    → node pressures written to Node,
  │                                     stream flows written to Stream
  │                                     (app/plant/topology.py)
  │
  └── 5. publish Snapshot (C4)       → immutable, the single read contract
            │
            ├── controllers (C-)   PID loops, auto/manual        [M8]
            ├── envelopes          normal bands + excursion time [M9]
            ├── alarms (C6/C7)     symptom-named events          [M10]
            ├── trips/interlocks   protective actions            [M11]
            ├── historian/trends   ring buffer                   [M17]
            ├── scoring            reads snapshot only           [M15]
            └── operator console   process graphic + faceplates  [M16]
```

At that point the device's own `step()` and `_calculate_operating_point()` are
**removed**, along with the interim `upstream_boundary_pressure` /
`downstream_boundary_pressure` attributes. Boundary pressures become properties
of boundary `Node`s in the topology, which is where they belong.

## 3. State ownership

This table is the heart of the architecture. Most contract violations are an
ownership violation.

| State | Owner today | Owner in target | Rule |
|---|---|---|---|
| **Node pressure** | Device (`*_boundary_pressure`, interim) | **Topology** (`Node.pressure`, written only by the solver via `set_pressure`) | A device must never read or write a node pressure. A boundary pressure owned by a device is a solver output in disguise. |
| **Stream flow** | Device (`self.flow`, from its own solve) | **Topology** (`Stream.flow`, written only by the solver via `set_flow`) | Same rule. `Branch.flow` is a read-only property over the stream. |
| **Slow actuator state** (load, speed, valve position, level, metal temp) | **Device** | **Device** — unchanged | Mutated *only* by `integrate(dt)`. `integrate(0)` must be a no-op. |
| **Equipment characteristic curve** | **Device** | **Device** — unchanged | `characteristic(flow)` is pure: reads slow state, returns a number, mutates nothing. The solver may call it many times per timestep. |
| **Port wiring** | `Port` (`node` only) | `Port` — unchanged | Wiring, not process state. `__slots__` makes it structurally impossible to store a pressure or flow on a port. Survives `reset()`. |
| **Simulated time** | **`SimulationClock`** | **`SimulationClock`** — unchanged | Never `time.time()`. Time enters a model only through injected `dt`. |
| **Integration cadence** | **`Engine`** | **`Engine`** — unchanged | Engine consults the clock's speed only, never a device's `simulation_speed`. |
| **Snapshot publication** | **`Engine`** → `Snapshot` | **`Engine`** → `Snapshot` — unchanged | Immutable; the only thing downstream consumers read. |
| **Plant structure** | `Topology` (built by hand in tests) | `Topology` built by the **plant loader** (T3-3) from validated config (C3) | Adding equipment must stop requiring a code change. |

## 4. The C1 equipment contract in one page

Every device implements this and nothing more.

```python
class Equipment:
    tag: str                      # "K-101", "P-101", "FV-1023"
    ports: dict[str, Port]        # {"suction": Port, "discharge": Port}

    def integrate(self, dt):
        """Advance SLOW state only. Never touches flow/pressure.
        integrate(0) is a no-op."""

    def characteristic(self, flow):
        """Pressure change across the device at this flow.
        Positive = rise (machine), negative = drop (valve, pipe).
        Pure — mutates nothing."""

    def get_state(self):
        """Flat, JSON-safe primitives — the device's row in the snapshot."""

    def reset(self):
        """Back to construction state, exactly. Port wiring is left alone."""
```

Construction state is captured automatically by `__init_subclass__`, so `reset()`
works for every device without each one reimplementing it. Subclasses are
auto-registered in `Equipment._registry`, which is how the parametrized contract
tests and the registry round-trip tests discover new devices without being
edited.

**Why the split exists:** the solver needs to ask "what pressure change would you
produce at this flow?" repeatedly within a single timestep while converging. If
that question could mutate the device, every solver iteration would change the
plant and the answer would depend on how hard the solver had to work. So the
mutating half (`integrate`) and the asking half (`characteristic`) are separated,
and only the mutating half gets `dt`.

## 5. Determinism

A hard requirement, not a nice-to-have: scenario replay, repeatable scoring and
the golden regression harness all depend on it.

- No wall-clock source anywhere inside a model.
- Simulated time arrives only through injected `dt`.
- All randomness must come from one seeded source. **That service does not exist
  on `main` yet** — T2-2 (`app/engine/rng.py`) is written on an unmerged local
  branch. Nothing in `app/` currently uses `random`, so the rule is not yet
  violated, but do not add a bare `import random` in the meantime.
- Same config + same seed + same sequence of `step(dt)` calls ⇒ bit-identical
  state, forever.
- `tests/fixtures/golden/*.json` pin current behaviour at 1e-5 relative /
  1e-7 absolute tolerance. A moved trace means behaviour changed — investigate,
  do not regenerate.

## 6. Module map

```text
app/
  main.py                 Flask routes (per-equipment today; C5 replaces this)
  config.py               Timing constants + TAG_PREFIXES. APPEND-ONLY.
  equipment/
    base.py               C1: Equipment + Port                  [SPINE]
    compressor.py         GasCompressor (K-101)
    pump.py               CentrifugalPump (P-101)
    registry.py           EquipmentRegistry: tag → device
  engine/                                                        [SPINE]
    clock.py              SimulationClock
    engine.py             Engine: integrate cadence + snapshot
    snapshot.py           C4: immutable Snapshot
    sessions.py           Session / SessionRegistry
    rng.py                Seeded RNG service
  plant/
    topology.py           C2: Node / Branch / Stream / Topology  [SPINE]
    validate.py           C3 validator
config/
  schema/plant.schema.json  C3 schema
templates/, static/       Per-equipment pages. Frozen; replaced at M16.
tests/
  golden_regression.py    Golden harness (scenarios + replay)
  fixtures/golden/*.json  Pinned numbers
```

## 7. Version 1 target plant

The train V1 is being built toward — small enough to finish, connected enough
that an upset propagates somewhere the operator can see it.

```text
N-01  boundary · feed header, pressure held
  ├─ P-101   centrifugal pump   speed, head curve, min-flow protection
  ├─ E-101   feed heater        duty, utility side, foulable
  ├─ V-101   separator          LEVEL — integrated state, the slow variable
  ├─ K-101   gas compressor     load, head curve, discharge temperature
  ├─ FV-101  discharge valve    characteristic, stroke rate, fail-closed
N-06  boundary · product header, pressure held

control     LIC-101  V-101 level    → letdown valve
            PIC-101  K-101 discharge → FV-101
protection  K-101 trip on discharge PSHH · P-101 trip on suction PSLL
            V-101 LSHH → K-101 trip   (cross-equipment consequence)
```

Of these, **P-101 and K-101 exist today**. E-101, V-101 and FV-101 do not, and
nothing connects them yet.
