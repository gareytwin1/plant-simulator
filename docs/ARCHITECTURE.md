# Architecture

How Plant Simulator is put together — what runs today, what it is being built
toward, and who owns which piece of state.

The two are kept strictly separate in this document. Blurring them has already
misled sessions. As of **T4-4 (Checkpoint B) the plant does solve as a
network**, as of **T5-2 and T5-3 it solves as several coupled domains**, and as
of **T2-6 a background scheduler, not a browser, advances it**. What is still
ahead is everything the solved plant feeds — controllers, envelopes, alarms,
trips, scenarios, scoring and the console. **None of those exist.**

For current status and task-level detail see
[project_state.md](../.workspace/memory/project_state.md); for the rules that constrain changes see
[../AGENTS.md](../AGENTS.md). The two architecture decisions on file are
[ADR 0001](ADR_0001_FLOW_DOMAIN_SEPARATION.md) (flow-domain separation) and
[ADR 0002](ADR_0002_TYPED_PORTS.md) (typed ports and the vessel connection
model).

---

## 1. Current runtime

This is what actually happens when someone loads the app today.

```text
Browser (vanilla JS, 1 s poll — reads only, steps nothing)
  │   GET  /compressor            GET  /pump
  │   GET  /api/state             GET  /api/pump/state
  │   POST /api/start|stop        POST /api/pump/start|stop
  │   POST /api/load               POST /api/pump/speed
  ▼
Flask routes  ·  app/main.py
  │   @before_request resolves a session cookie
  │   the page render starts that page's scheduler, and only that one
  ▼
SessionRegistry → Session  ·  app/engine/sessions.py
  │   one Engine per page over a single-device plant built by the C3 loader,
  │   and one Scheduler per Engine (compressor_scheduler, pump_scheduler)
  ▼
Scheduler  ·  app/engine/scheduler.py        [background thread]
  │   steps its Engine on its own cadence whether or not anything is polling
  ▼
Engine.step(dt)  ·  app/engine/engine.py
  │   ├── clock → elapsed simulated time
  │   ├── integrate(elapsed) on every device in Plant.devices
  │   ├── write boundary conditions from coupling-device slow state
  │   ├── NetworkSolver.solve() over EVERY entry in Plant.topologies
  │   ├── read solved flows back onto the coupling devices
  │   └── build_snapshot(...) with the solved nodes and streams
  ▼
Session.compressor_state() / pump_state()
  │   reads the latest PUBLISHED snapshot — it does not step anything
  ▼
JSON  →  browser display
```

`/api/step` and `/api/pump/step` survive as manual, test-facing controls. They
step exactly as before while the matching scheduler is stopped, and return
HTTP 409 while it is running rather than racing the background worker. A
Session's schedulers run until `Session.end()` stops and joins both — called
directly, by `SessionRegistry.end()`, or by capacity-bounded LRU eviction at
`config.MAX_SESSIONS`. There is deliberately no idle-age expiry yet; that is
T18-5's scope.

**Interim, until the pages are rewired onto a plant that contains a valve:** a
page's machine sits between two fixed battery limits with no line resistance
and no valve in between. Flow is therefore whatever the machine curve gives
against that fixed differential — above `max_flow`, which nothing clamps any
more — and the process spread equals the boundary difference. The retired
standalone solve is where those resistances used to live. T7-1 built the
valve and a plant that uses it (`config/plants/liquid_valve_train.yaml`) and
T7-2 put one on K-101's discharge in `config/plants/olefins_lite.yaml`; the
single-device pages have not been moved onto either, so **the compressor page
carries no valve control at all** — T7-2 removed the inert slider rather than
leave one that moves nothing.

**Still not on the request path:** `EquipmentRegistry` and `SeededRNG` (T2-2).
Nothing in `app/` draws a random number yet.

