# ADR 0001 — Flow-domain separation and the T3-4 re-scope

| | |
|---|---|
| **Status** | Accepted 19 September 2026. Applied to `docs/BUILD_PLAN.html`, the live build-plan artifact, `docs/BUILD_PLAN_STATUS.json` and `CLAUDE.md`. No code, schema, loader or solver change has been made. `docs/PROJECT_STATE.md` has **not** been refreshed and still describes T3-4 as undecided and T4-4 and T5-1 as startable. |
| **Date** | 19 September 2026 |
| **Decides for** | T3-4, T3-5 (new), T4-4 ordering, T5-1, T5-2, T5-5 (new) |
| **Verified against** | `main` at `7b54ec9`, 372 tests passing, `mypy` clean |
| **Supersedes** | The two open questions recorded in the T3-3 note (multi-port C3 wiring, flow-domain metadata) |

This record is written to be read by a session with no other context. Everything
it asserts about current behaviour was checked against the code on `main`, and
the checks are reproducible from the [Evidence](#evidence) appendix.

---

## 1. Context

`docs/BUILD_PLAN.html` specifies T3-4 as the project's reference plant:

> supply header → feed pump → separator → Gas Compressor → discharge header

T3-4 has been **Blocked** on a design decision recorded as *"Flow Domain
Metadata"*. The block is real and the reasons are load-time rejections, not
judgement calls:

1. There is no vessel model. `DEVICE_TYPES` in `app/plant/loader.py` maps only
   `pump` and `compressor`, so a `vessel` entry is rejected with *"has no device
   model yet"*. The vessel is **T5-1, in M5** — T3-4 sits in M3 and would depend
   on a task two milestones later.
2. C3 cannot wire a separator. `equipment` requires exactly `node_in` and
   `node_out` under `additionalProperties: false`.
3. The train crosses phase. Liquid through P-101 is GPM; gas through K-101 is
   SCFM. `NetworkSolver`'s internal-node mass balance sums raw branch flows, and
   nothing in C2 or C3 records or enforces which quantity a branch carries.

Point 3 is the dangerous one, because it fails silently. A mixed config loads,
builds a `Topology`, and solves to a confident, converged, physically
meaningless answer.

`docs/UNITS_CONVENTION.md` already records the intended architecture:

> Where the V1 train crosses phase — liquid through P-101, gas through K-101,
> with the V-101 separator between them — those are **separate hydraulic
> problems** coupled through the vessel's inventory, not one network with a
> single flow variable.

So the direction is not an open choice. The convention is written; the
**mechanism that makes it true is missing**. This record supplies the mechanism
and re-scopes T3-4 to what it can deliver honestly.

---

## 2. Verified findings

Eight findings were checked against the code. Six confirmed as stated, one
corrected, one added.

### 2.1 Confirmed — a mixed-domain topology solves, silently and wrongly

A two-node-boundary config with a pump and a compressor in series loads and
converges. Flow is a single shared variable across both branches, so the solver
reports the same number as GPM through the pump and SCFM through the compressor.

```
converged in 4 iterations, residual 2.56e-06x tolerance
branch B-P-101  N-supply -> N-mid    P-101  flow -272.84   <- GPM
branch B-K-101  N-mid    -> N-disch  K-101  flow -272.84   <- SCFM
```

The exact figure tracks the boundary pressures chosen; the defect does not.
**This must never be an accepted operating mode.**

### 2.2 Confirmed — the liquid/gas split is already the documented direction

See the `docs/UNITS_CONVENTION.md` quotation above. The `app/engine/network.py`
module docstring says the same thing and states plainly that nothing enforces
it: *"That assumption is not checked here, and nothing else encodes it either."*

### 2.3 Corrected — C1 and C2 already support multi-port equipment

The earlier assumption that C1/C2 could not represent a multi-port separator was
too broad and is **wrong**.

- `Equipment.add_port(name, direction)` accepts any number of named ports.
- `Branch.__init__` takes explicit `from_port` / `to_port` names, and
  `_resolve_port` falls back to `_sole_port` only when direction alone is
  unambiguous.
- The `Branch` docstring states the case outright: *"A device with more — a
  vessel with a vent, an exchanger with a utility side — sits in more than one
  branch, and each branch names the two ports it claims."*

Verified by wiring a three-port separator double across two branches: both
branches accepted, `Topology.unconnected_ports()` empty, and the one device
correctly de-duplicated in `Topology.devices`.

> **The immediate multi-port configuration gap is C3, and only C3.**
> Do not propose a C1 or C2 rewrite to solve it.

### 2.4 Confirmed — C3 is the wiring gap

`config/schema/plant.schema.json` requires `tag`, `type`, `node_in`, `node_out`
and `design`, with `additionalProperties: false`. Sufficient for a two-port
device, insufficient for a genuine multi-port one. The `type` enum already names
`vessel`, `control_valve`, `heat_exchanger` and `furnace`, so the schema is
ahead of the loader by design.

### 2.5 Confirmed — `characteristic(flow)` is device-wide

C1 exposes `characteristic(flow)` with no port or branch argument. A device
participating in two branches publishes **the same curve on both**. A separator
cannot independently publish a vapour-leg and a liquid-leg hydraulic curve
through this interface.

Recorded as a known limitation. **This ADR does not widen the C1 characteristic
contract**, and under the decision below the limitation does not block anything:
the separator is not modelled as one branch device carrying both domains.

### 2.6 Corrected (this ADR's own earlier draft) — domain is not device-wide

An earlier draft proposed `Equipment.FLOW_DOMAIN` as a class attribute, one
domain per device class. **That is wrong and is withdrawn.** A separator
participates in more than one domain through different ports, so no device-wide
domain property can describe it. Section 3.2 states the rule that replaces it.

### 2.7 Confirmed — vessel pressure is slow state, not a solver result

`Node.set_pressure()` raises on a boundary node by design: *"the whole point of
a battery limit is that the plant inside it cannot push it around."* Inventory
coupling will therefore need a sanctioned route for a vessel's integrated state
to establish a boundary condition. That route does not exist and **is not built
here**; Section 3.5 freezes the rule it must obey.

### 2.8 Confirmed — `CLAUDE.md` carries a stale solver statement

Three sentences in the *"Current runtime vs. target architecture"* section say
the network solver does not exist. It has been on `main` since `708745e`, with
T4-3 diagnostics on top. `docs/PROJECT_STATE.md` is correct and current. Exact
correction in Section 10.

### 2.9 Added — no boundary pair is sane both cold and running

Not previously recorded, and it changes T3-4's **acceptance criteria**, not just
its scope.

Both device curves scale with the square of speed or load, so a stopped machine
is pure resistance. The network contains **no other resistance**: there is no
check valve, no line loss, and no control valve until T7-1. `max_flow` is a
legacy-path attribute and is not enforced by `characteristic()` at all. The
system curve is therefore a constant boundary ΔP rather than a rising curve, and
the operating point is both unbounded and extremely sensitive.

Measured on a two-pump series fixture (rated `max_flow` 1200 GPM):

| Boundary sizing | Cold (speed 0) | Running (speed 1.0) |
|---|---|---|
| Sized for running (header 180 psia) | **−2081.67 GPM** backflow | 816.50 GPM — plausible |
| Matched (header 50 psia) | 0.00 GPM — correct | **2236.07 GPM** runaway |

The same holds in gas: −324.04 SCFM cold against a 120 SCFM rating, or 331.66
SCFM runaway.

> **Consequence:** the build plan's current T3-4 criterion *"Reaches a plausible
> steady state from cold"* **cannot be satisfied by any boundary choice** with
> only the pump and compressor models. It must be deferred to T7-1, not merely
> re-scoped. Section 9 records this.

---

## 3. Decision

Ten conclusions are frozen. A later task may not quietly reverse one; it raises
a new ADR.

### 3.1 The solver stays single-domain

> **D1.** One `NetworkSolver` invocation solves exactly one compatible
> hydraulic/process domain.
>
> **D2.** A mass balance that mixes incompatible flow units (GPM with SCFM) is
> invalid and must be **rejected before solve time**, at load, where the error
> can name a config path.
>
> **D3.** `NetworkSolver` is **not changed** by this decision. It does not gain
> domain awareness, partitioning, or any conversion between gas and liquid
> volumetric flow. No such conversion exists and none is to be invented.

Domain correctness is guaranteed *before* the solver is handed anything, so the
solver's existing assumption becomes true by construction. `app/engine/network.py`
is 400 lines of settled, 50-tested Newton-Raphson at the single largest risk
point in the plan; a design requiring no edit to it is worth a great deal.

### 3.2 Domain belongs to the hydraulic connection, not the device

> **D4.** A flow domain is a property of **nodes**, and derivatively of the
> branches and topologies built from them. It is **not** a device-wide property.
> An equipment object may couple more than one domain through distinct ports and
> its own slow state.

Concretely:

| Element | Carries a domain? | Rule |
|---|---|---|
| **Node** | **Yes — authoritative.** Declared in C3, optional, defaulting to a single implicit domain. | A mass balance is written at a node, so the node is where compatibility must hold. |
| **Branch** | Derived. | Its two nodes must agree; disagreement is a load error. |
| **Topology** | Derived. | One topology per domain. Its domain is its nodes' shared domain. |
| **Port** | Only where a device spans domains. | A port's domain is the domain of the node it is wired to. The loader can compute this from config alone. |
| **Equipment** | **No.** | A separator is the counterexample that forbids it. |

The critical invariant, stated once:

> **D5.** No mass-balance node and no solver invocation mixes incompatible
> hydraulic domains.

### 3.3 The integrated train is separate topologies coupled by inventory

> **D6.** The `supply → pump → separator → compressor → discharge` train is
> built as **two hydraulic problems** — one liquid, one gas — coupled through
> the separator's integrated inventory, never as one mixed mass-balance
> topology.
>
> **D7.** The separator is **not** a branch device carrying both domains. It
> terminates the liquid domain and originates the gas domain. This is why
> finding 2.5 (`characteristic(flow)` is device-wide) does not block anything.

```
   LIQUID DOMAIN  ·  GPM                              GAS DOMAIN  ·  SCFM
   ────────────────────────────────                   ────────────────────────────────
   (N-101)──[P-101]──▶(N-102)                         (N-201)──[K-101]──▶(N-202)
   boundary            boundary                        boundary            boundary
                          │                               ▲
                          │        ┌───────────┐          │
                          └───────▶│  V-101    │──────────┘
                                   │ inventory │
                                   └───────────┘
                          slow state supplies the boundary
                          condition for each domain's solve

   two square systems · two solvers · no shared flow variable
```

### 3.4 C1 and C2 are not changed

> **D8.** Multi-port support already exists in C1 and C2 (finding 2.3). Neither
> contract is modified by this decision.
>
> **D9.** C3 gains named-port wiring for genuine multi-port devices, as an
> **additive** change that keeps `node_in` / `node_out` working unchanged.
>
> **D10.** `characteristic(flow)` is **not** widened in this work.

A useful consequence worth stating explicitly, because it is what keeps the
change small: **the loader can compute every port's domain from the config
alone**, since it knows each device's ports from `device.ports` and knows which
node each is wired to from the config. A `Port.domain` field would only add a
device-side self-declaration — a nice-to-have for catching a pump wired into a
gas node, and deliberately deferred (Section 9).

### 3.5 Vessel pressure is slow state feeding a boundary condition

> **D11.** Vessel pressure and inventory are **integrated slow equipment
> state**, advanced by `integrate(dt)` from net flow, exactly like level. That
> state **supplies a boundary condition** to a hydraulic solve.
>
> It is **not** an internal solver result written back into equipment state, and
> a hydraulic network's internal solved pressures must never masquerade as
> vessel inventory state.

This distinction must be agreed in advance, or T5-2 will read the whole design
as a violation of the C1 invariant *"a boundary pressure owned by a device is a
solver output in disguise."* It is not: an accumulator advanced by `integrate`
is exactly what C1 calls slow state, and T5-3 already specifies vessel pressure
that way (*"dP/dt from net molar flow"*).

**The API that performs the boundary update is not designed or built here.** It
is a requirement handed to T5-2 (Section 8).

### 3.6 T3-4 is re-scoped to honest single-domain fixtures

> **D12.** T3-4 delivers two single-domain reference fixtures. The full
> integrated train moves to a new M5 task after the vessel and the coupling
> exist.

Full specification in Section 7.

---

## 4. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **One mixed topology, solver partitions rows by domain** | Pushes a process-modelling concept into the numerics layer, conditionalises Jacobian assembly, and re-derives "one system per domain" *inside* the module whose entire clarity rests on solving one square system. Violates D3 for no gain. |
| **Convert SCFM ↔ GPM so one flow variable works** | Physically meaningless across a phase change, and explicitly forbidden by `docs/UNITS_CONVENTION.md`: *"No cross-phase flow conversion exists, and none should be invented."* |
| **Device-wide `Equipment.FLOW_DOMAIN`** | This ADR's own earlier draft. Withdrawn — a separator spans domains through different ports, so no device-wide property describes it (finding 2.6). |
| **Domain on `Port` in C1, validated at `Branch` construction** | Catches errors earliest, but does not solve the problem on its own: the mass balance is written at a *node*, so the node needs the label regardless. It is also a `Port.__slots__` change to a frozen contract, to duplicate a check the loader already makes earlier and with a config path in the message. Deferred as optional hardening, not rejected on merit. |
| **Extend `node_in` / `node_out` to accept arrays** | Cheaper to write, ambiguous to read: which inlet pairs with which outlet to form a branch is unstated. |
| **A separate top-level `vessels` section in C3** | Splits equipment across two keys with two owners, against C3's one-owner-per-top-level-key rule. |
| **Unblock T3-4 with no prerequisite task at all** | Genuinely possible — a single-domain fixture needs no metadata to load and solve today. Rejected because nothing would then prevent the next mixed fixture, and finding 2.1 is a silent failure. The guard is the point. |

---

## 5. Consequences

**Good**

- `NetworkSolver` is untouched at the plan's highest-risk point.
- C1 and C2 are untouched. No frozen contract is reopened.
- The C3 change is additive and backward compatible; no existing config, test or
  golden trace moves.
- T3-4 becomes deliverable now instead of waiting two milestones.
- Two recorded open questions from the T3-3 note are retired, removing an
  escalation trap that would otherwise have been sprung by whoever picked up
  T5-1.
- The `network.py` docstring paragraph beginning *"That assumption is not
  checked here"* becomes false and can be rewritten to point at the loader — a
  comment edit, no code.

**Costs**

- `Plant` grows from holding one `Topology` to holding a mapping of them.
  `Plant.topology` is kept as a single-domain convenience that raises on a
  multi-domain plant, so existing callers and tests are unaffected.
- `Plant.to_config()` must iterate every topology rather than one.
- One extra small task (T3-5) lands before T3-4 and, on the recommendation in
  Section 6, before T4-4.

---

## 6. Compatibility impact and T4-4 ordering

### 6.1 Compatibility

| Surface | Impact |
|---|---|
| Existing `.json` / `.yaml` / `.yml` configs | **None.** `domain` is optional; absent means one implicit domain. |
| `node_in` / `node_out` | **None.** Retained as sugar for two-port devices; the schema becomes a `oneOf` over it and the new `ports` form. |
| The 27 tests in `tests/test_plant_loader.py` | **None expected**, including the round-trip test — but see the ordering risk in Section 8. |
| `tests/fixtures/golden/*.json` | **None.** No numbers move; no trace is regenerated. |
| C1, C2, C4 | **None.** |
| `NetworkSolver` | **None.** |
| `Engine`, Flask routes | **None.** T3-5 is off the request path, exactly as the loader is today. |

### 6.2 T4-4 ordering — recommendation

**Recommended order: T3-5, then T4-4.**

T3-5 touches `config/schema/plant.schema.json`, `app/plant/loader.py` and a new
test file. T4-4 touches `app/engine/engine.py`, `app/equipment/compressor.py`
and `app/equipment/pump.py`. **There is no file overlap**, and `app/plant/loader.py`
is not a spine file, so T3-5 is an ordinary satellite that takes no lock.

The reason for the order is narrow and concrete: **T4-4 is the task that decides
how `Engine` holds and solves a plant's topologies.** T3-5 changes `Plant` from
one topology to a mapping of them. If T4-4 lands first, it will reasonably wire
`Engine` to one topology and one solver, and T3-5 then either has to reopen
`engine.py` — a second Checkpoint-B-class spine pass — or leave `Engine`
silently wrong at M5.

**The counterargument, stated explicitly:** T4-4 is already startable, is
Checkpoint B, is the plan's critical path, and domain crossing does not become
*operationally* necessary until M5. Holding the critical path for a
two-milestones-out concern is real schedule risk, and the build plan already
names Checkpoint B as one of its three most likely slip points.

**What makes the choice low-stakes either way:** because T3-5 preserves
`Plant.topology` as a convenience that *raises* on a multi-domain plant, T4-4
can wire against it today and move to `Plant.topologies` at T5-2, with a clean
load-time error rather than silent wrongness in the interval.

**Therefore:** land T3-5 first — it is small, satellite, takes no lock, and
retires two open questions. If Checkpoint B is under date pressure, the fastest
correct path is to **run the two concurrently**: T4-4's "freeze other merges"
rule exists to protect spine files, and T3-5 touches none of them and none of
T4-4's files. Inverting the order outright is acceptable and costs one bounded,
known revisit of `engine.py`.

**A consequence to be honest about.** Recording the T3-5 edge on T4-4 makes T4-4
*not startable* until T3-5 is Complete, by the plan's own definition (every
dependency merged to `main`). "Run concurrently" therefore means the two branches
may be developed in parallel, but T4-4 cannot be marked ready or merged first. If
that is not what is wanted, remove the edge from T4-4 and rely on the
`Plant.topology` convenience alone; the cost is the bounded `engine.py` revisit
described above. T5-1 is affected the same way, and has no such alternative.

