# ADR 0001 — Flow-domain separation and the T3-4 re-scope

| | |
|---|---|
| **Status** | Accepted 19 September 2026, **amended 19 September 2026 by [Amendment 1](#amendment-1--the-connection-model-what-ports-means-and-where-an-inventory-device-lives)**, which corrects D7, D8 and D9 and splits T3-5. Read Section 3 together with the amendment: where the two disagree, the amendment wins. Applied to `docs/BUILD_PLAN.html`, the live build-plan artifact, `docs/BUILD_PLAN_STATUS.json` and `CLAUDE.md`. No code, schema, loader or solver change has been made. `docs/PROJECT_STATE.md` has **not** been refreshed and still describes T3-4 as undecided and T4-4 and T5-1 as startable. |
| **Date** | 19 September 2026 |
| **Decides for** | T3-4, T3-5 (new), T3-6 (new, Amendment 1), T4-4 ordering, T5-1, T5-2, T5-5 (new) |
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

**Amended.** The finding is true of a multi-port *branch* device and was
over-generalised to the separator. A `Branch` needs an explicit `from_port` /
`to_port` **pair**; a named-port map says only which node each port attaches to
and does not name pairs, so it cannot build branches on its own. And under D7
the separator is not a branch device at all, so C2 is not where it lives. See
[Amendment 1](#amendment-1--the-connection-model-what-ports-means-and-where-an-inventory-device-lives),
A1 and A4. The conclusion — no C1 or C2 rewrite — survives; the reasoning does
not.

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

**Amendment 1 makes the limitation enforced rather than latent.** C2 accepts a
device on two branches, so the limitation is reachable from config the moment
named ports exist. A6 turns it into a load-time rejection: at most one hydraulic
path per device until a task explicitly widens C1.

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
>
> **Amended by A4.** D7 is upheld and made concrete: a device in no hydraulic
> path is in **no `Topology` at all**, because `Topology.devices` is derived
> from `Topology.branches` and C2 has no device-only registration. Its identity
> lives on `Plant`. D7 stated what the vessel is not; A4 states where it is.

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
> **D8 amended — conclusion upheld, premise replaced.** C1 and C2 are still not
> modified, but not because "multi-port support already exists". It is because
> an inventory device lives **outside** C2 (A4) and its port-to-node binding is
> made with `Port.connect()`, which is existing C1 API. See A9, A10, A12.
>
> **D9 amended — a named-port map is not sufficient wiring.** It says which node
> each port attaches to and nothing about which two ports form a branch. C3 needs
> a **second, separate** declaration for hydraulic paths (A2), and the
> `oneOf` this ADR assumed cannot be used, because the C3 validator silently
> ignores `oneOf` (finding A-E3). See A2 and A11.
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

> **Superseded by [Amendment 1](#amendment-1--the-connection-model-what-ports-means-and-where-an-inventory-device-lives), A11.**
> T3-5 is split. The task block below — which folds named-port wiring into T3-5
> and describes it as a schema `oneOf` — is retained only as the record of what
> was decided on the first pass. **Do not implement it.** The current
> specification of T3-5 and of the new T3-6 is A11, and the applied build-plan
> text is in `docs/BUILD_PLAN.html`.

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
| Widening `characteristic(flow)` with a port or branch argument | Unscheduled | Not needed under D7. Raise a new ADR if a genuine multi-branch branch device appears. **A6 now enforces the limitation at load** — more than one hydraulic path on one device is rejected — so the widening task is also the task that deletes that check. |
| Requiring a coupling device's attachment node to be a boundary node | **T5-2** | It will be one in every plant T5-2 builds, because that is the node the vessel's integrated pressure is written to (D11). Enforcing it in T3-6 would forbid an inventory device attached mid-line before anything has tried one. See A7. |
| Extending the C3 validator with `oneOf` | Unscheduled, and discouraged | A combinator failure cannot name which alternative the author meant, and C3's whole error-quality bet is naming the offending path. A2 expresses the alternation in the loader instead. |
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

## Amendment 1 — the connection model: what `ports` means, and where an inventory device lives

| | |
|---|---|
| **Status** | Accepted 19 September 2026. Amends D7, D8 and D9 of this record. Applied to `docs/BUILD_PLAN.html`, `docs/BUILD_PLAN_STATUS.json` and `docs/PROJECT_STATE.md`. No code, schema or loader change has been made. |
| **Raised by** | The T3-5 implementation session, which stopped before editing anything. It was right to stop. |
| **Verified against** | `main` at `b081ed8`, worktree `../plant-simulator-flow-domains` clean |
| **Amends** | D7 (upheld, made concrete), D8 (conclusion upheld, premise replaced), D9 (corrected), Section 7.2 (superseded) |

### A.0 The contradiction, confirmed

Three contradictions, not two. All three are confirmed.

**C-1 — a named-port map cannot build a branch.** `ports: {liquid_in: N-101,
liquid_out: N-102, vapor_out: N-201}` states which node each port attaches to.
`Branch.__init__` needs a **pair** — `from_port` and `to_port` — and
`_resolve_port` falls back to `_sole_port` only when direction alone is
unambiguous, which for one inlet and two outlets it is not. Nothing in the map
says whether the branch is `liquid_in → liquid_out` or `liquid_in → vapor_out`.
Every way of guessing (declaration order, inlet×outlet cross product, parsing
`liquid_*` / `vapor_*`, a "primary" outlet) is forbidden, and correctly so.
Finding 2.3 was true of a multi-port *branch* device and was over-generalised.

**C-2 — "a port wired across domains" is a category error.** A port is wired to
exactly one node, so it sits in exactly one domain; a *port* cannot span
domains. The check named in the first-pass T3-5 note is therefore either vacuous
or, read as "a device whose ports span domains", forbids precisely the separator
that D7 requires. It is withdrawn and replaced by A5.

**C-3 — new, and not previously recorded: C3's validator cannot express a
`oneOf`.** `app/plant/validate.py` walks a deliberate subset of JSON Schema —
`type`, `enum`, `properties`, `required`, `additionalProperties: false`,
`items`, `minItems`, `minLength`, `minimum`. `oneOf` is **silently ignored**, as
are `additionalProperties` given as a subschema and `patternProperties`
(evidence A-E3). D9's prescription "the schema becomes a `oneOf` over it and the
new `ports` form" would therefore have produced a schema clause nothing
enforces: validation theatre in the contract whose entire value is being
checkable.

### A.1 — `ports` is attachment, and only attachment

> **A1.** A C3 `ports` entry binds **one device port to one node**. It declares
> attachment. It declares **nothing** about flow, about pairing, or about
> whether the device is in the hydraulic solve at all.

An attachment is `Port.connect(node)` — the binding C1 already models, and the
only thing C1 lets a port hold. The map is a map of exactly that.

### A.2 — hydraulic paths are declared separately and explicitly

> **A2.** A C3 `paths` entry names an ordered pair of **this device's port
> names** — `{from: <inlet port>, to: <outlet port>}` — and becomes exactly one
> C2 `Branch`. One path, one branch, no inference, ever.

`ports` and `paths` are the two concepts of Option C, kept apart because they
answer different questions: *where is this port* and *what flows through this
device*. A device may have ports and no paths (A4), or ports and one path. It
may never have a path whose ports are not in its `ports` map.

**In the `ports` form, `paths` is required, including when it is empty.** An
empty list is how a config says "this device is not in any hydraulic solve", and
that statement must be explicit, because the alternative is the loader inferring
a branch for a device the author meant as a coupler. `paths: []` is the shape of
that sentence.

**`node_in` / `node_out` is sugar, and is defined by rewriting.** An item
carrying `node_in` / `node_out` means exactly:

```
ports: {<the sole inlet port>: node_in, <the sole outlet port>: node_out}
paths: [{from: <the sole inlet port>, to: <the sole outlet port>}]
```

There is one connection model, not two. The sugar form is unambiguous by
construction: a device without exactly one inlet and one outlet cannot use it,
and is rejected today by `_sole_port` with a message naming the item. That is the
only place inference exists, and it exists because there is nothing to infer.

### A.3 — the alternation is enforced in the loader, not in the schema

> **A3.** The schema declares `node_in`, `node_out`, `ports` and `paths` as
> **optional** properties with their own types, and the loader enforces that an
> item carries exactly one wiring form.

Forced by C-3: `oneOf` is ignored by the validator, and adding it would give an
error that cannot name which alternative the author meant. The loader's errors
name `$.equipment[i]`, which is C3's stated bar.

The validator does gain two small, generic additions, because without them a
`ports` map's values are wholly unchecked: **`additionalProperties` as a
subschema** and **`minProperties`**. Both are ordinary extensions of the
existing generic walk and both produce path-named errors. `oneOf` is **not**
added (Section 9).

### A.4 — a device with no hydraulic path lives on `Plant`, not in a `Topology`

> **A4.** A device whose `paths` is empty is a **coupling device**. It is in no
> `Topology`, appears in no `Topology.devices`, and `NetworkSolver` never sees
> it. Its one identity lives in `Plant.devices`, and its ports are bound to
> their nodes with `Port.connect()`.

This is the concrete form of D7, and it is forced by the code rather than
chosen: `Topology.devices` is a property derived from `Topology.branches`, and
C2 has **no** device-only registration (evidence A-E1). A device in no branch
cannot be in a topology at all. Either C2 grows a device collection — a spine
change to a frozen contract, for a device that by D7 is not part of the
hydraulic graph — or the coupling device lives one layer up. It lives one layer
up.

One object, one slow-state identity, one row in the snapshot. A separator
touching two domains is **not** duplicated, and `Plant.topologies` holds no
reference to it.

### A.5 — the cross-domain invariant, stated precisely

> **A5.** Both nodes of a hydraulic **path** must be in the same domain. That is
> the whole of the cross-domain rule.
>
> A **device** whose *ports* attach to nodes in different domains is **valid**,
> and is the mechanism by which domains are coupled. There is no device-level
> port-domain rule, and "a port wired across domains" is not a thing that can
> happen.

D5 is unchanged and is what this serves: no mass-balance node and no solver
invocation mixes domains. A coupling device introduces no mass-balance row, so
it cannot violate D5.

### A.6 — at most one hydraulic path per device, enforced at load

> **A6.** A device declaring more than one `paths` entry is **rejected at
> load**, with an error saying why: `characteristic(flow)` is device-wide, so a
> second path would publish the same curve — in the same flow unit — into a
> second branch.

C2 accepts a device on two branches, and accepts two branches sharing one port
when they meet at the same node (evidence A-E2), so this is reachable from
config the moment `paths` exists. Finding 2.5 recorded the limitation; leaving
it latent would make it exactly the class of silent wrongness this ADR was
written to end. It is now a readable load error instead.

`paths` stays an **array**, because the shape is right and the restriction is
temporary. The task that widens `characteristic(flow)` is the task that deletes
this check; nothing before then needs more than one path — the pump, compressor
and control valve have one each, the vessel has none, and T6-3's heat exchanger
is a single-leg UA duty model with no utility-side hydraulics.

### A.7 — what a coupling device's attachment node is expected to be

Not enforced, stated so it is not rediscovered. Under D11 a vessel's integrated
pressure supplies a **boundary condition** to the domain it originates, so in
every plant T5-2 builds, a coupling device's attachment node is a **boundary
node** of that domain. T3-6 does not enforce it: enforcing it would forbid an
inventory device attached mid-line before anything has tried one, and the
enforcement belongs with the coupling, which is T5-2's.

A consequence worth having in advance: in T5-5, the liquid domain is
`N-101 →[P-101]→ N-102` with **N-102 a boundary**, and the gas domain is
`N-201 →[K-101]→ N-202` with **N-201 a boundary**. V-101 attaches to N-102 and
N-201 and couples them. Each domain is a complete square system on its own.

### A.8 — a domain may be legal with no branches in it

> **A8.** A domain that contains nodes but no branches loads. It must still have
> a boundary node. Nothing hands it to `NetworkSolver`; what gets solved is
> T5-2's decision.

The realistic case is a T5-1 or T5-2 fixture: a vessel venting to a gas boundary
with no gas equipment built yet. Rejecting it would forbid the smallest honest
vessel test. The boundary requirement is kept because it is right and because it
is where the vessel pressure will be written.

### A.9 — C1's role

Unchanged, and no file under `app/equipment/` is edited.

`Port.connect(node)` is the attachment primitive, and the loader calling it
directly is the same act `Branch.__init__` performs today. The `Port` docstring's
"the node the topology attached it to" is to be read as *the plant-building
layer* — for a coupling device that is the loader, because by A4 there is no
topology to do it. That reading is recorded here rather than edited into
`app/equipment/base.py`, which is a spine file and is not opened by this work.

`characteristic(flow)` is not widened (D10 stands). `Equipment` gains no domain
property (2.6 stands). `Port` gains no `domain` field (Section 9 stands).

### A.10 — C2's role, and whether it needs a new abstraction

> **A10.** C2 is **not changed**, and **no** `PortBinding`, `Attachment` or
> `Connection` abstraction is created.

`Branch` remains the only connection type in C2, and it remains strictly
hydraulic: two nodes, one device, one flow, one pair of ports. Everything C2
holds is something the solver reads.

The attachment needed no new type because it already had one: the binding **is**
`Port.node`, and the lookup is `Plant.devices` plus `Plant.nodes`. A new class
would have held one pointer that `Port` already holds, in a contract that by A4
does not own the object.

`Topology.unconnected_ports()` is kept unchanged and stays the right primitive
for a topology — but it is **no longer the loader's dangling-port check**,
because it walks `Topology.devices` and therefore cannot see a coupling device
at all. A9 of the implementation contract moves that check to `Plant.devices`.

### A.11 — T3-5 is split

T3-5 as written carries domain declaration, named-port attachment, hydraulic
paths, a `Plant` restructure, a validator extension and a `to_config()` rewrite.
That is two tasks, and only the first is on the critical path.

> **A11.** **T3-5** keeps flow-domain declaration and partitioning. **T3-6**, new
> in M3, takes C3 multi-port wiring — `ports`, `paths`, `Plant.devices` and the
> attachment semantics.

- **T3-4** and **T4-4** depend on **T3-5** only. Neither needs a named port, so
  Checkpoint B is unblocked by the smaller task — the split shortens the
  critical path rather than lengthening it.
- **T5-1** moves from T3-5 to **T3-6**, which is what it actually needs.
- The graph stays acyclic: `T3-3 → T3-5 → T3-6 → T5-1 → T5-2 → T5-5`, with
  `T3-5 → T3-4 → T5-5` and `T3-5 → T4-4 → T5-2`.

**T3-6 has no circular dependency on T5-1.** `load_plant()` already takes a
`device_types` override, so T3-6 tests named-port wiring against a stub
multi-port `Equipment` subclass in its own test file, exactly as
`tests/test_plant_loader.py` already does with `ManifoldDouble`. No vessel is
needed to specify or to test the wiring that the vessel will use.

### A.12 — the hold, and what T3-5 must get right

The implementation session's recommended hold **was correct to take** — under
the first-pass ADR, `Plant.to_config()` and equipment identity genuinely did
depend on an unmade decision.

**The hold is now lifted for T3-5's revised scope.** A4 fixes equipment identity
(coupling devices on `Plant`, never in a `Topology`), so nothing T3-5 builds is
at risk of rework from T3-6. Two requirements make that true, and both are in
the implementation contract: `to_config()` must be driven by a config-ordered
`Plant.nodes`, not by walking partitioned topologies; and `Plant` must be built
expecting to grow a `devices` mapping in T3-6.

### A.13 — options, and why Option C

| Option | Verdict |
|---|---|
| **A — `ports` plus explicit paths, one concept** | This *is* Option C once the vessel question is answered, and A leaves that question open: it never says what a port in no path means. Folded into C. |
| **B — branchless attachments only, no path declaration** | Correct for the vessel, insufficient for everything else. A pump still needs a branch, and a >2-port hydraulic device still needs a pair named. B alone cannot express a plant. |
| **C — both concepts, explicitly separated** | **Chosen.** It is the only option that answers both questions without either inferring a pair or denying that a coupling device exists. Verified against C1 and C2 rather than adopted because it was suggested: A4 and A6 are both things the code decided, and neither was in the proposal. |
| **D — revise C1/C2 more fundamentally** | Not needed. The premise of D8 was wrong, but its conclusion survives for a better reason (A4, A10). The one place the code genuinely pushed back — a device cannot exist in a topology without a branch — is answered one layer up, in the layer that owns configuration, rather than by reopening a frozen contract. |

### A.14 — what a future session must be able to answer

- **A hydraulic branch** is a C2 `Branch`: two nodes in one domain, one device,
  one flow, one named port pair. It comes from exactly one C3 `paths` entry.
  It is the only thing `NetworkSolver` sees.
- **A port attachment** is `Port.node`, set from one C3 `ports` entry. It says
  where a port is and nothing else.
- **A domain** is a label on a node (`nodes[].domain`, defaulting to
  `"default"`). Branches and topologies derive theirs from their nodes.
  Equipment never has one.
- **An inventory device** lives in `Plant.devices` and in no `Topology`.
- **Domains are coupled** by one device attaching ports in each, and by that
  device's integrated slow state supplying each domain's boundary condition
  (D11). Never by a shared flow variable, and never by a branch.
- **`NetworkSolver` receives** one `Topology` from `Plant.topologies`: one
  domain, one flow unit, one square system. Exactly as today.

### Amendment evidence

Reproducible on `main` at `b081ed8` with `PYTHONPATH=.`.

**A-E1 — C2 has no device-only registration.** `Topology.devices` is a property
computed from `Topology.branches`; there is no `add_device`. Three ports of a
separator bound with `Port.connect()` across two topologies leave
`liq.devices == {}`, `gas.devices == {}` and `liq.unconnected_ports() == ()` —
the device is bound, and the topology cannot see it. `reset()` preserves the
binding, as C1 promises.

**A-E2 — one port can be claimed by two branches.** `Branch._bind` refuses a
second claim only when the second branch attaches the port to a *different*
node. Two branches leaving the same node through one inlet port are accepted,
giving one device two branches that return the identical curve — the reachable
form of finding 2.5, and the reason for A6.

**A-E3 — the C3 validator ignores `oneOf`.** A schema whose item carries
`"oneOf": [{"required":["node_in","node_out"]}, {"required":["ports"]}]`
validates `{"tag": "P-101"}` with **no errors**. `additionalProperties` given as
a subschema and `patternProperties` are likewise ignored: `{"ports": {"a": 123}}`
validates clean against both.

---

## 12. Implementation contract for T3-5 and T3-6

Frozen by Amendment 1. An implementation session follows this; it does not
re-decide any of it. Where a rule is owned by one of the two tasks, the task is
named. **T3-5 implements only what is marked T3-5.**

### 12.1 C3 schema — exact shape

**`nodes[]` items (T3-5).** One property added, optional:

```jsonc
{
  "type": "object",
  "required": ["id", "boundary", "pressure"],
  "additionalProperties": false,
  "properties": {
    "id":       {"type": "string", "minLength": 1},
    "boundary": {"type": "boolean"},
    "pressure": {"type": "number"},
    "domain":   {"type": "string", "minLength": 1}   // NEW, optional
  }
}
```

**`equipment[]` items (T3-6).** `node_in` / `node_out` leave `required` and two
properties are added. The item schema stays `additionalProperties: false`:

```jsonc
{
  "type": "object",
  "required": ["tag", "type", "design"],            // node_in/node_out removed
  "additionalProperties": false,
  "properties": {
    "tag":  {"type": "string", "minLength": 1},
    "type": {"type": "string", "enum": [ ...unchanged... ]},

    "node_in":  {"type": "string", "minLength": 1},  // now optional
    "node_out": {"type": "string", "minLength": 1},  // now optional

    "ports": {                                        // NEW
      "type": "object",
      "minProperties": 1,
      "additionalProperties": {"type": "string", "minLength": 1}
    },
    "paths": {                                        // NEW
      "type": "array",
      "items": {
        "type": "object",
        "required": ["from", "to"],
        "additionalProperties": false,
        "properties": {
          "from": {"type": "string", "minLength": 1},
          "to":   {"type": "string", "minLength": 1}
        }
      }
    },

    "design": {"type": "object"}
  }
}
```

`node_in` / `node_out` becoming optional is a deliberate schema *weakening*,
compensated in the loader by A3: every wiring-form rule is a loader error naming
`$.equipment[i]`. **No `oneOf`** — the validator ignores it (A-E3).

**`app/plant/validate.py` (T3-6) gains exactly two generic features**, because
without them the `ports` map's values are unchecked:

- `additionalProperties` given as a **subschema** — apply it to every property
  not named in `properties`. `additionalProperties: false` keeps its current
  meaning.
- `minProperties` on an object.

Nothing else. No `oneOf`, no `patternProperties`. Both additions are generic
walk extensions and both report `$.path`-style errors.

### 12.2 Every new field, exactly

| Field | Owner | Meaning |
|---|---|---|
| `nodes[].domain` | T3-5 | The flow domain this node's mass balance is written in. Free string; `"liquid"` and `"gas"` are conventional, not enumerated. Absent means `DEFAULT_DOMAIN`. |
| `equipment[].ports` | T3-6 | Attachment map, **port name → node id**. Nothing more (A1). Keys are this device's port names; values are node ids. |
| `equipment[].paths` | T3-6 | Hydraulic paths. Each `{from, to}` names two of this device's **ports** and becomes one `Branch` (A2). Required whenever `ports` is used; `[]` is the explicit "coupling device, not in any solve". |
| `equipment[].node_in/out` | unchanged | Sugar for the one-inlet-one-outlet case; defined by the rewrite in A2. |

`DEFAULT_DOMAIN = "default"` — a module constant in `app/plant/loader.py`. The
literal string is part of this contract: it appears in error messages, so an
author who labelled some nodes and not others can see why the two disagree.

### 12.3 Branch construction — exact algorithm

Run per `equipment` item, in config order, after reference checks pass.

1. **Pick the form.**
   - `node_in` **and** `node_out` present, `ports` and `paths` absent → **sugar
     form**.
   - `ports` present, `node_in` and `node_out` absent → **named form**.
   - Anything else → reject `$.equipment[i]`: both forms, neither form, `paths`
     without `ports`, or only one of `node_in` / `node_out`.
2. **Sugar form** — build as today: `Branch(id=f"B-{tag}", from_node=node_in,
   to_node=node_out, device=device)` with `from_port` / `to_port` left `None`,
   so `_sole_port` resolves them. A device without exactly one inlet and one
   outlet is rejected there, unchanged, and
   `test_a_device_with_more_ports_than_the_config_can_wire_is_rejected` keeps
   passing. Exactly one branch, and one path for the purposes of every rule
   below.
3. **Named form** — in this order:
   1. Every key of `ports` must be a port of the device (`device.ports`);
      otherwise reject `$.equipment[i].ports.<key>` naming the device's real
      ports.
   2. Every port of the device must appear as a key; a missing one is rejected
      as a dangling port at `$.equipment[i].ports`, before anything is built.
   3. Every value must be a known node id; otherwise reject
      `$.equipment[i].ports.<key>`.
   4. `paths` must be present. Reject `$.equipment[i]` if it is not.
   5. `len(paths) > 1` → reject `$.equipment[i].paths` per A6.
   6. For the single path, if present: `from` and `to` must be keys of `ports`;
      `from`'s port must be an `INLET` and `to`'s an `OUTLET`; their two nodes
      must differ; their two nodes must share a domain (12.5). Each failure is
      its own error at `$.equipment[i].paths[0]`.
   7. **Branch**: `Branch(id=<12.4>, from_node=ports[path.from],
      to_node=ports[path.to], device=device, from_port=path.from,
      to_port=path.to)`, added to the topology of that domain.
   8. **Attachment**: for every port **not** named by the path — which is every
      port when `paths` is `[]` — call `port.connect(node)` directly with the
      node from `ports`. This is the only new binding route, and it is existing
      C1 API (A9).
4. **Record the device** in `Plant.devices` under its tag, whether or not it
   holds a branch.

No step consults declaration order, port names, or a notion of a primary
outlet. Rule 3.4 is what makes that possible.

### 12.4 Branch ids

- A device with exactly one path (which includes every sugar-form device):
  **`B-{tag}`**, unchanged. Every existing config keeps its branch ids, so the
  round trip, the loader tests and any future golden trace are untouched.
- More than one path is rejected today (A6). When A6 is lifted, the id is
  **`B-{tag}-{from_port}-{to_port}`** — deterministic, readable, and stable
  under reordering `paths`, which an index-based id would not be. Recorded here
  so the task that lifts A6 does not re-decide it.

### 12.5 Domain validation — exact rules (T3-5)

Resolve first: `domain_of(node) = node_config.get("domain", DEFAULT_DOMAIN)`.

1. **No inheritance.** A node without `domain` is in `DEFAULT_DOMAIN`. It does
   **not** take a domain from a neighbour, a branch, or a device. There is no
   graph-based propagation of any kind. The implementation session's reading is
   **confirmed**: with one node declaring `"gas"` and its neighbour declaring
   nothing, the branch between them is a mismatch and is rejected.
2. **Path/branch agreement.** Both nodes of a branch must be in the same domain.
   Reject at the equipment item, naming both nodes, both domains, and — when
   either side is `DEFAULT_DOMAIN` by omission — saying that it is the default
   because no `domain` was declared.
3. **Partition.** `Plant.topologies` gets one `Topology` per distinct domain.
   Every node goes into its domain's topology; every branch into the topology of
   its (single, agreed) domain.
4. **Boundary per domain.** Every domain must hold at least one boundary node.
   This replaces the single global check. The existing global "no boundary node"
   error keeps its wording for a single-domain plant.
5. **Connectedness per domain.** The existing `_components` check runs **inside
   each domain**, not across the plant. A plant with a liquid domain and a gas
   domain is no longer "two disconnected subgraphs" — that is the point. Within
   one domain, more than one component is still rejected, naming the domain.
6. **Branchless domain.** Legal (A8). Rules 4 and 5 still apply to it; a single
   node is one connected piece.
7. **No device-level port-domain rule.** A device whose ports attach across
   domains is valid (A5). Nothing checks it, now or later.

Every error is collected into the one `PlantConfigError`, as today.

### 12.6 `Plant.topologies` (T3-5)

```python
self.topologies: dict[str, Topology]   # domain -> topology
self.nodes: dict[str, Node]            # every node, flat, CONFIG ORDER
self.devices: dict[str, Equipment]     # every device, CONFIG ORDER  (T3-6)
```

- Key order is the order each domain **first appears** in `config["nodes"]`, so
  a single-domain plant has exactly one entry and a two-domain plant is listed
  the way its file reads.
- `Plant.nodes` is flat and config-ordered across all domains. It is what makes
  the round trip exact (12.8) and what lets T5-2 find a coupling device's node
  by id without knowing its domain.
- `Plant.devices` (T3-6) holds **every** device — branch devices and coupling
  devices alike. **T4-4 builds `Engine`'s equipment from `Plant.devices`, never
  from `Topology.devices`**, or a coupling device would silently never be
  integrated.

### 12.7 `Plant.topology` compatibility (T3-5)

```python
@property
def topology(self) -> Topology:
```

- Exactly one domain → returns it. Every existing caller and every existing test
  is unaffected, and this is what T4-4 wires against.
- More than one → raises `ValueError` naming the domains and pointing at
  `Plant.topologies`. Loud, at the call site, rather than silently returning one
  of them.

It is a property, not an attribute; `Plant.__init__` no longer stores
`self.topology`.

### 12.8 Round trip and canonicalization

`to_config()` is **form-preserving, not canonicalizing**. It reproduces the
config it was given, field for field, so
`test_round_trip_plant_to_config_to_plant_is_identical` keeps asserting exact
dict equality.

- **Nodes** are emitted by walking `Plant.nodes` — config order, never by
  iterating `topologies` (T3-5). `domain` is emitted **only if the config
  declared it**; a plant that never mentioned a domain round-trips without one.
- **Equipment** is emitted by walking `Plant.devices` in config order (T3-6),
  not by walking branches, or a coupling device would vanish from the output.
- Each item is emitted **in the form it was loaded in**: a sugar-form device
  emits `node_in` / `node_out`; a named-form device emits `ports` and `paths`.
  The loader records that per tag beside `_design_keys`. `ports` keys keep config
  order; `paths` keeps config order.
- Design values are still read back off the live device, unchanged.

### 12.9 Unconnected ports

- `Topology.unconnected_ports()` is **unchanged** and stays in C2.
- It is **no longer** what the loader checks (A10) — it walks
  `Topology.devices`, so it cannot see a coupling device.
- **T3-6 moves the check to `Plant.devices`:** after building, any port of any
  loaded device with `port.connected is False` is an error naming the tag, the
  port and its direction. For a sugar-form device this reports exactly what it
  reports today. For a named-form device it is unreachable, because 3.3.2
  already required every port in the map — belt and braces, and cheap.

### 12.10 C1's role

Unchanged. No file under `app/equipment/` is opened by either task.

`Port.connect()` is the attachment primitive. `characteristic(flow)` is not
widened. `Equipment` gains no domain, `Port` gains no `domain`.

### 12.11 C2's role

Unchanged. `app/plant/topology.py` is not opened by either task. `Branch` stays
strictly hydraulic; `Topology` stays the graph the solver walks and holds only
devices that are in it through a branch.

### 12.12 New C2 abstraction

**None.** No `PortBinding`, `Attachment` or `Connection` type is created — see
A10 for why one would hold nothing that `Port` does not already hold.

### 12.13 Task scope after the ruling

**T3-5 — Flow-domain declaration** (`feature/flow-domains`, Sonnet, satellite,
no lock). Depends on T3-3.

*In:* `nodes[].domain` in the schema · `DEFAULT_DOMAIN` · partitioning ·
`Plant.topologies` · `Plant.nodes` · `Plant.topology` as a raising property ·
the five domain rules in 12.5 (1–6) · `to_config()` driven by `Plant.nodes` with
`domain` emitted only when declared.

*Out:* `ports` · `paths` · `Plant.devices` · any `validate.py` change · any
equipment-schema change · anything in `app/equipment/`, `app/plant/topology.py`,
`app/engine/`.

*Files:* `config/schema/plant.schema.json`, `app/plant/loader.py`,
`tests/test_plant_domains.py`.

**T3-6 — Multi-port equipment wiring** (`feature/multi-port-wiring`, Sonnet,
satellite, no lock). Depends on T3-5.

*In:* the `equipment` schema shape in 12.1 · the two `validate.py` additions ·
form selection and every rule in 12.3 · `Plant.devices` · branch ids (12.4) ·
attachment via `Port.connect` · A6's one-path rejection · form-preserving
`to_config()` · the dangling-port check moved to `Plant.devices`.

*Out:* the vessel (tested against a stub multi-port `Equipment` subclass through
`load_plant`'s existing `device_types` override, as `ManifoldDouble` already
is) · any `app/equipment/` change · `NetworkSolver` · `Engine`.

*Files:* `config/schema/plant.schema.json`, `app/plant/validate.py`,
`app/plant/loader.py`, `tests/test_plant_ports.py`.

**Neither task** touches `NetworkSolver`. The one permissible edit to
`app/engine/network.py` remains the docstring paragraph beginning *"That
assumption is not checked here"*, which T3-5 may rewrite to point at the loader.

### 12.14 Build-plan and dependency edits

| Task | Before | After |
|---|---|---|
| T3-5 | `T3-3`; named-port wiring in scope | `T3-3`; **domains only** |
| **T3-6** *(new, M3)* | — | **`T3-5`** |
| T3-4 | `T3-3`, `T3-5` | unchanged |
| T4-4 | `T4-2`, `T2-3`, `T3-5` | unchanged; note gains **"build `Engine`'s equipment from `Plant.devices`, not `Topology.devices`"** |
| T5-1 | `T1-2`, `T3-5` | `T1-2`, **`T3-6`** |
| T5-2 | `T5-1`, `T4-4` | unchanged |
| T5-5 | `T3-4`, `T5-2` | unchanged |

Counts: M3 5 → **6**, totals 96 → **97**, startable-now unchanged at 18 (T3-6 is
Blocked on T3-5, which is startable). The graph stays acyclic.

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