An `Engine` built with no topology — `Engine(devices)`, the form that predates
T4-4 — still integrates and nothing more; its snapshot reports the trivial
converged placeholder and empty `nodes` and `streams`. The snapshot's
`controllers`, `envelope` and `alarms` sections are empty for **every** engine,
connected or not: nothing fills them yet.

A solve that fails to converge is reported, never raised and never guessed at:
the plant keeps the state it had, `Engine.step()` still advances time and slow
state, and the snapshot's `solver` section carries `converged: false` with the
iteration count and residual that came with it.

## 2. How domains are coupled

A `NetworkSolver` solves exactly one flow domain. A mass balance mixing GPM
with SCFM is invalid and is rejected at load, not in the solver. What joins two
domains is **a coupling device's inventory, never a shared flow variable**.

```text
liquid topology          V-101 (a coupling device, on no branch)          gas topology
  N-101 ── P-101 ──►  N-102 ═══ level (gal, integrated)  ═══►  N-201 ──► K-101 ──► N-202
                              ║ head_at_full * level                ║
                              ║ added to the OUTLET boundary        ║ pressure (psia, integrated)
                              ╚═ net signed flow read back ═════════╝ REPLACES every gas boundary
```

- A vessel's liquid **head is an offset** applied to the `configured_pressure`
  of the boundary node its outlet port attaches to, so an empty vessel leaves
  the boundary at its as-built value.
- A vessel's gas **pressure replaces** the boundary at every SCFM attachment,
  inlet and outlet alike, so the machine feeding it sees the back-pressure.
- Flows come back as the **signed exchange at each attachment node**, counted
  once per node however many nozzles sit on it and never by port name. Several
  branches at one node are summed, and distinct nodes are summed into the
  aggregate they feed.
- The step order is clock → integrate → write boundaries → solve every domain →
  read flows back → snapshot. That is explicit Euler with one step of lag on the
  vessel's flows; **nothing iterates between the integrator and the solver**.

`app/engine/coupling.py` owns that join and is the only place it lives. A
device still never reads a node or a branch, and `Node.set_boundary_pressure`
refuses on an internal node, so the solver's writer and the coupling's writer
partition the graph between them.

### The node is the unit of account (T5-6)

ADR 0002 Amendment 2 settles what an aggregate is a sum *over*. A port is a
declaration about a connection; the hydraulics belong to the node it lands on,
so the four vessel attributes are sums over **nodes**, each counted once:

```text
one node, both directions declared    inlet  += arrivals       outlet += departures
one node, one direction declared      that aggregate += the node's net exchange
several nodes                         independent, and summed
several nozzles on one node           one exchange, counted once
```

Both components are signed sums over branch orientation — a reversed feed
arrives negatively and is not clamped — and an aggregate any failed domain
feeds holds its previous value entirely rather than being written from the
converged part.

Classification is by **declared `phase`**: `liquid` is GPM and `vapor` is SCFM.
Machines *confirm* it — a pump is GPM, a compressor SCFM — and a declaration
they contradict refuses. `ControlValve` is listed as `UNIT_NEUTRAL` and
confirms nothing, which is not the same as an unrecognised device: **a new
device model on a branch must be added to `FLOW_UNITS`** either way, or a
vessel attached beside it refuses at Engine construction. `DOMAIN_UNITS` is
retired, so a domain may be called `vent`, `flare` or `relief_header` without
its name selecting a unit; a legacy untyped port is still read off the
machines at its node, and refused where they settle nothing. A typed
attachment couples with no branch at the node at all, and one node may be
claimed by only one inventory device.

`purpose` and `control` are read nowhere in any of this (Amendment 1 A.3).

## 3. Target architecture

Where this is going. **The solver and coupling path below is live**; the
consumers hanging off the snapshot are not.