---

## 7. Build-plan changes proposed

**Applied** to `docs/BUILD_PLAN.html` and to the live build-plan artifact on 19
September 2026, together with the T3-4 working note in the artifact's status
database. The live artifact was then synced to the repository file, so both
describe the same 96-task graph. `docs/BUILD_PLAN_STATUS.json` (Section 7.6) and
the `CLAUDE.md` correction (Section 10) are also applied. T3-4 remains
**Blocked**; T5-1 remains **Not Started**.

Three edits went beyond the drafts below, all consequences of the re-scope rather
than new decisions:

- **T7-1** gained the cold-start test (`A reference fixture reaches a plausible
  steady state from cold once the valve supplies a system curve`) and a note
  pointing here, so the criterion dropped from T3-4 is deferred, not lost
  (Section 9).
- **The hand-maintained agent-assignment table** gained a `feature/flow-domains`
  row (can start now) and `feature/vessel-model` now reads *"CP-A · 2 Oct, and
  T3-5"*, matching T5-1's new dependency.
- **T7-2, T8-3, T9-2 and T11-1** each gained an explicit `T5-5` dependency,
  because each edits `config/plants/olefins_lite.yaml`, which T5-5 now creates.
  Reachability was checked rather than assumed: none of T8-3, T9-2 or T11-1 is
  downstream of T7-2, so each needed its own edge (Section 8, risk 7).

