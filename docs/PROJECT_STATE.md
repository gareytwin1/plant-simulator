# Project State

Concise handoff for a new session. **This file goes stale by design** — it
records what is true right now, not the stable rules. Those are in
[CLAUDE.md](../CLAUDE.md).

Refresh this file whenever a task merges to `main`.

---

**Last refreshed:** 20 September 2026 (**T5-6 opened for review** on `feature/coupling-aggregation` — coupling aggregates by node)
**Current `main`:** `1848d53` — *Merge pull request #43 from gareytwin1/docs/t3-7-complete*, documentation only on top of the T3-7 code merge `8733b1a`
**Last code merge:** `8733b1a` — T3-7, typed ports (PR #42): `Port` gains `phase`, `purpose` and optional `control`, typed C3 `ports` entries load and round-trip, and a guard test keeps port names from driving behaviour. Production change is `app/equipment/base.py` and `app/plant/loader.py` only. Previous: `4e48949` T2-6, the scheduler owns simulated time (PR #38); `4ced83c`, `60c252e` and `7aa4e3c` and their neighbours are documentation only. Previous: `06569cc` T2-5 background scheduler (PR #36); `42dc227` T5-3, gas-phase pressure accumulation (PR #35); `fefa841` T7-1, the control valve model (PR #32); `e261134` T4-5, the cause-and-effect assertion suite (PR #29, test-only). Previous: `154385c` T5-2, level to hydraulics coupling and the multi-domain Engine (PR #27). Previous: `0efa5be` T4-4, the solver wired into the engine (PR #25). Previous: `b8af231` T5-1 vessel model (PR #23); `d6a6cfb` T3-4 single-domain reference fixtures (PR #19); `2c08bb8` T3-6 multi-port equipment wiring (PR #20); `11517f6` T3-5 flow-domain declaration (PR #17); `c406ca0` YAML plant loading; previous solver merge `a2a1596` (T4-3)
**Architecture decisions on `main`:** ADR 0001 ([flow-domain separation](ADR_0001_FLOW_DOMAIN_SEPARATION.md)) with Amendment 1, and **ADR 0002 ([typed ports and the vessel connection model](ADR_0002_TYPED_PORTS.md))**, merged 20 September 2026 as `60c252e` and **amended twice** — Amendment 1, before T3-7, supersedes the single `service` axis with independent `purpose` and `control` descriptors; **Amendment 2, before T5-6, makes the hydraulic node the unit of account** for the aggregation the original record wrote as a sum over ports
**Full suite on `main`:** **868 passed** (`conda activate plant-simulator && python -m pytest -q`); `python -m mypy` clean over **24 source files**; no golden trace moved

**[T5-6](#in-flight-t5-6) is in flight** on `feature/coupling-aggregation`
([PR #44](https://github.com/gareytwin1/plant-simulator/pull/44), Opus, spine) and is **Ready for Review, not merged** — `main` does not
carry it. The sequence ADR 0002 sets is unchanged: **T5-6 → T5-5 → T5-7
later.** On the branch: **971 passed**, `mypy` clean over 24 source files,
no golden trace moved.

---

## Milestone progress

| Milestone | Status |
|---|---|
| **M0** Baseline Cleanup | **7/7 Complete** |
| **M1** Equipment Model Contract | **5/5 Complete** — Checkpoint A reached |
| **M2** Simulation Engine and Clock | **6/6 Complete** — T2-5 `06569cc`, T2-6 `4e48949` |
| **M3** Plant Topology and Streams | **7/7 Complete** — T3-7 (typed ports) merged as `8733b1a` |
| **M4** Pressure-Flow Network Solver | **5/5 Complete** — Checkpoint B reached; T4-5 was re-scoped before implementation, see [The T4-5 re-scope](#the-t4-5-re-scope) |
| **M5** Inventory and Mass Balance | 3/7 — T5-1, T5-2, T5-3 Complete. ADR 0002 added **T5-6** and **T5-7**; **T5-6 is Ready for Review**, holding the spine lock on `app/engine/coupling.py`; **T5-5 is Blocked** behind it; **T5-4 was re-sequenced after T5-5**. The engine and topology spine locks are otherwise released |
| **M7** Control Valves and Final Elements | 1/5 — T7-1 Complete; **T7-3 and T7-4 startable**; T7-2 also needs T5-5; **T7-5 (relief device) is new and now startable** (T3-7 merged), and blocks nothing |
| M6, M8–M19 | Not started |

Overall: **34 of 101 tasks Complete.** T3-7 merged; the count was 33 before it.

### Completed and merged to `main`

- **M0** — `T0-1` … `T0-7`: baseline cleanup, pump endpoints, unit convention,
  portable requirements, static typing baseline.
- **M1** — `T1-1` golden harness · `T1-2` Equipment base + Port (C1) ·
  `T1-3` compressor onto C1 · `T1-4` pump onto C1 · `T1-5` equipment registry.
- **M2** — `T2-1` simulation clock · `T2-2` seeded RNG (`SeededRNG`, no global
  stream — see the note below) · `T2-3` engine + snapshot (C4) ·
  `T2-4` session/plant registry.
- **M3** — `T3-1` plant config schema + validator (C3) ·
  `T3-2` node/branch/stream (C2) · `T3-3` plant loader (`app/plant/loader.py`) ·
  `T3-5` flow-domain declaration · `T3-6` multi-port equipment wiring ·
  `T3-7` typed ports (`8733b1a`, PR #42): `Port` phase / purpose / control, typed
  C3 `ports` entries, and the port-name guard.
- **M5** — `T5-1` vessel model (`app/equipment/vessel.py`): a coupling device,
  level integrated from `inlet_flow` / `outlet_flow` (GPM) · `T5-2` level to
  hydraulics coupling (`154385c`): `head_at_full * level` added to the
  `configured_pressure` of the boundary the vessel's OUTLET port attaches to,
  flows read back as net signed flow at each attachment, one solver per flow
  domain, and `"vessel"` registered in `DEVICE_TYPES`.
- **M4** — `T4-1` branch characteristic interface (sign convention,
  `signed_square`, `Branch.characteristic()` / `Branch.residual()`) · `T4-2`
  Newton-Raphson network solver (`NetworkSolver`) · `T4-3` solver diagnostics
  and failure handling (`SolverResult.failure`, `snapshot.solver_status()`; C4's
  shape unchanged) · `T4-4` solver wired into the engine (`0efa5be`): `Engine.step()`
  integrates, solves and publishes real `solver` / `nodes` / `streams`;
  `Engine.from_plant()` builds equipment from `Plant.devices`; the devices'
  standalone operating point is retired · `T4-5` cause-and-effect assertion
  suite (`e261134`, `tests/test_cause_effect.py`, 26 tests, test-only), re-scoped
  before implementation — two criteria moved to T7-1 · `T7-1` control valve model
  (`fefa841`, PR #32, `app/equipment/valve.py`, `control_valve` in the loader,
  domain-resolved flow unit in `coupling.py`, `config/plants/liquid_valve_train.yaml`,
  `tests/test_control_valve.py`, 103 tests). 681 tests and `mypy` clean over 23
  source files before merge; no golden trace changed.

### Seeded RNG: what shipped and what did not (T2-2)

`SeededRNG` (`app/engine/rng.py`) is a seeded generator instance; a seed is
required. `tests/test_random_source_guard.py` fails the build if any other module
in `app/` imports `random`. Nothing in `app/` draws random numbers yet.

**There is deliberately no module-level RNG and no `set_seed()`.** A process-wide
stream would be shared by every browser session, and each session owns its own
plant. **Open decision:** which object owns a plant's RNG (session, engine or
plant). It belongs to the first task that needs randomness. (T2-2 was once
recorded as Complete before it was merged; that was corrected, and the task is
now genuinely merged.)

## Architecture status

### What exists

| Component | Where | Notes |
|---|---|---|
| Equipment contract (C1) + `Port` | `app/equipment/base.py` | Frozen at Checkpoint A; `characteristic()` sign convention and `signed_square` added at T4-1 |
| `GasCompressor` (`K-101`) | `app/equipment/compressor.py` | On C1 |
| `CentrifugalPump` (`P-101`) | `app/equipment/pump.py` | On C1 as of T1-4 |
| `ControlValve` (`FV-101`) | `app/equipment/valve.py` | T7-1 (`fefa841`). Linear / equal-percentage, rate-limited stroke, `min_position` floor, fail-open / fail-closed. Config key is `flow_characteristic` because `characteristic` is the C1 method. Flow unit resolved from its domain |
| `Vessel` (`V-101`) | `app/equipment/vessel.py` | T5-1 + T5-3. A **coupling device**: holds no branch, lives in `Plant.devices`, in no `Topology`. Liquid level (gal) and gas pressure (psia) are integrated slow state |
| `EquipmentRegistry` | `app/equipment/registry.py` | Tag → device; rejects duplicate tags. **Not on the request path** |
| `SimulationClock` | `app/engine/clock.py` | Sim time, speed, pause |
| `Scheduler` | `app/engine/scheduler.py` | T2-5 + T2-6. One per `Engine`; deadline-scheduled fixed-`dt` worker, fail-stop, explicit `stop()` + join. A `Session` owns `compressor_scheduler` and `pump_scheduler`, started only by the matching page render |
| `Engine` | `app/engine/engine.py` | Integrates all devices, couples inventory, solves every flow domain, publishes snapshot. `Engine.from_plant()` wires one solver per entry in `Plant.topologies` (T5-2) |
| Inventory coupling | `app/engine/coupling.py` | T5-2 + T5-3 on `main`. Liquid head added to an OUTLET boundary; gas pressure **replaces** the boundary at every SCFM attachment; signed exchange read back. `FLOW_UNITS` decides the unit; **a new branch device must be added to it**. **T5-6 (branch, not merged)** makes the hydraulic **node** the unit of account — counted once per node, summed across nodes, gross components where both directions are declared — classifies from the declared `phase`, and retires `DOMAIN_UNITS` |
| `Snapshot` (C4) | `app/engine/snapshot.py` | Immutable; shape frozen |
| `SessionRegistry` / `Session` | `app/engine/sessions.py` | Per-browser plant isolation; one `Engine` per page over a loader-built single-device plant |
| `NetworkSolver` | `app/engine/network.py` | Newton-Raphson solver for plant-wide flows and pressures; called by `Engine.step()` since T4-4. Diagnostics and failure policy as of T4-3 |
| Topology (C2) | `app/plant/topology.py` | `Node`, `Branch`, `Stream`, `Topology`; `Branch.characteristic()` / `residual()` as of T4-1 |
| Plant config schema (C3) + validator | `config/schema/plant.schema.json`, `app/plant/validate.py` | |
| Golden regression harness | `tests/golden_regression.py`, `tests/fixtures/golden/` | |
| Flask app | `app/main.py` | Per-equipment routes, session-scoped |

### What intentionally does not exist yet

- **~~The Engine path is single-domain~~ — delivered by T5-2 (`154385c`).**
  `Engine.from_plant()` now wires one solver per entry in `Plant.topologies`,
  and the boundary-condition update couples them through vessel inventory.
  `Engine.topology` survives as a convenience that raises on a multi-domain
  engine, the same bargain `Plant.topology` makes.
- **The control valve has no hydraulic path on the live pages.** *(T7-1 built the
  valve and a plant that uses it, `liquid_valve_train.yaml`; the `Session` pages
  still run one machine between two fixed limits, so the interim state below
  stands for them.)* The line resistances and the
  `max_flow` clamp lived only in the retired standalone solve, so a page's flow
  runs above `max_flow`, the discharge valve strokes without changing flow, and
  spread equals the boundary difference. Equipment does not clamp; envelopes and
  alarms own that later. Known interim state, not a bug to fix in passing.
- **Reference plant files.** The loader reads `.json`, `.yaml` and `.yml` (YAML
  via `yaml.safe_load`, same C3 validation and `load_plant()` path), and T3-4
  (`d6a6cfb`) added the first plant files: `config/plants/liquid_transfer.yaml`
  and `gas_compression.yaml`, two single-domain fixtures checked against
  closed-form values in `tests/test_reference_plants.py`. The full
  `olefins_lite.yaml` train is T5-5, in M5, and does not exist.
- **Flow-domain enforcement is done for the loader only (T3-5, `11517f6`).**
  `load_plant` rejects a branch across domains, a domain with no boundary and a
  domain that is not one connected piece, and partitions into `Plant.topologies`.
  A `Topology` built by hand is still unchecked. `Session` calls the loader for
  the live pages' single-device plants.
- **Multi-port wiring in C3 is done for the loader only (T3-6, `2c08bb8`).**
  `equipment` takes `node_in`/`node_out` sugar or `ports` + `paths`; a device with
  `paths: []` is a coupling device, held in `Plant.devices` and in no `Topology`.
  The vessel (T5-1, `b8af231`) is the first device that uses it. The live pages'
  `Session` builds its single-device plants through the loader (T4-4).
  `DEVICE_TYPES` now maps `pump`, `compressor`, `control_valve` and `vessel`.
  **ADR 0002 rewrote what a `ports` entry may say:** T3-7 lets it carry a
  declared `phase`, `purpose` and optional `control`, enforced in the loader
  rather than the schema. Merged as `8733b1a`.
- **Single action endpoint (C5).** Routes are still per-equipment.
- **Controllers (PID), envelopes, alarms, trips, scenarios, scoring, historian,
  console.** All specified in the build plan, none implemented.
- `Snapshot`'s `controllers` / `envelope` / `alarms` sections are still empty
  by design. `nodes` and `streams` are filled from the solved topology since
  T4-4 for an engine built with one, and empty for `Engine(devices)`.

### Architecture decisions on file

Two ADRs are on `main`. A later task does not reverse a decision in either
without a new ADR.

#### ADR 0002 — typed ports and the vessel connection model

[docs/ADR_0002_TYPED_PORTS.md](ADR_0002_TYPED_PORTS.md), **accepted and merged
20 September 2026 as `60c252e`**, verified against `main` at 753 tests and a
clean `mypy`. It extends ADR 0001: where that settled what `ports` means
*structurally*, this settles what a port means *semantically*.

**Amendment 1 (20 September 2026) — four descriptors, not three.** Accepted
before T3-7 wrote any code, and **where it and the original record disagree,
the amendment wins.** The original `service` enum mixed physical purpose with
control function, so a manual drain and a level-controlled drain could not be
told apart while both being drains, and `flow` was unrepresentable. It is
replaced by two independent descriptors:

```text
name       identifier only, never behaviour
direction  inlet | outlet
phase      liquid | vapor
purpose    process | vent | drain | relief
control    flow | pressure | level | temperature, or absent
```

**Only `phase` and `direction` may participate in conservation.** `purpose` and
`control` are descriptive and never alter a balance. T3-7 builds this
vocabulary; **T5-6 aggregates over it.** Everything else in the record stands.

**Amendment 2 (20 September 2026) — the node is the unit of account.**
Accepted before T5-6 wrote any code, and **where it and D3.3, D3.8 or
Amendment 1 A.4 disagree, Amendment 2 wins.** The original record wrote the
four vessel aggregates as sums *over ports*, but `Attachment.net_flow` is
derived from every branch meeting the attachment **node** — so two ports on
one node would double-count it, while netting each node instead would lose
the gross throughput `residence_time` is made of. The amendment defines
node-based accounting with gross directional components, and settles the
rules T5-6 needs beside it: phase-first classification, the `DOMAIN_UNITS`
retirement, unit-neutral versus unrecognised equipment, branchless typed
attachments, one node per inventory device, and per-aggregate atomicity on a
failed domain.

What it found, by building the configuration and stepping it:

- **One port may already serve many branches.** `Attachment.net_flow` sums every
  branch meeting the node, so a vapour node carrying both K-101 and a
  pressure-control valve aggregates correctly today. The causal chain
  — PCV moves, vent flow moves, vessel pressure moves, K-101's flow moves —
  already works, lacking only the controller that will call
  `set_position_target()`.
- **Two vessel ports on two domains lose flow silently.** `write_flows`
  *assigns* to one of four fixed attributes, so a second vapour attachment
  overwrites the first: a measured **141.4214 SCFM, 26.1% of the withdrawal**,
  left the pressure balance with no error raised. The defect is the assignment
  target, not the plurality.
- **The working idiom requires the port names to lie.** Head is applied to an
  OUTLET attachment only, so the configuration that computes correctly must
  wire the port named `outlet` to the node that *receives* the feed.

What it decided:

- **A port carries orthogonal descriptors.** `direction` (unchanged) says which
  way material crosses; **`phase`** (liquid, vapor) says which inventory.
  *Originally a third `service` axis; **superseded by Amendment 1**, which
  splits it into **`purpose`** (process, vent, drain, relief) and an
  independent optional **`control`** (flow, pressure, level, temperature).*
  None is derived from another. `mixed` is reserved and rejected — splitting a
  two-phase stream is a flash calculation.
- **Port names are identifiers, not behaviour.** No code may branch on a port
  being called `inlet`, `outlet` or `suction`.
- **Coupling aggregates; it does not overwrite, and it does not prohibit.** Any
  number of connections may share a `(phase, direction)` pair, and the coupling
  sums them. Prohibition was considered and rejected: it would forbid an
  ordinary separator.
- **Metadata never changes the conservation math.** Only `phase` and
  `direction` classify a connection; `purpose` and `control` never alter a
  balance. A vent outlet under pressure control, and a relief outlet, sum into
  `gas_outlet_flow` exactly as a `process` outlet does. They exist so a
  controller can find its valve and the console can label a nozzle.
- **`DOMAIN_UNITS` is retired** once phase is declared, which removes the
  restriction that a vent, flare or relief header cannot be its own domain.
- **T5-5 ships manual PV-101 and LV-101; M8 owns the loops.** Requiring T5-5 to
  ship working pressure control would invert the T8-3 edge and deadlock M5
  against M8.
- **Component inventory and phase equilibrium are out of V1.** No composition,
  no flash, no K-values, no component balances. The port model stays capable of
  carrying more metadata later, which is the whole reason to type ports now.

> **Naming hazard.** The requirement that prompted the ADR used "C1/C2/C3" to
> mean methane, ethane and propane. This repository uses **C1–C8 for interface
> contracts**. Say *light-hydrocarbon components* explicitly.

**CLAUDE.md carries these invariants as of T3-7**, and not before: port names
are identifiers only, `phase` and `direction` own conservation classification,
and `purpose` and `control` are descriptive and never change a balance. See
*A connection is described, never inferred* in
[CLAUDE.md](../CLAUDE.md#critical-architectural-invariants). **T5-6's
aggregation is written but not merged** — it lives on
`feature/coupling-aggregation`, and `main` still assigns to four fixed
attributes.

Build-plan changes it made, now applied to the live artifact and both durable
copies: **T3-7**, **T5-6**, **T5-7** and **T7-5** added; **T5-5** gained T3-7
and T5-6 and a steady-state acceptance criterion; **T5-4** re-sequenced after
T5-5 and given the conservation-identity convention.

#### ADR 0001 — flow-domain separation

[docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md](ADR_0001_FLOW_DOMAIN_SEPARATION.md) (accepted 19 September 2026) froze these.

- **`NetworkSolver` stays single-domain.** One invocation solves exactly one
  compatible flow domain. A mass balance mixing GPM with SCFM is invalid and is
  rejected at load, not in the solver. The solver is not changed.
- **Domain belongs to the hydraulic connection, not the device.** Nodes carry it
  (optional `domain` in C3); branches and topologies derive it; a separator that
  spans two domains through different ports is why no device-wide domain exists.
- **The integrated train is two hydraulic problems** (liquid, gas) coupled through
  the vessel's slow state, never one mixed topology.
- **C1 and C2 need no change** — the conclusion stands, but Amendment 1 replaced
  the reason. Not because "multi-port devices already work in C2": a named
  `ports` map does **not** say which two ports form a `Branch`, and an
  inventory device that holds no branch cannot be in a `Topology` at all,
  because `Topology.devices` is derived from `Topology.branches`. C2 is
  untouched because a coupling device lives on `Plant`, one layer up, and its
  port-to-node binding is made with `Port.connect()`, which is existing C1 API.
- **`characteristic(flow)` is device-wide** (no port or branch argument). Recorded
  as a limitation, deliberately not widened.
- **Vessel pressure is integrated slow state supplying a boundary condition**, not
  a solver result written back into equipment. The route that does this is T5-2's.
- **No boundary pair is sane both cold and running** with only the pump and
  compressor models, so the "plausible steady state from cold" criterion moved
  from T3-4 to T7-1.

Build-plan changes it made: **T3-5** (new, satellite), **T5-5** (new), T3-4
re-scoped, and new dependencies on T3-5 (T3-4, T4-4, T5-1) and on T5-5 (T7-2,
T8-3, T9-2, T11-1, the tasks that edit `olefins_lite.yaml`).

### Amendment 1 (19 September 2026) — the connection model

Raised by the T3-5 implementation session, which stopped before editing anything
and was right to. Three contradictions, all confirmed; the full ruling and the
implementation contract are ADR 0001 **Amendment 1** and **section 12**.

- **A named `ports` map is attachment, and only attachment.** It says which node
  each port attaches to. It does **not** say which two ports form a `Branch`,
  and no heuristic may guess — not declaration order, not inlet×outlet, not
  reading `liquid_*` / `vapor_*`, not a "primary" outlet.
- **Hydraulic paths are a separate, explicit declaration.** C3 gains `paths`:
  each `{from, to}` names two of the device's ports and becomes exactly one
  `Branch`. In the `ports` form `paths` is required, and **`paths: []`** is the
  explicit way to say "coupling device, in no hydraulic solve".
- **A device with no path is in no `Topology`.** It lives in `Plant.devices`,
  bound by `Port.connect()`. That is where a vessel lives. One object, one
  slow-state identity, never duplicated across domains.
- **"A port wired across domains" is withdrawn** — a port attaches to one node
  and so sits in one domain. The invariant is per **branch**: both nodes of a
  path share a domain. A device whose *ports* span domains is valid, and is the
  coupling mechanism.
- **At most one hydraulic path per device, enforced at load**, because
  `characteristic(flow)` is device-wide and a second path would publish the same
  curve into a second branch. The task that widens C1 deletes that check.
- **No `oneOf` in C3.** `app/plant/validate.py` silently ignores `oneOf`,
  `additionalProperties`-as-subschema and `patternProperties`, so the schema
  clause ADR 0001 prescribed would have enforced nothing. The wiring-form
  alternation is a loader check that names the config path; the validator gains
  only `additionalProperties`-as-subschema and `minProperties`.
- **Domains never propagate.** An undeclared node is in `DEFAULT_DOMAIN`
  (`"default"`) and does not inherit a neighbour's, so a branch between a `gas`
  node and an undeclared one is a mismatch and is rejected.
- **T3-5 is split.** T3-5 keeps flow domains; new **T3-6** takes `ports`,
  `paths` and `Plant.devices`. T3-4 and T4-4 depend on T3-5 only, so Checkpoint
  B is unblocked by the smaller task. **T5-1 now depends on T3-6**, not T3-5.
- **T4-4 must build `Engine`'s equipment from `Plant.devices`**, never from
  `Topology.devices`, or a coupling device would silently never be integrated.

## Branch characteristic convention (T4-1)

The convention T4-2 builds against. Authoritative text is the
`Equipment.characteristic` docstring in `app/equipment/base.py`.

- `characteristic(flow)` is **outlet minus inlet**: positive is a rise (machine),
  negative a drop (valve, pipe). The direction is fixed by the ports, never by
  the sign of the flow.
- Flow is signed; positive runs inlet -> outlet. Every real flow is in range,
  including negative — a device may not raise, clamp or return a non-finite
  number outside the range it expects to operate in.
- The curve is **non-increasing in flow everywhere**, so the branch equation has
  exactly one root and the Jacobian diagonal does not vanish.
- Write every quadratic term against `signed_square(flow)` (`|q|*q`), never
  `flow ** 2`. The latter is even and reports the same pressure change at -100
  as at +100.
- `Branch.residual(flow)` is `from_node.pressure + characteristic(flow) -
  to_node.pressure`: the branch equation T4-2 drives to zero. Both branch
  methods take a trial flow and default to the flow the branch is carrying.

## Static typing

**Permanent policy:** type hints are **required** in new and modified production
Python under `app/`. The rules — what must be typed, what must not be annotated
for its own sake, and when `Any` is acceptable — are in
[CLAUDE.md](../CLAUDE.md). This replaced the project's earlier "no type hints"
rule.

**Current migration state:** all of `app/` is inside the checked scope and
`python -m mypy` is clean. `mypy` is pinned in `requirements-dev.txt` and
configured in `pyproject.toml`; it checks `app/` only.

| Module | State |
|---|---|
| `app/statetypes.py` | New. `JSONValue` / `StateRow` — the JSON-safe row every `get_state()` returns |
| `app/equipment/base.py`, `registry.py`, `compressor.py`, `pump.py` | Typed |
| `app/engine/clock.py`, `engine.py`, `snapshot.py`, `sessions.py` | Typed |
| `app/plant/topology.py`, `validate.py` | Typed |
| `app/main.py` | Typed (`flask.typing.ResponseReturnValue`) |
| `app/config.py` | Unannotated by design — its constants infer exactly |
| `tests/` | Outside the checked scope; tests stay lightly typed |

`app/plant/validate.py` is the only deliberate `Any`: it walks decoded JSON
whose shape is what it exists to discover.

Strictness today is "every def in `app/` is annotated, no implicit `Optional`,
no bare generics". Tightening further (`disallow_untyped_calls`,
`warn_unreachable`, `strict`) is a later decision, not a silent one.

## Known temporary compatibility paths

**Do not "fix" these as a side effect of an unrelated task.** Each is deliberate
and has a task that retires it. T4-4 retired the legacy device `step()` path,
the interim `*_boundary_pressure` attributes, `simulation_speed`, and the
Engine-computes-nothing state; what remains is the consequence of that:

1. **A page's machine runs between two fixed battery limits with nothing in
   between.** The `Session` plants are one machine and two boundary nodes
   (750/750 psia and 50/50 psia). Flow reads above `max_flow` (compressor 331.7
   vs 120, pump 2236 vs 1200), the discharge valve strokes without changing
   flow, and spread equals the boundary difference so temperature is flat. Equal
   boundaries were kept so an idle machine sits at zero flow — exactly zero if it
   has never run, and within a hair of it once stopped; see *A stopped machine
   keeps a small residual flow* under Known technical debt.
   *Retired by:* **T7-1** built the valve; the pages themselves are not yet
   rewired onto a plant that contains one.

2. **No check valve.** With a boundary differential and a machine slower than it,
   the solver finds reverse flow through the machine, where the retired standalone
   solve clipped at zero. The live pages do not hit it because their boundaries
   are equal; a plant sized for running and started cold would.
   *Retired by:* not retired. **T7-1** carried the cold-start criterion and met
   it between equal boundaries, but a resistance cannot stop reverse flow against
   an adverse gradient (see Known technical debt). Needs a check valve.

3. **`get_state()` on a device is slow state only.** Flow and the two pressures
   are not device attributes; the page's row is assembled by
   `Session.compressor_state()` / `pump_state()` from the snapshot. The
   compressor's `temperature` became `temperature_at(spread)` because spread is
   a solver output. Do not put a flow or pressure back on a device.

## Known technical debt (recorded, not scheduled)

- **A stopped machine keeps a small residual flow.** A machine that has never run
  reads exactly `0.0`. One that has been *stopped* settles just off zero and
  stays there: **0.055 GPM** on the pump page, **0.004 SCFM** on the compressor,
  unchanged after a further 2200 simulated seconds. Convergence is measured in
  psia, and at shutoff the branch curve is flat — the root is a double root, so
  the 1e-7 psia of slack the tolerance allows maps to `sqrt(tolerance /
  resistance)` of flow, 0.08 GPM and 0.007 SCFM. The solver then reports
  `converged` at **`iterations: 0`**, because the residual is already inside
  tolerance on entry and nothing drives the flow the rest of the way down. The
  exact solution is still zero; only the reported one is not. Not a defect and
  not a reason to retune the tolerance — `tests/test_network_solver.py` has
  asserted `approx(0.0, abs=0.05)` on a stopped pump, with the reason in a
  comment, since T4-2, and `tests/test_cause_effect.py` asserts against a bound
  derived from the tolerance rather than a recorded value. **Do not assert an
  idle flow of exactly zero.** T5-2 settled what becomes of that residual
  downstream, and the answer is that nothing special does: a solved flow is
  written onto a coupling device and integrated with **no clamp and no
  deadband**, so a stopped machine's residual reaches vessel level at 0.055 GPM,
  or 3.3 gal/hour against a 1000 gal vessel. That is the decided behaviour; what
  stays recorded here is the numerical residual itself.
- **The `Equipment.characteristic` docstring overstates the Jacobian.** It says a
  non-increasing curve "keeps the Jacobian from going singular". For
  `-K * signed_square(q)` the derivative is `-2K|q|`, **zero at q = 0**, so the
  diagonal does vanish at zero flow — for a valve, a pipe and a machine alike.
  The curve is monotone non-increasing with exactly one root; that is all the
  docstring can claim. The vanishing diagonal is the double-root behaviour
  behind the residual-flow entry above. `base.py` is frozen, so it was not
  edited at T7-1; whoever next opens C1 should correct the wording.
- **A resistance-only valve cannot stop reverse flow, and it absorbs most of
  the drop.** Against an adverse boundary pair (50 -> 180 psia) a cold plant
  backflows through a wide-open valve (-1000 GPM at Cv 100), and closing it only
  trims that by the square root of the added resistance. Only a check valve or a
  non-adverse boundary pair stops it. Separately, with the valve the only
  resistance the reference plant's valve takes 107-139 psi of the 150 psi the
  pumps make, 70-93% of system drop against 10-30% in a real plant. A pipe or
  resistance device is needed for line loss; no task owns one yet.
- **`app/init.py` is a misnamed empty file** — it is not `__init__.py`. `app`
  resolves as a namespace package so imports work anyway. Harmless; has never
  had a task.
- **`static/style.css` is 15 lines** and defines almost none of the classes the
  templates use (`.panel`, `.status-grid`, `.control-group`, …). The pages are
  largely unstyled. Intentional — the console is rebuilt wholesale at M16.
- **Contract-test discovery is import-order dependent.** `REGISTERED` in
  `tests/test_equipment_contract.py` is computed at module import time, so an
  `Equipment` subclass defined in a test module imported *later* silently escapes
  the parametrized contract tests. Flagged at T1-2; `tests/test_topology.py`'s
  `Device` double is a live instance of it. Passes either way today.
- **History carries T1-5 twice** (`d41949a` + `5ea07cb`, identical content) from
  a branch race. Already pushed; deliberately not rewritten.
- **`package.json` / `node_modules/`** exist only for a TypeScript dev
  dependency and are not part of the app.

## Active branches and PRs

**`feature/coupling-aggregation` is in flight (T5-6, Ready for Review, [PR #44](https://github.com/gareytwin1/plant-simulator/pull/44)) and holds the spine lock on `app/engine/coupling.py`.** Nothing else may change that module until it merges or is abandoned. Previously, T3-7 merged as `8733b1a` ([PR #42](https://github.com/gareytwin1/plant-simulator/pull/42)) and released the `app/equipment/base.py` lock; `feature/typed-ports` and its worktree are deleted. Previously, T2-6 merged as `4e48949` ([PR #38](https://github.com/gareytwin1/plant-simulator/pull/38)): `Session` owns `compressor_scheduler` / `pump_scheduler`, started only by the `/compressor` or `/pump` page render and stopped only by `Session.end()` (direct, `SessionRegistry.end()`, or LRU eviction past `config.MAX_SESSIONS = 32`). Design: [docs/T2-6_SCHEDULER_OWNERSHIP.md](T2-6_SCHEDULER_OWNERSHIP.md). The build plan's file list named only `static/compressor.js`; `static/pump.js` changed too, because it stepped pump physics on its own timer. The `app/main.py` and `app/engine/sessions.py` locks are released.

**T18-5 still owns idle-session reclamation.** The T2-6 cap is only the bounded-resource guard that makes scheduler ownership safe; it is not idle expiry and does not close out T18-5.

**No other spine lock is held.** T5-2 merged as `154385c`
([PR #27](https://github.com/gareytwin1/plant-simulator/pull/27)) and released
the locks on `app/engine/engine.py` and `app/plant/topology.py`.

**T5-3 merged** as `42dc227` ([PR #35](https://github.com/gareytwin1/plant-simulator/pull/35),
based on `4259617`; 715 tests, `mypy` clean on `main`). It touches `app/equipment/vessel.py`,
`app/engine/coupling.py` and `app/config.py` only.

- **Rate law.** `dP/dt = P_std * (Q_in - Q_out) / V_gas` (psi/min, SCFM, ft^3),
  isothermal at the SCFM standard temperature, `P_std = 14.696` psia
  (`config.STANDARD_PRESSURE`). No Z-factor, no temperature dynamics. The
  standard-volume inventory `P * V_gas / P_std` (scf) changes by exactly the net
  SCFM, so mass closes to rounding. Verified by hand: 70.7 SCFM into 100 ft^3 is
  10.39 psi/min.
- **Routing.** A vessel keeps GPM (`inlet_flow`, `outlet_flow`) and SCFM
  (`gas_inlet_flow`, `gas_outlet_flow`) apart. The flow unit confirmed at each
  attachment picks the pair, never the port name.
- **Boundaries.** Vessel pressure *replaces* the runtime boundary at **every** gas
  attachment, inlet and outlet, so the inlet machine sees the back-pressure.
  `configured_pressure` is untouched and `Plant.to_config()` still round-trips.
  Liquid keeps its head-as-offset on the outlet only, and a head never reaches a
  gas boundary.
- **Attachment-driven.** A confirmed SCFM attachment activates the gas phase
  (`Vessel.activate_gas()`, called by the coupling). A liquid-only vessel never
  integrates pressure and its `get_state()` is unchanged; a gas vessel adds
  `pressure`, `gas_volume`, `gas_inventory`, `gas_inlet_flow`, `gas_outlet_flow`.
- **Reference numbers** (`tests/test_gas_inventory.py`): compressor 60 psia with
  shutoff 220 and R 0.002 into the vessel, vent valve capacity 20 to 14.696 psia.
  Balance at 162.09 psia and 242.8 SCFM; linearised time constant
  `V / (P_std * 1.853)`, 220 s for 100 ft^3 (measured to within 10%).
- **Known limit, not fixed and no clamp added.** The compressor curve and the
  valve are square-root laws, so the flow's slope is infinite at zero flow and
  explicit Euler steps across a zero-flow point (the vent pressure, compressor
  shutoff) by about `(a * dt)^2 / 4`, `a = P_std * C / (60 * V)`. That is 0.0006
  psi at 100 ft^3 and 0.06 psi at 10 ft^3 (dt = 1 s), a bounded limit cycle
  near 1 ft^3, and at 0.3 ft^3 pressure would go negative, where the
  strictly-positive guard raises. Supported: 10 ft^3 and up at a one-second step.
- The three pre-T5-3 tests that encoded "gas is not coupled yet" were rewritten,
  not weakened: the SCFM-never-reaches-GPM and head-never-reaches-gas guarantees
  remain, and an unclassifiable node still raises.

**T4-5 merged** as `e261134` ([PR #29](https://github.com/gareytwin1/plant-simulator/pull/29)):
`tests/test_cause_effect.py` and nothing else. It was rebased onto `154385c`
first and every reference value re-derived — each of its plants is
single-domain and holds no coupling device, so `build_couplings()` returns `[]`,
one solver is wired as before, and no assertion moved.

T4-4 merged as `0efa5be`
([PR #25](https://github.com/gareytwin1/plant-simulator/pull/25)) and released
`app/equipment/compressor.py`, `app/equipment/pump.py`,
`app/engine/sessions.py` and `app/main.py`.

T5-1 merged as `b8af231` ([PR #23](https://github.com/gareytwin1/plant-simulator/pull/23)): `Vessel` and `tests/test_vessel.py`, nothing else. T3-4 merged
as `d6a6cfb` ([PR #19](https://github.com/gareytwin1/plant-simulator/pull/19)): two
single-domain reference fixtures and their tests, nothing under `app/`.

T3-6 merged as `2c08bb8` ([PR #20](https://github.com/gareytwin1/plant-simulator/pull/20)).
It implements ADR 0001 section 12.1-12.4, 12.8 and 12.9: `ports` (attachment only)
and `paths` (one `Branch` each) in C3, one-form-only enforced in the loader, more
than one path rejected (A6), `Plant.devices` (config-ordered, every device, coupling
devices included), the dangling-port check moved onto it, and a form-preserving
`to_config()`. `validate.py` gained `minProperties` and `additionalProperties` as a
subschema. C1, C2, `NetworkSolver` and `Engine` are untouched. **T4-4 must build
the Engine's equipment from `Plant.devices`, never `Topology.devices`**, or a
coupling device would never be integrated.

T3-5 merged as `11517f6` ([PR #17](https://github.com/gareytwin1/plant-simulator/pull/17)): domains only, `Plant.topologies`, flat `Plant.nodes`.

T4-3 merged as `a2a1596` ([PR #14](https://github.com/gareytwin1/plant-simulator/pull/14)) and released `app/engine/snapshot.py`.

Merged local branches can be deleted at any time.

## Next integration checkpoint

**Checkpoint B (M4, network solver merged) is reached** (T4-4, `0efa5be`). The
plant is connected: a step integrates, couples inventory, solves every domain
and publishes. T5-2 and T5-3 landed the coupling, and T2-5/T2-6 gave the
scheduler ownership of simulated time.

**Checkpoint C (M5, M6 and M7 complete) is the next one, and it is not close.**
M6 has not started, M5 is 3/7 with T5-5 blocked, and M7 is 1/5.

## Handoff: the next agents

**Everything below is verified against `main` at `60c252e`** (753 tests, `mypy`
clean over 24 source files). Read [CLAUDE.md](../CLAUDE.md) first, then the
build plan entry for your task. Model guidance is the **Agent model guidance**
section of CLAUDE.md; the column below is the build plan's own assignment.

### T3-7, typed ports (merged)

**Merged as `8733b1a` ([PR #42](https://github.com/gareytwin1/plant-simulator/pull/42)).** Cut from `main` at
`4ced83c` (baseline 753 tests, `mypy` clean over 24 source files).

Amendment 1 to ADR 0002 was written **before** any production code, because the
original `service` axis could not describe a level-controlled drain and a
manual drain as different things while calling both drains. What T3-7 delivers:

| | |
|---|---|
| **Branch** | `feature/typed-ports` |
| **Files** | `app/equipment/base.py`, `config/schema/plant.schema.json`, `app/plant/loader.py`, `tests/test_typed_ports.py`, `tests/test_port_name_guard.py`, `CLAUDE.md`, `docs/ADR_0002_TYPED_PORTS.md`, `docs/BUILD_PLAN.html`, `docs/BUILD_PLAN_STATUS.json`, `docs/PROJECT_STATE.md` |
| **Model** | **Opus** — a contract change on a spine file |
| **Lock** | **SPINE.** `app/equipment/base.py`, alone. **Released on merge** |
| **Suite** | 868 passed on `main` after the merge, `mypy` clean over 24 files, no golden trace moved |

- `Port` carries `phase`, `purpose` and an optional `control` in `__slots__`,
  validated through `Port.declare()`. `direction` is unchanged, and `phase`
  `mixed` is refused **as reserved** rather than merely unknown.
- A C3 `ports` entry may be a node-id string **or** a typed object
  `{node, phase, purpose, control?}`. There is no half-typed form. The
  alternation is a **loader** check naming paths down to
  `$.equipment[2].ports.pressure_out.phase`; the schema only describes the
  shape, because the C3 validator ignores `oneOf`.
- `to_config()` is form-preserving per entry: a string returns as a string, a
  typed object returns typed with its metadata, sugar stays sugar. **A legacy
  config is never silently upgraded**, and typed and untyped entries may sit in
  one `ports` map.
- Descriptors reach the runtime `Port` from **configuration only**. No device
  class and no port name is consulted.
- `tests/test_port_name_guard.py` makes "port names are identifiers" an
  enforced rule rather than a convention — verified by injecting a real
  violation into `vessel.py`, not only against synthetic snippets.

**What T3-7 deliberately did not do:** it did not touch
`app/engine/coupling.py`, implement any aggregation, retire `DOMAIN_UNITS`, or
widen `characteristic(flow)`. Nothing yet reads `phase` to decide a balance —
that is T5-6.

### In flight: T5-6

**Ready for Review on [PR #44](https://github.com/gareytwin1/plant-simulator/pull/44), branch `feature/coupling-aggregation`. Not merged.**

| | |
|---|---|
| **Branch** | `feature/coupling-aggregation` |
| **Files** | `app/engine/coupling.py`, `tests/test_coupling_aggregation.py` |
| **Model** | **Opus** |
| **Lock** | **SPINE** by the `app/engine/` rule — it modifies an existing module there |
| **Suite** | 971 passed on the branch, `mypy` clean over 24 files, no golden trace moved |

The four vessel aggregate flows are sums over the **hydraulic nodes** a
vessel attaches to (ADR 0002, Amendment 2) rather than assignment targets,
which recovers the measured 141.4214 SCFM that `write_flows` was dropping.
Each node is counted once however many nozzles sit on it; a node declaring
both directions keeps its gross components, one declaring a single direction
keeps its net, and both are signed sums over branch orientation.
Classification comes from the declared `phase`, confirmed against the
machines at the node; `DOMAIN_UNITS` is gone, `ControlValve` is listed as
`UNIT_NEUTRAL`, and an unrecognised device still refuses. An aggregate fed by
a domain that did not converge holds its previous value in full.

Every existing single-attachment plant is bit-identical, which is what the
unchanged golden traces and the untouched closed-form gas numbers say.

Two things it did not do:

- **It does not reject several nozzles sharing a `(phase, direction)` pair.**
  Prohibition was considered and rejected: it would forbid a physically valid
  separator.
- **`purpose` and `control` appear in none of the four sums.** Only
  `phase` and `direction` classify a connection (ADR 0002, Amendment 1 A.3). A
  relief or vent outlet sums into `gas_outlet_flow` exactly as a process outlet
  does.

One thing to know before T5-5 writes a fixture: **ADR 0002 §2.2's numbers**
(level 0.12335, 78.140 psia, 357.414 GPM) come from a probe configuration
that was never recorded and could not be reproduced from the record. T5-6's
shared-node fixture builds the same idiom with round design values and pins
its own steady state — 361.15756 GPM at level 0.15217391 and 113.04348 psia
— which sits inside a band 4e-8 wide relative from step 5 000 out past step
200 000, self-regulating and unclamped. The §2.2 *behaviour* reproduces; the
§2.2 *constants* belong to that lost probe.

**Then T5-5**, which is Blocked until this merges. **T5-4 follows T5-5**, not
T5-2 — see *T5-4 and the conservation identity* below.

#### T5-5 resumes at construction, not at design

**There is no second architecture phase for T5-5, and starting one is a
mistake.** T5-5's design phase already ran; its output is ADR 0002. Section 1
of that ADR describes the investigation in the past tense — *"A design
investigation against `f8aab59` was asked to confirm the train's intent before
writing design values"* — and it is a record of work that finished, **not a
template for doing it again**.

Once T5-6 merges, T5-5 goes straight into plant construction:
write `config/plants/olefins_lite.yaml`, choose and justify design values, size
the train, write the tests, meet the acceptance criteria. Every structural
question is already answered and is to be *implemented*, not reopened:

| Question | Settled by |
|---|---|
| How is the vessel wired? | Shared-node idiom — one node per phase (ADR 0002 §3.8) |
| What does a port declare? | `phase`, `purpose` and an optional `control`, delivered by T3-7 (§3.1–3.2 as amended by Amendment 1) |
| How do several nozzles combine? | Summed over the **nodes** they attach to, each counted once, delivered by T5-6 (§3.3 as amended by Amendment 2) |
| Are the valves controlled? | No. PV-101 and LV-101 are **manual at fixed position**; M8 owns the loops (§3.7) |
| Is composition modelled? | No. No flash, no K-values, no component balances (§3.6) |

**The one thing that warrants stopping.** If no choice of design values gives
both a credible cold start *and* a credible running point, that is the missing
**check valve** — see ADR 0001 section 2.9 and *A resistance-only valve cannot
stop reverse flow* under Known technical debt. Escalate it as its own task.
Do **not** redesign the vessel connection model, and do **not** shorten a test
horizon to hide it — hiding it behind a horizon is the precise defect ADR 0002
was written to remove.

### Critical path after Checkpoint B

**M4 and the coupling are both done.** T4-4, **T4-5** (cause-and-effect suite,
test-only, re-scoped — see below), **T5-2** (level-to-hydraulics coupling,
spine) and **T5-3** (gas-phase accumulation) are all Complete.

The solver is on the request path. `Engine.step()` calls `NetworkSolver` once
per domain, the snapshot's `solver`, `nodes` and `streams` are real, and
topology owns hydraulic flow and pressure. **T3-7 and T5-6 must not widen
`characteristic(flow)`, and vessel pressure stays integrated slow state
supplying a boundary condition** — see ADR 0001 section 3.5 and ADR 0002
section 9.

### T5-4 and the conservation identity

**T5-4 is no longer startable and is no longer next.** ADR 0002 section 8.2
re-sequenced it after T5-5, because its "10,000 steps at steady state with no
drift" criterion was written against a vessel that fills monotonically — there
being no liquid draw, the only way to pass was to stop the run before level
clamped at 1.0, a test-horizon trick standing in for missing physics. T5-5's
separator reaches a real steady state, so the criterion becomes a genuine one.

T5-4 also **inherits ADR 0002 section 7.1** and owns writing it down:

`Engine._couple()` runs in the constructor, so a flow exists before the first
`step()` and each `integrate` consumes the flow solved on the *previous* pass.
The exact identity is

```text
Δinventory  =  Σ (n = 0 … N−1)  q_n · dt / 60
```

with `q₀` read from `engine.snapshot()` before the first step. Summing the N
*published* flows instead is wrong by `(q_N − q₀)·dt/60` — measured at **2.36
scf over 10 000 gas steps, 0.09%**, four orders outside any sensible tolerance,
and it reads as a physics bug. It hides completely on a constant-flow domain,
where `q_N = q₀`. **Record it; do not paper over it in a fixture.**

Related and **owned by nobody yet**: near zero vapour flow, where
`dε/dt ∝ −√ε`, explicit Euler settles into a stable period-2 limit cycle,
measured at ±0.0615 SCFM and ±7.5e-6 psi and still bounded at step 12 000. It
gets its own numerical-stability task *if and when* it matters for M8
controller testing. Until then, **assert a pressure asymptote rather than a
final flow**, which is phase-dependent.

**T3-4 is Complete (`d6a6cfb`).** ADR 0001 settled the design and re-scoped the task. Verified against
`main` on 19 September:

- The **YAML half is merged** (PR #15, `c406ca0`) — `.json`/`.yaml`/`.yml` in
  `load_plant_file`, `PyYAML` + `types-PyYAML` pinned, `tests/test_plant_yaml.py`.
- The **original full train cannot be expressed or solved honestly yet**: there
  is no vessel model (`DEVICE_TYPES` maps only `pump` and `compressor`), C3
  cannot wire a multi-port device, and a mixed liquid/gas config solves to a
  meaningless answer (a pump and compressor in series converge to the same number
  as GPM and as SCFM). That train is now **T5-5**, after the vessel and the
  inventory coupling.
- **T3-4 delivered two single-domain fixtures**, both re-run on `main`:
  `liquid_transfer.yaml` (booster into transfer pump, 50 to 180 psia, solves to
  816.50 GPM with the internal node at 115.00 psia) and `gas_compression.yaml`
  (two-stage compression, 60 to 480 psia, solves to 70.71 SCFM with the internal
  node at 270.00 psia). Both answers are closed-form and hand-checkable.
- **Do not assert a cold-start operating point in those fixtures**
  (`tests/test_reference_plants.py` deliberately does not). With no check
  valve, line resistance or control valve, a stopped machine backflows if the
  boundaries are sized for running and a started one runs away if they are sized
  for cold. That criterion moved to T7-1.

### The T4-5 re-scope

**Agreed 20 September 2026, before any test was written.** The build plan's
original four acceptance criteria for T4-5 predate T4-4, which retired the
devices' standalone solve and with it the line resistances and the `max_flow`
clamp. Two of the four cannot be written against `main` at all:

| Original criterion | Now |
|---|---|
| Closing a downstream valve lowers flow and raises upstream pressure | **Moved to T7-1** |
| Added restriction raises upstream pressure | **Moved to T7-1** |
| More compressor load raises flow and discharge pressure | Kept, sharpened |
| Two parallel pumps raise flow by less than 2x | Kept, sharpened |

Both moved criteria need a second resistance in the network. Until T7-1 gives the
control valve a branch of its own, a machine runs between two fixed battery
limits with nothing between them and the compressor's discharge valve strokes
without changing anything hydraulically — item 1 of *Known temporary
compatibility paths*. They moved rather than merging as permanently skipped
tests, and **M4 is not held open waiting for T7-1**: T4-5 completes on what is
expressible now. T7-1 gained both criteria and a `T4-4` dependency, since a
valve's effect on a plant is asserted through `Engine.from_plant()`.

T4-5's kept criteria are joined by speed affinity, boundary response, stopping,
node mass balance and run-twice determinism. Two traps a session writing that
suite must not rediscover:

- **Backflow hides inside a "flow rises" assertion.** On the reference gas plant
  (60 -> 480 psia) raising `K-101`'s load 0.8 -> 0.9 -> 1.0 gives −121.7, −73.8,
  +70.7 SCFM. Flow does rise, so a naive assertion passes while the machine
  backflows. **Assert `flow > 0` in every compared state**, and pick a boundary
  difference the machines clear — 60 -> 300 psia gives forward flow on the series
  gas plant from about 0.75 load.
- **Assert only on settled states.** Load ramps at 0.05 per second, so a
  mid-ramp state can sit in the backflow regime.

### Open questions carried from T3-3

Question 1 still has no task and needs an owner (an Opus decision). Question 2
is retired: **T3-6** (merged) carried the C3 named-port wiring (moved from T3-5 by ADR
0001 Amendment 1).

1. **`Equipment.reset()` drops design values.** The loader applies a config's
   `design` by setting attributes after construction, but `reset()` restores the
   state captured at the end of `__init__`. A reset device returns to class
   defaults, not the configured design. Fixing it needs a design/configure hook
   on C1 — a spine change — before anything relies on `reset()` for a loaded
   plant. Also relevant to T12-1 (save/restore).
2. **Resolved by T3-6 (`2c08bb8`).** C3 now wires a multi-port device with
   `ports` and `paths`; T5-1 uses it and must not invent its own.

### Open question carried from T2-2

**Who owns a plant's RNG** (session, engine or plant)? Undecided on purpose — see
"Seeded RNG" above. `SeededRNG` also has no state save/restore, which T12-1
(snapshot save and restore) and T14-5 (deterministic replay) will need.

### Startable now (19 tasks)

All dependencies are Complete. T12-1 adds a *new* isolated module under
`app/engine/`, which is satellite work under the **`app/engine/` rule** in
CLAUDE.md.

**Changes since the last refresh:** **T5-6 started and left the list** — it is
Ready for Review on `feature/coupling-aggregation` and holds the spine lock on
`app/engine/coupling.py`, so nothing here may touch that module. T5-5 stays
Blocked behind T5-6, and T5-4 was re-sequenced behind T5-5 (ADR 0002 section
8.2). T7-3 edits `app/equipment/valve.py` and should not run beside another
valve change.

| Task | Name | Model | Branch |
|---|---|---|---|
| **T6-1** | Stream enthalpy and mixing *(start here)* | Opus | `feature/stream-enthalpy` |
| **T7-3** | Valve fault modes | Sonnet | `feature/valve-faults` |
| **T7-4** | Command arbitration | Sonnet | `feature/command-arbitration` |
| **T7-5** | Relief device *(deferred in sequencing, blocks nothing)* | Sonnet | `feature/relief-valve` |
| **T13-1** | Malfunction model and registry | Opus | `feature/malfunction-model` |
| **T12-1** | Plant snapshot save and restore | Sonnet | `feature/state-persistence` |
| **T8-1** | PID block | Sonnet | `feature/pid-block` |
| **T9-1** | Envelope evaluator | Sonnet | `feature/envelope-evaluator` |
| **T10-1** | Alarm state machine | Sonnet | `feature/alarm-state-machine` |
| **T14-1** | Scenario file schema | Sonnet | `feature/scenario-schema` |
| **T15-1** | Operator action log | Sonnet | `feature/action-log` |
| **T16-1** | Console design system | Sonnet | `design/console-system` |
| **T18-1** | Container and WSGI serving | Sonnet | `chore/container-and-ci` |
| **T18-2** | CI pipeline | Haiku | `chore/ci-pipeline` |
| **T16-2** | Snapshot push transport | Sonnet | `feature/snapshot-transport` |
| **T18-4** | Structured logging and health | Sonnet | `feature/observability` |
| **T13-5** | Physics isolation guard | Haiku | `test/import-direction-guard` |
| **T15-4** | Score persistence | Haiku | `feature/score-store` |
| **T17-1** | Ring-buffer historian | Haiku | `feature/historian` |

**Blocked, and why:** **T5-5** (integrated reference plant) waits on T5-6;
**T5-4** waits on T5-5; **T5-7** waits on T5-5.

Working rules for every one of them: one worktree per task branched from
`origin/main`; full suite **and** `python -m mypy` green before review; small
commits led by the task ID; **Ready for Review** at PR time and **Complete** only
with the merge SHA. `tests/test_random_source_guard.py` shows the AST-scan style
a layering guard like T13-5 can follow.

## Solver and snapshot state

**`SolverResult` (`app/engine/network.SolverResult`)**:
- `converged`: bool — whether the solver converged
- `iterations`: int — number of Newton-Raphson iterations
- `residual`: float — maximum of all residuals, dimensionless (each scaled by
  its own tolerance); converged is exactly `residual <= 1.0`
- `pressure_residual`: float — worst branch-equation residual, psia
- `flow_residual`: float — worst mass-balance residual, in the branch's flow unit
- `failure`: `str | None` — why a non-converged solve stopped, `iteration_cap`
  or `line_search_stall`. Set on exactly the results that did not converge:
  `converged` and `failure` cannot disagree, and a result that tried to claim
  both is refused at construction.

**Failure policy (T4-3).** Two-sided and deliberate:

- **Structural / ill-posed network raises `SolverError`** — no boundary node to
  anchor the pressure field, an unknown appearing in no equation. No iteration
  count or tolerance would have helped, so there is nothing for a caller to
  decide.
- **Numerical non-convergence returns `SolverResult(converged=False)`** with
  `failure` set. The cap was reached or the line search stalled; the same plant
  may solve from a different state or at a looser tolerance. That is a flag,
  never a success. `raise_if_not_converged()` is the opt-in escalation for a
  caller that would rather have the exception, and `describe()` gives one
  diagnostic line.

`solve_network()` delegates and therefore follows exactly the same policy —
there is one, not two. A failed solve is atomic either way: unless the solve
converges and commits, every node pressure and branch flow is what it was on
entry, so a caller that ignores the flag reads the pre-solve plant rather than
an unchecked iterate.

**Snapshot solver section (C4) is unchanged in shape**: `converged`,
`iterations`, `residual`, and nothing else. `app.engine.snapshot.solver_status(result)`
is the projection a caller uses, reading `converged` straight off the result so
a failed solve cannot arrive looking like one that landed. `pressure_residual`,
`flow_residual` and `failure` stay off C4 on purpose — widening it is a contract
change and its own task. `build_snapshot()` refuses *half* a solver section (a
residual with no `converged` reads as a clean solve to any consumer that treats
the flag as optional) and refuses an unexpected key; `solver={}` still means "no
solve to report". The default placeholder is now only what an `Engine` built
with no topology reports; an engine built from a plant publishes
`solver_status(result)` of its real solve every step (T4-4).

**Damping, recorded not retuned.** Heavy under-relaxation (`damping <= 0.25`)
can exhaust the default 50-iteration cap on a plant that solves in four
iterations at full step. T4-3 made that legible rather than changing it: the
failure reads `iteration_cap` at a residual a few times tolerance — a slow
solve, not a stuck one. The cap is untouched and is not coupled to damping.

**The build plan's reference-plant acceptance test can now run.** "Residual falls
below tolerance on the reference plant" has files to run against: T3-4's two
single-domain fixtures converge under `NetworkSolver` unchanged (5 and 4
iterations). T4-3's own convergence tests still use a hand-built plant
(`tests/test_solver_diagnostics.py`). The integrated train (T5-5) comes later.

**Process-domain caveat (enforced in the loader as of T3-5)**:
A `Topology` must be a single flow domain (gas or liquid, not both), and
`NetworkSolver` solves exactly one. The loader now enforces this (T3-5, `11517f6`): it partitions
nodes by declared `domain` into one `Topology` per domain and rejects a branch
that crosses domains, at load, naming the config path. C2 and the solver still
carry no domain metadata, so a `Topology` assembled by hand is unchecked. The
solver is untouched — `NetworkSolver` still solves exactly one domain per
invocation. What changed at T5-2 is the caller: `Engine.from_plant()` builds one
solver per entry in `Plant.topologies` and no longer raises on a multi-domain
plant. (T5-1's
dependency moved to T3-6, the multi-port wiring half of the original T3-5, when
Amendment 1 split the task.)

## Test suite composition (868 tests on `main`)

| File | Tests | File | Tests |
|---|---|---|---|
| `test_api.py` | 7 | `test_port_name_guard.py` | 62 |
| `test_cause_effect.py` | 26 | `test_pump.py` | 9 |
| `test_clock.py` | 13 | `test_pump_api.py` | 8 |
| `test_compressor.py` | 19 | `test_random_source_guard.py` | 26 |
| `test_control_valve.py` | 103 | `test_reference_plants.py` | 20 |
| `test_engine.py` | 13 | `test_registry.py` | 17 |
| `test_engine_solver.py` | 17 | `test_rng.py` | 10 |
| `test_equipment_contract.py` | 76 | `test_scheduler.py` | 22 |
| `test_gas_inventory.py` | 34 | `test_scheduler_ownership.py` | 8 |
| `test_golden_regression.py` | 15 | `test_session_isolation.py` | 6 |
| `test_inventory_coupling.py` | 26 | `test_sessions.py` | 14 |
| `test_network_solver.py` | 36 | `test_snapshot.py` | 16 |
| `test_plant_config_validation.py` | 13 | `test_solver_diagnostics.py` | 14 |
| `test_plant_domains.py` | 23 | `test_topology.py` | 59 |
| `test_plant_loader.py` | 27 | `test_typed_ports.py` | 52 |
| `test_plant_ports.py` | 33 | `test_vessel.py` | 27 |
| `test_plant_yaml.py` | 17 | | |

`test_equipment_contract.py` and `test_registry.py` discover device classes
dynamically, so a new `Equipment` subclass is swept into the contract tests
automatically — adding one raises the total by more than the tests you wrote.
The table above is regenerated from `pytest --collect-only`, not maintained by
hand, because it had drifted twice before T4-5.

Recent movement: T4-4 took `main` to 486; T5-2 added `test_inventory_coupling.py`
(26) and grew `test_vessel.py`, `test_topology.py` and `test_engine_solver.py`,
taking it to 541; T4-5 added `test_cause_effect.py` (26), taking it to 567; T7-1
added `test_control_valve.py` (103) and, because the contract and registry tests
sweep in the new device class, grew `test_equipment_contract.py` and
`test_registry.py`, taking it to 681; T5-3 added `test_gas_inventory.py` (34),
taking it to 715; T2-5 added `test_scheduler.py` (22), taking it to 738; and
T2-6 added `test_scheduler_ownership.py` (8) and grew `test_sessions.py`,
taking it to 753; and T3-7 added `test_typed_ports.py` (52) and
`test_port_name_guard.py` (62) and split one schema test in `test_plant_ports.py`
into two, taking it to **868**.

T3-7 raised the total by exactly the tests it wrote. Changing `Port` did **not**
move the dynamic sweeps, because T3-7 added no `Equipment` subclass — the
sweeps grow when a class is registered, not when an existing class's contract
widens. Regenerate the table from `pytest --collect-only`; do not adjust it by
hand.

**On `feature/coupling-aggregation` the total is 971**, and the table above is
still `main`'s. T5-6 writes 21 tests of its own in
`test_coupling_aggregation.py` and moves two existing files by one each, but
its eight test doubles — a configurable-nozzle `Separator` and its subclasses,
plus one deliberately unrecognised branch device — are registered `Equipment`
subclasses, so the dynamic sweeps add 72 contract tests and 8 registry tests
on top. That is the sweep behaving as designed, not 80 new assertions about
coupling. Regenerate the table when T5-6 merges.