```text
Browser / API client
  │   (C5: one action endpoint — POST /api/action {target, action, value})
  ▼
Session → Scheduler                              [live]
  ▼
Engine.step(dt)
  │
  ├── 1. SimulationClock             → elapsed simulated time          [live]
  │
  ├── 2. integrate slow state        → every device advances load, speed,
  │                                     valve stroke, level, metal temperature
  │                                     (never flow, never pressure)     [live]
  │
  ├── 3. controllers                 → read the published snapshot and move a
  │                                     final element before the solve     [M8]
  │
  ├── 4. inventory coupling          → app/engine/coupling.py            [live]
  │
  ├── 5. plant network solver        → app/engine/network.py, one per domain
  │        iterates, calling each device's pure characteristic(flow)
  │        as many times as convergence needs                            [live]
  │
  ├── 6. topology owns the result    → node pressures written to Node,
  │                                     stream flows written to Stream    [live]
  │
  └── 7. publish Snapshot (C4)       → immutable, the single read contract [live]
            │
            ├── controllers (C-)   PID loops, auto/manual        [M8]
            ├── envelopes          normal bands + excursion time [M9]
            ├── alarms (C6/C7)     symptom-named events          [M10]
            ├── trips/interlocks   protective actions            [M11]
            ├── historian/trends   ring buffer                   [M17]
            ├── scoring            reads snapshot only           [M15]
            └── operator console   process graphic + faceplates  [M16]
```

### The snapshot boundary is not a convenience

**Every one of those downstream systems reads the published `Snapshot` and
nothing else.** A controller, alarm evaluator, envelope tracker, scenario
runner, scoring module or console that reaches directly into `Equipment` or
`Topology` — because the object is right there and the snapshot would need one
more field — has coupled the operator layer to the physics implementation, and
that is a contract violation rather than a shortcut.

The boundary exists so the physics can be rewritten without rewriting the game
layer, and so the game layer provably cannot bend the physics. Two consequences
worth stating plainly:

- **A subsystem that needs a number the snapshot does not carry widens C4** as
  its own deliberate task. It does not open a side channel.
- **Writing is narrower still.** A controller may only move a final element —
  `ControlValve.set_position_target()` — never assign a pressure or a flow.
  Nothing outside `app/engine/coupling.py` writes a node pressure, and the
  solver owns every internal one. T13-5 enforces the import direction with a
  test.

## 4. State ownership

This table is the heart of the architecture. Most contract violations are an
ownership violation.

| State | Owner | Rule |
|---|---|---|
| **Node pressure** | **Topology** (`Node.pressure`, written only by the solver via `set_pressure`; boundary values via `set_boundary_pressure`, written only by the coupling) | A device must never read or write a node pressure. A boundary pressure owned by a device is a solver output in disguise. |
| **Stream flow** | **Topology** (`Stream.flow`, written only by the solver via `set_flow`) | Same rule. `Branch.flow` is a read-only property over the stream. |
| **Slow actuator state** (load, speed, valve position, level, vessel pressure, metal temp) | **Device** | Mutated *only* by `integrate(dt)`. `integrate(0)` must be a no-op. |
| **Equipment characteristic curve** | **Device** | `characteristic(flow)` is pure: reads slow state, returns a number, mutates nothing. The solver may call it many times per timestep. |
| **Port wiring** | `Port` | Connection metadata, not process state. `__slots__` makes it structurally impossible to store a pressure or flow on a port. Survives `reset()`. T3-7 adds `phase`, `purpose` and optional `control` — still description, still not process state, and only `phase` and `direction` may reach a balance. |
| **Domain coupling** | **`app/engine/coupling.py`** | The only place a vessel's inventory reaches a boundary, and the only writer of boundary pressures. |
| **Simulated time** | **`SimulationClock`** | Never `time.time()`. Time enters a model only through injected `dt`. |
| **Integration cadence** | **`Engine`**, driven by **`Scheduler`** | Engine consults the clock's speed only. No device has a speed of its own. |
| **Snapshot publication** | **`Engine`** → `Snapshot` | Immutable; the only thing downstream consumers read. |
| **Plant structure** | `Topology` built by the **plant loader** from validated config (C3) | Adding equipment must stop requiring a code change. |