Summary: **two new tasks, seven edited dependency edges, one re-scope.**

### 7.1 T3-4 — re-scoped

**Old intent (verbatim from `docs/BUILD_PLAN.html`):**

```js
  {id:"T3-4",m:"M3",cat:"dep",n:"Reference plant configuration",
   p:"The smallest topology that exercises real interaction: supply header, feed pump, separator, compressor, discharge header. Becomes the fixture for every later test.",
   d:["T3-3"],f:["config/plants/olefins_lite.yaml"],b:"feature/reference-plant",
   w:["Loads and solves","Design values reviewed against docs/units.md"],
   t:["Loads without warnings","Reaches a plausible steady state from cold"],
   x:"Shared config file from here on. One owner per top-level key."},
```

**Proposed replacement:**

```js
  {id:"T3-4",m:"M3",cat:"dep",n:"Single-domain reference fixtures",
   p:"Two small real plants, one liquid and one gas, each a single hydraulic domain. They are the solver's regression fixtures and the first real config files in the repository. The full mixed-phase train moves to T5-5, which needs a vessel and inventory coupling that do not exist in M3.",
   d:["T3-3","T3-5"],f:["config/plants/liquid_transfer.yaml","config/plants/gas_compression.yaml"],b:"feature/reference-plant",
   w:["Each fixture declares one domain and loads through the current C3/YAML loader","Each solves with NetworkSolver unchanged","Solved values match a closed-form expected value, so the fixture can be checked without trusting the solver","Design values reviewed against docs/UNITS_CONVENTION.md"],
   t:["Both fixtures load without warnings","Both converge, and the mass balance closes at the internal node","Solved flow and internal-node pressure match the hand-derived value","A fixture with mixed domains is rejected at load"],
   x:"First files under config/plants/. Shared config from here on, one owner per top-level key. Do NOT assert a plausible cold-start operating point: with no check valve, line resistance or control valve, no boundary pair is sane both cold and running - see ADR 0001 section 2.9. That criterion moves to T7-1."},
```