## 5. The C1 equipment contract in one page

Every device implements this and nothing more.

```python
class Equipment:
    tag: str                      # "K-101", "P-101", "FV-101", "V-101"
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

`characteristic(flow)` is **device-wide** — it takes no port or branch argument —
which is why a device may hold at most one hydraulic path, enforced at load. A
device with no path at all is a **coupling device**: it lives in `Plant.devices`,
sits in no `Topology`, and is how a vessel spans two domains.

## 6. Determinism

A hard product requirement, not a nice-to-have: scenario replay, repeatable
scoring and the golden regression harness all depend on it.

- No wall-clock source anywhere inside a model.
- Simulated time arrives only through injected `dt`.
- All randomness must come from a `SeededRNG` (`app/engine/rng.py`).
  `tests/test_random_source_guard.py` fails if any other module in `app/`
  imports `random`. There is deliberately no module-level generator: a
  process-wide stream would be shared by every browser session, and each
  session owns its own plant. Which object owns a plant's RNG is an open
  decision for the first task that needs randomness.
- Same config + same seed + same sequence of `step(dt)` calls ⇒ bit-identical
  state, forever.
- `tests/fixtures/golden/*.json` pin current behaviour at 1e-5 relative /
  1e-7 absolute tolerance. A moved trace means behaviour changed — investigate,
  do not regenerate.

## 7. Module map

```text
app/
  main.py                 Flask routes (per-equipment today; C5 replaces this)
  config.py               Timing constants + TAG_PREFIXES. APPEND-ONLY.
  statetypes.py           JSONValue / StateRow — the get_state() row type
  equipment/
    base.py               C1: Equipment + Port                  [SPINE]
    compressor.py         GasCompressor (K-101)
    pump.py               CentrifugalPump (P-101)
    valve.py              ControlValve (FV-101)
    vessel.py             Vessel (V-101) — coupling device, liquid + gas
    registry.py           EquipmentRegistry: tag → device
  engine/                                                        [SPINE]
    clock.py              SimulationClock
    engine.py             Engine: integrate cadence, coupling, solve, snapshot
    snapshot.py           C4: immutable Snapshot
    sessions.py           Session / SessionRegistry
    coupling.py           Vessel inventory ↔ boundary conditions
    network.py            NetworkSolver (new isolated module, satellite-built)
    scheduler.py          Background stepping (new isolated module)
    rng.py                SeededRNG (required seed; no global stream)
  plant/
    topology.py           C2: Node / Branch / Stream / Topology  [SPINE]
    validate.py           C3 validator
    loader.py             load_plant(): C3 config → Plant, rejects unsolvable graphs
config/
  schema/plant.schema.json  C3 schema
  plants/*.yaml             Reference fixtures
templates/, static/       Per-equipment pages. Frozen; replaced at M16.
tests/
  golden_regression.py    Golden harness (scenarios + replay)
  fixtures/golden/*.json  Pinned numbers
```

## 8. Version 1 target plant

The train V1 is being built toward — small enough to finish, connected enough
that an upset propagates somewhere the operator can see it.

```text
N-01  boundary · feed header, pressure held
  ├─ P-101   centrifugal pump   speed, head curve, min-flow protection
  ├─ E-101   feed heater        duty, utility side, foulable
  ├─ V-101   separator          LEVEL and PRESSURE — the slow variables
  ├─ K-101   gas compressor     load, head curve, discharge temperature
  ├─ FV-101  discharge valve    characteristic, stroke rate, fail-closed
  ├─ PV-101  vessel pressure control valve   vapour outlet
  ├─ LV-101  vessel level control valve      liquid draw
N-06  boundary · product header, pressure held

control     LIC-101  V-101 level    → LV-101
            PIC-101  V-101 pressure → PV-101
protection  K-101 trip on discharge PSHH · P-101 trip on suction PSLL
            V-101 LSHH → K-101 trip   (cross-equipment consequence)
```

Read that diagram carefully, because three different things are being described
and they have repeatedly been conflated:

| | Status |
|---|---|
| **Models that exist** | `CentrifugalPump`, `GasCompressor`, `ControlValve` and `Vessel` are all implemented and registered in the loader's `DEVICE_TYPES`. |
| **Wired into the browser pages** | Only K-101 and P-101, each alone on a single-device plant between two fixed boundaries. |
| **Present in a reference config** | `liquid_transfer.yaml` and `gas_compression.yaml` (single-domain, T3-4), `liquid_valve_train.yaml` (T7-1), and `olefins_lite.yaml` (T5-5) — the two-domain train coupled through V-101 inventory. It is not yet the full seven-device V1 train: `E-101` is absent, and PV-101/LV-101 are manual. |
| **Not built at all** | `E-101`, the heat exchanger. LIC-101 and PIC-101 as *controllers* — the PID block, modes, loop config and loop execution are all M8 and none of it exists. |

### What ADR 0002 settles about the separator

The connected train was assembled by **T5-5**, to ADR 0002's re-specification:

- **The vessel is wired with the shared-node idiom, not bespoke plumbing.** One
  node per phase; every device exchanging that phase takes a branch on that
  node; the vessel takes typed ports on it. Relief later is simply a third
  branch on the vapour node.
- **V-101 takes three typed ports**: a liquid inlet and a liquid outlet on its
  one liquid node, and one vapour outlet on its one vapour node. K-101 and
  PV-101 are two branches on that vapour node, not two vessel nozzles.
- **T5-7 came first**, and both are now on `main`. ADR 0002 Amendment 3 lets an
  opted-in device (only `Vessel`, through a class-level
  `accepts_configured_ports` marker) take its port set, with a `direction` on
  every entry, from configuration instead of its class — implemented in
  `app/plant/loader.py` and `app/equipment/vessel.py`. Without it the `Vessel`
  declared only `inlet` and `outlet` and the loader rejected any other port
  name, so the separator could not be wired at all.
- **V-101 gains a liquid draw**, which is what turns a vessel that fills
  monotonically into one with a genuine self-regulating steady state — the head
  rises, the drain flow rises, the feed flow falls. Conservation can then be
  asserted at steady state instead of before a bounded test horizon expires.
- **PV-101 and LV-101 ship in T5-5 as MANUAL valves at fixed position.** T5-5
  proves that moving them moves vessel pressure and level in the right
  direction. It contains **no controller**. M8 owns the automatic loops, and
  requiring T5-5 to ship working pressure control would invert the T8-3
  dependency and deadlock M5 against M8.
- **Component inventory and phase equilibrium stay out of V1.** No composition,
  no flash, no K-values, no component balances. The vessel keeps scalar liquid
  inventory (level, gallons) and scalar vapour inventory (pressure, psia). The
  port model is built to carry more metadata later, so adding composition would
  extend a typed connection rather than rediscover what a connection is.

## 9. Deferred: operator console screens and subunit views

**Not built, not scheduled, and not a task.** Recorded so the console milestone
(M16) does not have to rediscover it. Nothing described here exists today.

The finished simulator may present one processing unit through several
operator-facing screens or subunit views. Illustrative examples only:

- unit overview
- feed / pumping
- separation / heating
- compression / product
- alarms / controls / trends

The number and split of screens is deliberately undecided.

**The rule that matters: UI screen boundaries must never become physics
boundaries.** There is one connected plant model, one simulation state, one set
of topologies and one `Snapshot`. A screen is only a view into that state.

The same equipment may appear on several screens, e.g. K-101 on the unit
overview, again on the compression screen, and again in a detailed faceplate.
Each is a read of the same snapshot row.

Process-area or display metadata may eventually be added to the plant
configuration, but it must stay separate from equipment physics and topology: a
device's type, ports, design values and wiring must not depend on which screen
shows it.