**Proposed content of the two fixtures** (validated against `main`; see
[Evidence](#evidence)). Neither is a fake train: a booster feeding a transfer
pump and a two-stage compressor with an interstage node are both ordinary real
configurations.

| | `liquid_transfer.yaml` | `gas_compression.yaml` |
|---|---|---|
| Shape | `N-101` ──[P-101]──▶ `N-102` ──[P-102]──▶ `N-103` | `N-201` ──[K-101]──▶ `N-202` ──[K-102]──▶ `N-203` |
| Reads as | booster pump into transfer pump | two-stage compression with interstage |
| Domain | `liquid` | `gas` |
| Boundaries | 50.0 and 180.0 psia | 60.0 and 480.0 psia |
| Internal node | `N-102` | `N-202` |
| Design | `speed: 1.0` on both | `load: 1.0` on both |
| **Solved flow** | **816.50 GPM** (68% of rated 1200) | **70.71 SCFM** (59% of rated 120) |
| **Solved internal pressure** | **115.00 psia** | **270.00 psia** |
| Converges in | 5 iterations | 4 iterations |

Both answers are closed-form, which is what makes them good regression fixtures
— a future session can check them without trusting the solver:

```
two identical devices in series, boundaries P_lo and P_hi:
    internal node  =  (P_lo + P_hi) / 2                       exactly
    branch flow    =  sqrt( (2*shutoff - (P_hi - P_lo)) / (2*R) )

liquid   (50 + 180)/2                         = 115.00 psia
         sqrt((2*75  - 130) / (2 * 1.5e-5))   = 816.4966 GPM
gas      (60 + 480)/2                         = 270.00 psia
         sqrt((2*220 - 420) / (2 * 0.002))    =  70.7107 SCFM
```

Each fixture has **one internal node**, so it exercises a real mass-balance row.
A single branch between two boundary nodes would not — it produces zero internal
nodes and therefore no mass balance at all.

### 7.2 T3-5 — new, the one prerequisite task

```js
  {id:"T3-5",m:"M3",cat:"dep",n:"Flow-domain declaration and multi-port wiring",
   p:"Make the single-domain rule enforceable instead of conventional. A mixed liquid/gas topology currently loads and solves to a confident, meaningless answer; this rejects it at load, where the error can name a config path. Carries C3's named-port wiring in the same schema pass, so T5-1 never has to invent it.",
   d:["T3-3"],f:["config/schema/plant.schema.json","app/plant/loader.py","tests/test_plant_domains.py"],b:"feature/flow-domains",
   w:["C3 nodes accept an optional domain; absent means one implicit domain, so every existing config stays valid","C3 equipment accepts a named ports map as a oneOf alongside node_in/node_out, which is retained unchanged","The loader partitions nodes by domain and returns Plant.topologies, keeping Plant.topology as a single-domain convenience that raises on a multi-domain plant","NetworkSolver is not modified"],
   t:["A mixed-domain config is rejected at load, naming the offending branch","A branch whose two nodes declare different domains is rejected","A domain with no boundary node is rejected","A domain that is not one connected piece is rejected","Every existing loader test and fixture still passes unchanged","Round trip plant -> config -> plant is still identical, node order preserved"],
   x:"Schema and loader only - app/plant/loader.py is not a spine file, so this is satellite work and takes no lock. Design is settled by docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md; do not redesign it. Do NOT touch C1, C2 or NetworkSolver. Retires both open questions recorded in the T3-3 note."},
```

**Ownership, stated precisely:**

- **Schema/config work (this task owns it):** `nodes[].domain`; the
  `equipment[]` `oneOf`; the `ports` map form; the loader's partitioning and its
  four new rejections; `Plant.topologies`.
- **Solver work (this task owns none of it):** `NetworkSolver` is not opened.
  The only permissible edit to `app/engine/network.py` is rewriting the now-false
  docstring paragraph beginning *"That assumption is not checked here"* to point
  at the loader — a comment, and optional.
- **Not in scope:** any C1 or C2 edit; a `Port.domain` field; vessel semantics;
  engine wiring.

### 7.3 T5-1 — what it receives already settled

No re-scope. **One dependency edge added, and the task note gains three
sentences**, so a Sonnet agent is not forced to invent architecture:

```js
   d:["T1-2","T3-5"],
```

> T5-1 receives already settled, and must not redesign: C3 named-port wiring
> (T3-5), flow-domain semantics (T3-5), and solver orchestration (unchanged —
> one solver per domain). The vessel is an **inventory device**, not a branch
> device carrying both phases; it terminates one domain and originates another.
> Its pressure and level are slow state integrated by `integrate(dt)`. T5-1
> does **not** build the boundary-update mechanism — that is T5-2 — and does
> **not** widen `characteristic(flow)`. If any of those appear necessary, stop
> and escalate against ADR 0001.

### 7.4 T5-2 — scope sharpened, and a model raise

No new dependencies. Its note gains the D11 requirement:

> T5-2 owns the sanctioned boundary-condition update: solve each domain against
> fixed boundary conditions, integrate vessel inventory from the resulting
> flows, then write the next step's boundary conditions from that slow state.
> `Node.set_pressure()` refuses on a boundary by design, so this needs an
> explicit new route in C2 — a spine change belonging to this task. Vessel
> pressure is integrated slow state supplying a boundary condition; internal
> solver pressures must never be written back as vessel inventory state. See
> ADR 0001 section 3.5.

**Model: Opus, unchanged.** T5-2 is `cat:"core"`, and the plan's `agentOf()`
defaults core tasks to Opus, so no assignment edit is needed. An earlier draft of
this record said the model was being raised from Sonnet; that was wrong. The task
does now carry a C2 spine change and the coupling-stability risk the build plan
already names, which is why Opus is right.

### 7.5 T5-5 — new, the full integrated reference plant

```js
  {id:"T5-5",m:"M5",cat:"dep",n:"Integrated reference plant",
   p:"The full train T3-4 originally promised: supply header, feed pump, separator, Gas Compressor, discharge header. Built as two hydraulic domains coupled through vessel inventory, never as one mixed mass-balance topology. Becomes the reference fixture for every later milestone.",
   d:["T3-4","T5-2"],f:["config/plants/olefins_lite.yaml"],b:"feature/integrated-plant",
   w:["Liquid and gas sides are separate topologies coupled through V-101 inventory","Loads and solves each domain","Design values reviewed against docs/UNITS_CONVENTION.md"],
   t:["Both domains converge","Mass balance closes across the separator over a long run","No domain mixes flow units"],
   x:"Inherits the parked feature/reference-plant branch history if useful. Depends on T5-2, which transitively carries T5-1 and T4-4 - do not add those edges directly."},
```

Dependencies are deliberately minimal: `T5-2` transitively carries `T5-1` and
`T4-4`, and `T3-4` carries `T3-5`. Adding them directly would over-constrain the
graph without evidence.

### 7.6 Exact `docs/BUILD_PLAN_STATUS.json` edits

```jsonc
// 1. milestones.M3.total:  4 -> 5
// 2. milestones.M5.total:  4 -> 5
// 3. totals.tasks:        94 -> 96
// 4. totals.startable_now: 17 -> 18   (T3-5 is startable; T3-4 leaves the count
//                                      as Blocked-but-now-unblockable)

// 5. REPLACE the T3-4 entry's name/files/note; it stays Blocked, blocked_by changes:
{
  "id": "T3-4", "milestone": "M3", "name": "Single-domain reference fixtures",
  "category": "dep", "branch": "feature/reference-plant",
  "depends_on": ["T3-3", "T3-5"],
  "files": ["config/plants/liquid_transfer.yaml", "config/plants/gas_compression.yaml"],
  "status": "Blocked", "status_key": "blocked",
  "blocked_by": ["T3-5"],
  "note": "Re-scoped by docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md from the full mixed-phase train to two single-domain fixtures; the full train is now T5-5. Blocked only on T3-5 (flow-domain declaration), not on a design decision - the design is settled. Fixtures validated against main at 7b54ec9: liquid_transfer (N-101 50 psia -> P-101 -> N-102 -> P-102 -> N-103 180 psia, speed 1.0) solves to 816.50 GPM with N-102 at 115.00 psia in 5 iterations; gas_compression (N-201 60 psia -> K-101 -> N-202 -> K-102 -> N-203 480 psia, load 1.0) solves to 70.71 SCFM with N-202 at 270.00 psia in 4 iterations. Both answers are closed-form and hand-checkable. The old criterion 'reaches a plausible steady state from cold' is DROPPED and moved to T7-1: with no check valve, line resistance or control valve, no boundary pair is sane both cold and running - see ADR 0001 section 2.9."
}

// 6. INSERT a new T3-5 entry after T3-4:
{
  "id": "T3-5", "milestone": "M3",
  "name": "Flow-domain declaration and multi-port wiring",
  "category": "dep", "branch": "feature/flow-domains",
  "depends_on": ["T3-3"],
  "files": ["config/schema/plant.schema.json", "app/plant/loader.py", "tests/test_plant_domains.py"],
  "status": "Not Started", "status_key": "todo", "startable": true,
  "note": "Sonnet. Design settled by docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md - implement it, do not redesign it. Satellite: app/plant/loader.py is not a spine file and this takes no lock. Additive C3 only: optional nodes[].domain, and a named ports map as a oneOf alongside node_in/node_out which is retained unchanged. The loader partitions by domain into Plant.topologies, keeps Plant.topology as a single-domain convenience that raises on a multi-domain plant, and gains four rejections: branch nodes disagreeing on domain, a domain with no boundary, a domain that is not one connected piece (today's _components check, moved inside the partition), and a port wired across domains. Do NOT modify C1, C2 or NetworkSolver. Watch the round-trip test at tests/test_plant_loader.py:313 - it asserts exact dict equality, so partitioning must preserve config node order. Retires both open questions in the T3-3 note."
}

// 7. T4-4 depends_on:  ["T4-2","T2-3"] -> ["T4-2","T2-3","T3-5"]
//    and append to its note: "Wire Engine against Plant.topology (the
//    single-domain convenience) - one solver per domain arrives with T5-2.
//    See ADR 0001 section 6.2 for the ordering argument and its counterargument."

// 8. T5-1 depends_on:  ["T1-2"] -> ["T1-2","T3-5"]
//    and add the note text from section 7.3 above.

// 9. T5-2 note gains the text from section 7.4. Model stays Opus (core default).

// 10. INSERT a new T5-5 entry:
{
  "id": "T5-5", "milestone": "M5", "name": "Integrated reference plant",
  "category": "dep", "branch": "feature/integrated-plant",
  "depends_on": ["T3-4", "T5-2"],
  "files": ["config/plants/olefins_lite.yaml"],
  "status": "Not Started", "status_key": "todo", "startable": false,
  "blocked_by": ["T3-4", "T5-2"],
  "note": "The full supply -> pump -> separator -> compressor -> discharge train, split out of the original T3-4 by docs/ADR_0001_FLOW_DOMAIN_SEPARATION.md. Two hydraulic domains coupled through V-101 inventory, never one mixed mass-balance topology. T5-2 transitively carries T5-1 and T4-4; T3-4 carries T3-5. Do not add those edges directly."
}
```

### 7.7 Dependency changes, collected

| Task | Before | After |
|---|---|---|
| T3-4 | `T3-3` | `T3-3`, **`T3-5`** |
| **T3-5** *(new)* | — | `T3-3` |
| T4-4 | `T4-2`, `T2-3` | `T4-2`, `T2-3`, **`T3-5`** |
| T5-1 | `T1-2` | `T1-2`, **`T3-5`** |
| T5-2 | `T5-1`, `T4-4` | unchanged |
| **T5-5** *(new)* | — | `T3-4`, `T5-2` |
| T7-2 | `T7-1`, `T4-4` | `T7-1`, `T4-4`, **`T5-5`** |
| T8-3 | `T3-1`, `T8-2`, `T1-5` | `T3-1`, `T8-2`, `T1-5`, **`T5-5`** |
| T9-2 | `T3-1`, `T9-1` | `T3-1`, `T9-1`, **`T5-5`** |
| T11-1 | `T3-1`, `T9-1` | `T3-1`, `T9-1`, **`T5-5`** |

---

## 8. Risks

1. **Coupling stability (highest).** Solving each domain at fixed boundary
   conditions, then integrating inventory, then updating those conditions, is
   explicit Euler on the coupling term. This is precisely the slip point the
   build plan already names for T5-2: *"level-to-hydraulics coupling oscillating
   once the solver and the integrator disagree about timescale."* This ADR does
   not solve it; it localises it to one task and names it in advance.
2. **The D11 agreement.** If the distinction in Section 3.5 is not accepted up
   front, T5-2 will correctly read the design as violating the C1 invariant and
   stop. This is the single most important thing to settle before M5.
3. **Round-trip node ordering.** `tests/test_plant_loader.py:313` asserts
   `first.to_config() == config` — exact dict equality over a list of nodes, so
   **node order matters**. Partitioning by domain and reassembling will reorder
   nodes unless the loader deliberately preserves config order. Named in the
   T3-5 task note.
4. **Finding 2.5 stays latent.** One curve per device across every branch it
   occupies. Harmless under this decision; live again the moment anything wants
   a genuine multi-branch branch device.
5. **The train is process-odd.** `supply → pump → separator → compressor →
   discharge` has a pump feeding liquid to a separator whose vapour then feeds a
   compressor, with no stated vapour source and no liquid draw. Fine as a
   mechanism exercise, which is the project's stated bar — but T5-5 should
   confirm the intent before writing design values a later reader mistakes for
   process guidance.
6. **Inherited, not worsened.** `Equipment.reset()` still restores the state
   captured at the end of `__init__`, so a reset device returns to class
   defaults rather than its configured design — the open question recorded at
   T3-3. T3-5 sets design attributes by the same route and inherits it.
7. **Shared-config ownership after the re-scope — resolved.** T7-2, T8-3, T9-2
   and T11-1 each list `config/plants/olefins_lite.yaml` in their files. Before
   this ADR the file was created by T3-4 in M3; it is now created by **T5-5** in
   M5, and none of the four depended on it — the ordering held only through
   milestone dates, which is too implicit for a plan meant to drive agents. Each
   now carries an explicit `T5-5` dependency. T7-2 is not upstream of the other
   three, so each needed its own edge rather than inheriting one. The cost is
   that T9-2 and T11-1, which previously waited only on the pure-algorithm
   T9-1, now also wait on the integrated plant.

---

## 9. Deferred work

Recorded so a later session finds it rather than rediscovering it.

| Deferred | To | Why |
|---|---|---|
| The boundary-condition update API | **T5-2** | Needs the vessel to exist. D11 fixes the rule it must obey. |
| `Port.domain` self-declaration in C1 | Optional hardening, unscheduled | The loader can compute port domains from config alone (D-note in 3.4). Would only add device-side validation, e.g. catching a pump wired into a gas node. |
| Widening `characteristic(flow)` with a port or branch argument | Unscheduled | Not needed under D7. Raise a new ADR if a genuine multi-branch branch device appears. |
| **"Reaches a plausible steady state from cold"** | **T7-1** (control valve) | Finding 2.9: unachievable with no check valve, line resistance or control valve. The network's only resistance is the machine's own. |
| A parallel-pump fixture | T4-5 | The cause-and-effect suite already specifies *"two parallel pumps raise flow by less than 2x"*. Measured: parallel boosters at 516.4 GPM each feeding a transfer pump at 1032.8 GPM — mass balance exact, but close to the 1200 rating and over it at some boundary choices, so it needs sizing care. |
| Cross-checking tags in `limits` / `controllers` / `interlocks` | Unscheduled | Recorded at T3-3, unaffected by this ADR. |

---

## 10. `CLAUDE.md` correction

**Applied** on 19 September 2026 as a separate, minimal docs change, not mixed
into T3-5 or any architectural prose. All three edits below were made; the third
was also reflowed to the file's 80-column wrapping.

All three edits are in the *"Current runtime vs. target architecture"* section.

**Edit 1 — the stale statement itself** (`CLAUDE.md:153-154`).

Current:

```markdown
**What does not exist yet:** the plant-wide pressure-flow **network solver**
(T4-2, milestone M4) and its wiring into the engine (T4-4). Until T4-4 lands:
```

Proposed:

```markdown
**What exists but is not yet on the request path:** the plant-wide pressure-flow
**network solver** (`app/engine/network.py`, T4-2 and T4-3) — merged and tested,
but nothing calls it. **What does not exist yet:** its wiring into the engine
(T4-4). Until T4-4 lands:
```

The three bullets that follow remain true and are not touched.

**Edit 2 — a now-wrong directive** (`CLAUDE.md:170`). This one matters most: it
currently instructs sessions not to write code implying the solver exists, when
it does.

Current: `**Do not write documentation, comments, or code that implies the network solver,
controllers, alarms, envelopes, or scoring already exist.**`

Proposed: `**Do not write documentation, comments, or code that implies controllers,
alarms, envelopes, or scoring already exist.**`

**Edit 3 — optional, one line** (`CLAUDE.md:265`). The `app/engine/` rule cites
`network.py` (T4-2) as something the build plan *intends*; it has landed. Move
it into the preceding "that is how it landed" clause alongside `rng.py`.

---

## 11. Explicit non-goals

This ADR does **not**, and the work it proposes must not:

- implement vessel physics, level, or pressure accumulation;
- modify C1 — no `Port.domain`, no `Equipment.FLOW_DOMAIN`, no widening of
  `characteristic(flow)`;
- modify C2 behaviour;
- modify `NetworkSolver` mathematics, or add domain awareness, partitioning or
  unit conversion to it;
- modify `Engine`, or start T4-4;
- design or build the boundary-condition update API;
- create the reference YAML fixtures — T3-4 does that, after T3-5;
- create the integrated plant — T5-5 does that;
- regenerate any golden trace;
- rewrite `CLAUDE.md` beyond the three surgical edits in Section 10.

---

## Evidence

Every number in this record is reproducible on `main` at `7b54ec9`. Probes were
run from the repository root with `PYTHONPATH=.` under the `plant-simulator`
conda environment, using only `app.plant.loader.load_plant` and
`app.engine.network.solve_network`. No repository file was modified to produce
them.

**Mixed-domain hazard (2.1)** — pump and compressor in series, boundaries 50 and
200 psia: converges in 4 iterations, residual 2.56e-06× tolerance, flow −272.84
on both branches.

**Multi-port support (2.3)** — a three-port `Equipment` subclass
(`feed` INLET, `vapour` OUTLET, `liquid` OUTLET) wired across two branches with
explicit `from_port` / `to_port`: both branches accepted,
`Topology.unconnected_ports()` returns `()`, `Topology.devices` holds one entry.

**Proposed fixtures (7.1)** — as tabulated, with closed-form checks shown.

**Cold-start impossibility (2.9)** — two-pump series fixture:

```
shipped state speed=1.0, header=180   B-P-101=   816.50  B-P-102=   816.50  N-102=115.00  iters=5
same file, pumps stopped speed=0.0    B-P-101= -2081.67  B-P-102= -2081.67  N-102=115.00  iters=4
shipped cold speed=0.0, header= 50    B-P-101=     0.00  B-P-102=     0.00  N-102= 50.00  iters=1
then started  speed=1.0, header= 50   B-P-101=  2236.07  B-P-102=  2236.07  N-102= 50.00  iters=3
```

Gas equivalent, rated `max_flow` 120 SCFM: 70.71 running, −324.04 stopped
against a running-sized discharge, 331.66 runaway against a matched one.
