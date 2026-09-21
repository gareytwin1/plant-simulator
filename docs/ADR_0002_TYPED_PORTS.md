# ADR 0002 — Typed ports and the vessel connection model

| | |
|---|---|
| **Status** | Accepted 20 September 2026, **amended by [Amendment 1](#amendment-1--a-connection-has-four-descriptors-not-three) before T3-7 implementation**, which replaces the single `service` axis with independent `purpose` and `control` descriptors. Read Section 3 together with the amendment: **where the two disagree, Amendment 1 wins.** |
| **Date** | 20 September 2026 |
| **Decides for** | T3-7 (new), T5-6 (new), T5-7 (new), T5-5 (unblocked, re-sequenced), T7-5 (new, deferred), and the M5/M8 boundary |
| **Verified against** | `main` at `f8aab59`, 753 tests passing, `mypy` clean over 24 source files |
| **Supersedes** | Nothing. It *extends* ADR 0001 Amendment 1, which settled what `ports` means structurally; this settles what a port means **semantically**. |

This record is written to be read by a session with no other context. Everything
it asserts about current behaviour was checked against the code on `main` and is
reproducible from the [Evidence](#evidence) appendix.

---

## 1. Context

T5-5 builds the integrated reference plant: supply header → feed pump →
separator → Gas Compressor → discharge header. A design investigation against
`f8aab59` was asked to confirm the train's intent before writing design values.

That investigation found the train as specified could not do what a separator
must do. The vessel fills monotonically because there is no liquid draw, so the
only way to make a conservation test pass was to stop the run before the level
clamped at 1.0 — a test-horizon trick standing in for missing physics.

A second requirement then arrived: the vessel must support vapor–liquid
interaction — several feeds, a process vapor outlet, a *separate* vapor
pressure-control outlet through a PCV, a liquid outlet, and relief service later
— and must not be modelled as two independent inventories with a fixed liquid
pressure.

The question this ADR answers is therefore: **what is the minimum architecture
that lets a vessel be wired like a real separator, and what stays out of V1?**

---

## 2. Verified findings

All six were established by building the configuration and stepping it. No
change was made under `app/` for any of them; the working tree was clean
throughout.

### 2.1 Corrected — one port may already serve many branches

`Attachment.net_flow` sums every branch meeting the attachment node, and
`coupling.py` says so in its own docstring: *"More than one branch may meet the
node, and the port's `direction` — never its name — decides the sign."*

A vapor node carrying both K-101 and a pressure-control valve aggregates
correctly today: 141.4214 + 40.0000 = 181.4214 SCFM, and `gas_outlet_flow`
reads exactly that. Stroking the PCV moves vent flow, which moves vessel
pressure, which moves K-101's flow. **The causal chain the requirement
describes already works**, lacking only the controller that will call
`set_position_target()`.

### 2.2 Confirmed — the same idiom removes the finite test horizon

Feed and drain on one liquid node, with the vessel's level head applied to it,
produces a genuine self-regulating steady state. Level settles at 0.12335,
pressure at 78.140 psia, feed equals drain at 357.414 GPM, and the result is
bit-stable from step 5 000 to step 20 000 — no clamp, no drain-out, no drift.

The head feedback is the loop: as level rises, head rises, drain flow rises and
feed flow falls. **There is no longer any reason to end a conservation test
before the vessel fills.**

### 2.3 Confirmed — two vessel ports on two domains lose flow silently

`VesselCoupling.write_flows` *assigns* to one of four fixed attributes, so a
second vapor attachment overwrites the first:

```
two vapor withdrawals total : +541.4214 SCFM
vessel.gas_outlet_flow      : +400.0000 SCFM
SILENTLY LOST               : +141.4214 SCFM  (26.1%)
```

K-101's entire withdrawal left the pressure balance with no error raised. The
same layout inside one domain is rejected at load instead (*"domain 'gas' is not
one connected piece"*), and a separately-named vent domain is refused by
`DOMAIN_UNITS`, which knows only `liquid` and `gas`.

**The defect is not that two vapor outlets exist. It is that `write_flows`
treats an aggregate quantity as a single assignment target.**

### 2.4 Confirmed — the working idiom requires the port names to lie

`write_boundary_pressures` applies level head to an **OUTLET** attachment only,
while gas pressure lands on **both** directions. So the configuration that works
must wire the vessel's port named `outlet` to the liquid node that *receives*
the feed, and the port named `inlet` to the vapor node that *supplies* K-101 —
both backwards from their names.

The arithmetic is right and the declaration is unreadable. This is the
load-bearing argument for typing: the coupling currently infers phase from
whichever device sits on the next branch, and infers role from a direction
keyword that means something else.

### 2.5 Confirmed — phase is inferred, never declared

`FLOW_UNITS` maps device classes to units and `DOMAIN_UNITS` maps domain *names*
to units. A vessel port on a node with no branches cannot be classified at all
and is silently left uncoupled; a device absent from `FLOW_UNITS` raises; and a
vent, flare or relief header cannot be its own domain because only `liquid` and
`gas` carry a declared unit.

`Port.__slots__` is `("name", "direction", "node")`. There is nowhere to record
phase, and nowhere to record that a connection is pressure-control rather than
process service.

### 2.6 Confirmed — the C3 validator has no `oneOf`

`app/plant/validate.py` implements `type`, `enum`, `properties`, `required`,
`additionalProperties` (including as a subschema), `items`, `minItems`,
`minLength`, `minimum` and `minProperties`. Nothing else. A schema `oneOf` is
**silently ignored**, which is why T3-6 was told to enforce the wiring-form
alternation in the loader instead.

This directly constrains T3-7: the string-or-object alternation for a `ports`
entry cannot be expressed in the schema and must be enforced in the loader,
where the error can name a config path.

---

## 3. Decision

### 3.1 A port carries three orthogonal axes

> **Amended.** [Amendment 1](#amendment-1--a-connection-has-four-descriptors-not-three)
> splits `service` into independent `purpose` and `control` descriptors. The
> three-axis model below is superseded; `direction`, `phase` and `mixed` are
> unchanged.

`Port` gains **`phase`** and **`service`**. **`direction` stays separate.** The
three are independent and none is derived from another.

| Axis | Values | Means |
|---|---|---|
| `direction` | `inlet`, `outlet` | Which way material crosses the boundary. Unchanged, C1 as it stands. |
| `phase` | `liquid`, `vapor` | Which inventory the connection belongs to, and therefore which flow unit it carries. |
| `service` | `process`, `pressure_control`, `level_control`, `relief`, `drain` | What the connection is *for*. |

A declaration reads conceptually as `vapor/out/process`,
`vapor/out/pressure_control`, `liquid/in/process`, `liquid/out/level_control`.

`mixed` is **reserved and rejected at load** for V1. A stream carrying both
phases has to split, and splitting it is a flash calculation, which D6 rules
out. Reserving the name now costs nothing and stops a later session inventing a
different one.

### 3.2 Port names are identifiers, not behaviour

A port's name is a human-readable label for configuration and diagnostics. **It
must not drive engineering behaviour anywhere.** No code may branch on a port
being called `inlet`, `outlet`, `suction` or anything else; behaviour comes from
`direction`, `phase` and `service`.

This retires the 2.4 wart by construction: once phase is declared, a liquid feed
nozzle can be named `feed` and say `liquid/in/process`, and the coupling needs
nothing else.

### 3.3 Coupling aggregates; it does not overwrite, and it does not prohibit

> **Upheld, restated.** [Amendment 1 A.4](#a4--d33-restated-in-the-amended-vocabulary)
> writes the four sums below in the amended vocabulary. The substance is
> unchanged.

**A vessel may have any number of connections sharing a `(phase, direction)`
pair.** K-101 suction, PV-101 and a future PSV are all legitimately
`vapor/out`. Multiple liquid outlets are equally realistic.

The coupling therefore **sums** the flows of all matching typed ports. The
vessel's existing aggregate attributes become sums over the applicable ports
rather than assignment targets:

```
gas_outlet_flow  =  Σ flow over ports where phase = vapor  and direction = outlet
gas_inlet_flow   =  Σ flow over ports where phase = vapor  and direction = inlet
outlet_flow      =  Σ flow over ports where phase = liquid and direction = outlet
inlet_flow       =  Σ flow over ports where phase = liquid and direction = inlet
```

This is the correct fix for 2.3. Prohibition was considered and rejected: it
would have turned a silent wrong answer into a loud wrong answer, forbidding a
physically valid separator.

**What the coupling must still reject**, at Engine construction, naming the
offending port:

- a port with missing or invalid typing;
- contradictory declarations — a declared phase that disagrees with the flow
  unit confirmed from the branches at its node;
- an ambiguous legacy mapping — an untyped port that cannot be classified
  unambiguously from the devices around it;
- an attachment on an internal node (unchanged, ADR 0001 A7).

It must **not** reject multiple nozzles of the same phase and direction.

### 3.4 Service declares intent and never changes the conservation math

> **Amended and widened.** [Amendment 1 A.3](#a3--only-phase-and-direction-may-participate-in-conservation)
> restates this for `purpose` and `control`: only `phase` and `direction` may
> participate in conservation.

`service` exists so that a controller can find the valve it manipulates, so a
relief path can be told apart from a process path in a snapshot, and so the
console can label a nozzle. It is metadata.

**All vapor withdrawals participate in the same vapor balance regardless of
service**, and all liquid withdrawals in the same liquid balance. A
`pressure_control` outlet and a `relief` outlet are summed into
`gas_outlet_flow` exactly as a `process` outlet is. Any future code that lets
`service` alter a balance is a contract violation, not an optimisation.

### 3.5 Pressure is manipulated through a valve, never assigned

Already true and now stated as a contract. A controller may only move a final
element — `ControlValve.set_position_target()`. Nothing outside
`app/engine/coupling.py` may write a node pressure, and `Node.set_boundary_pressure`
already refuses an internal node.

The intended chain is, and stays:

```
vapor inventory changes → vessel pressure changes → controller responds
  → PCV position changes → vent flow changes → inventory and pressure respond
```

K-101's withdrawal participates in that same balance by D3, not by a separate
path.

### 3.6 Component inventory and phase equilibrium are out of V1

**Explicitly ruled out.** No composition model, no methane / ethane / propane
component inventories, no flash calculation, no K-values, no component balances.

The vessel keeps scalar liquid inventory (`level`, gallons) and scalar vapor
inventory (`pressure`, psia, via `gas_inventory` in scf). This preserves
`CLAUDE.md`'s engineering bar — *"Do not add thermodynamic rigor, compositional
property packages, or numerical sophistication that the training goal does not
require"* — which adding them would materially change.

The port model must stay **capable of carrying more metadata later**. Adding
composition is then a matter of extending a typed connection, not of
rediscovering what a connection is. That is the whole reason to type ports now.

> **Naming hazard.** The requirement that prompted this ADR referred to "C1/C2/C3
> service", meaning methane, ethane and propane. This repository uses **C1–C8 for
> interface contracts**. Any later task touching this subject must say
> *light-hydrocarbon components* explicitly; "C1/C2/C3" in a task note will be
> misread.

### 3.7 T5-5 ships manual valves; M8 owns closed-loop control

T5-5 contains **PV-101 and LV-101 as manual valves at fixed position** and
proves that changing those positions changes vessel behaviour correctly. It
contains no controller.

M8 owns automatic control: `T8-1` PID block, `T8-2` modes and bumpless transfer,
`T8-3` loop configuration and tag wiring, `T8-4` loop execution in the engine
step.

This is not a convenience. ADR 0001 §7 gave **T7-2, T8-3, T9-2 and T11-1** an
explicit dependency on T5-5, because each edits `config/plants/olefins_lite.yaml`.
Requiring T5-5 to ship working pressure control would invert the T8-3 edge and
deadlock M5 against M8. Manual valves keep the existing direction intact and
leave T8-3 exactly the handles it needs.

### 3.8 The vessel is not given bespoke plumbing rules

The existing flow mathematics already represents a separator (2.1, 2.2). T5-5
must not introduce vessel-specific wiring semantics, and the **shared-node
idiom** is the documented rule:

> One node per phase. Every device exchanging that phase with the vessel takes a
> branch on that node. The vessel takes one typed port per (phase, direction).

Relief later is a third branch on the vapor node and changes nothing structural.

---

## 4. Alternatives considered

**Reject duplicate `(phase, direction)` attachments.** Proposed in the design
note and rejected here. It makes the failure loud instead of silent, but a
separator with a process outlet, a PCV and a PSV is ordinary, and the
architecture would forbid it. The defect was the assignment target, not the
plurality.

**Give the vessel a fixed set of named ports — feed, vapor, PCV, drain, relief.**
Rejected: it hard-codes a nozzle count, which the requirement explicitly ruled
out, and it puts engineering meaning back into port names (D2).

**Keep inferring phase from `FLOW_UNITS` / `DOMAIN_UNITS` and simply extend
`DOMAIN_UNITS`.** Rejected: it works only while every attachment node happens to
carry a classifiable branch, leaves an equipment-free node uncoupled in silence,
and still cannot express service.

**Model the vessel as two independent inventories.** Rejected by the
requirement, and rightly: it makes vessel pressure a free variable rather than a
consequence of what enters and leaves.

---

## 5. Consequences

- **C1 changes.** `Port` gains two attributes. `app/equipment/base.py` is a
  spine file and takes the spine lock (T3-7).
- **C3 changes.** A `ports` entry may be an object carrying `phase` and
  `service`, not only a node-id string. The alternation is enforced in the
  loader, never in the schema (2.6).
- **Existing configs keep working.** `liquid_transfer.yaml`,
  `gas_compression.yaml` and `liquid_valve_train.yaml` declare no vessel and are
  untouched. `node_in` / `node_out` sugar is unchanged.
- **`DOMAIN_UNITS` is retired** once phase is declared. Domain names stop
  carrying engineering meaning, which removes the vent/flare/relief-header
  restriction found in 2.3.
- **No golden trace may move.** None of this changes a device curve. If a trace
  moves, stop and explain.

---

## 6. Sequencing

```
ADR 0002  →  T3-7  →  T5-6  →  T5-5  →  T5-7 (later)
```

**T5-7 does not block the separator fixture.** It generalises the vessel to an
arbitrary configured nozzle set *after* the typed connection contract has been
proven in anger by T5-5. Doing it first would be designing against an untested
contract.

T7-5 (relief device) is deferred and blocks nothing.

---

## 7. Numerical findings carried forward, and where they belong

Two findings from the T5-5 investigation are preserved. **Neither is fixed by
T3-7 or T5-6, and neither may be folded into them.**

### 7.1 `Engine._couple()` runs in the constructor — the conservation identity is off by one

A flow exists before the first `step()`, so each `integrate` consumes the flow
solved on the *previous* pass. The exact identity is:

```
Δinventory  =  Σ (n = 0 … N−1)  q_n · dt / 60
```

`q₀` is read from `engine.snapshot()` before the first step. Summing the N
published flows instead is wrong by `(q_N − q₀)·dt/60` — measured at 2.36 scf
over 10 000 gas steps, 0.09%, four orders outside any sensible tolerance, and it
reads as a physics bug. It hides on a constant-flow domain, where `q_N = q₀` and
both sums agree exactly.

**Owner:** the conservation-test convention, i.e. T5-4's documented method.
Record it there; do not paper over it in a fixture.

### 7.2 Period-2 limit cycle near zero vapor flow

Where `dε/dt ∝ −√ε`, the quantity reaches zero in finite time, so an explicit
Euler step overshoots and settles into a stable two-cycle — measured at
±0.0615 SCFM and ±7.5e-6 psi, still present at step 12 000 with no growth.
Bounded, deterministic and bit-stable.

**Owner:** nobody yet. It gets **its own numerical-stability task if and when it
matters for T8 controller testing** — a controller tuned against a limit-cycling
process variable is the case where it stops being harmless. Until then it is
documented, not fixed. Tests must assert a pressure asymptote rather than a
final flow, which is phase-dependent.

### 7.3 Cold-start backflow and K-101 reversal are different, and are T5-5's

These are **not** numerical artifacts. In the probe runs the feed pump backflows
on cold start against the level head, and K-101 reverses at steady state once
the vessel draws below `P_disch − 220`.

They affect whether the fixture represents a **physically credible operating
point**, so they are resolved when T5-5 is resized — by choosing design values,
not by shortening a test horizon or asserting around them. ADR 0001 §2.9 already
records that no boundary pair is sane both cold and running without a check
valve or line resistance; T5-5 now has a control valve on each phase and should
use it.

---

## 8. Build-plan changes proposed

**Not yet applied.** `docs/BUILD_PLAN.html` is synced from the live artifact, so
editing it out of band would desync the two. These are proposals.

### 8.1 New tasks

| id | m | name | files | branch | depends |
|---|---|---|---|---|---|
| **T3-7** | M3 | Typed ports — phase and service | `app/equipment/base.py`, `config/schema/plant.schema.json`, `app/plant/loader.py`, `tests/test_typed_ports.py` | `feature/typed-ports` | T3-6 |
| **T5-6** | M5 | Coupling aggregates typed connections | `app/engine/coupling.py`, `tests/test_coupling_aggregation.py` | `feature/coupling-aggregation` | T3-7, T5-2 |
| **T5-7** | M5 | Vessel configurable nozzle set | `app/equipment/vessel.py` | `feature/vessel-nozzles` | T5-5 |
| **T7-5** | M7 | Relief device | `app/equipment/relief.py` | `feature/relief-valve` | T3-7 |

T3-7 is **spine** (`app/equipment/base.py`). T5-6 modifies an existing
`app/engine/` module and is spine by the rule in `CLAUDE.md`.

### 8.2 Edited dependencies

- **T5-5** gains `T3-7` and `T5-6`; its acceptance changes from a horizon-bounded
  conservation test to a steady-state one, and gains *"changing PV-101 or LV-101
  position changes vessel pressure or level in the expected direction"*.
- **T5-4** should be sequenced after T5-5 and inherit 7.1, replacing its
  "10 000 steps at steady state" criterion, which was written against a vessel
  that fills.
- **T7-2, T8-3, T9-2, T11-1** keep their existing T5-5 edges unchanged (D7).

### 8.3 `CLAUDE.md`

One addition to the critical-invariants section, once T3-7 lands: port names are
identifiers and must not drive behaviour (D2), and service never alters a
conservation balance (D4).

---

## 9. Explicit non-goals

- No composition, flash, K-values or component balances (D6).
- No controller, PID or loop execution — M8 owns all of it (D7).
- No relief *device* in this work; the wiring supports one, T7-5 builds it.
- No change to `NetworkSolver`, to C2, or to the single-domain solver rule.
- No mixed-phase connection in V1; the value is reserved and rejected.

---

## Evidence

Reproducible against `main` at `f8aab59`. Each probe was configuration-only,
loading a config through `load_plant` and stepping an `Engine.from_plant`; no
file under `app/` was modified for any of them.

| Finding | How it was established |
|---|---|
| 2.1 | Vapor node `N-201` with branches `B-K-101` and `B-PV-101`; compared `gas_outlet_flow` against the branch sum. |
| 2.2 | Liquid node `N-102` carrying `B-P-101` and `B-LV-101`, vessel liquid port on it; ran 20 000 steps and compared level, pressure and both flows at steps 5 000 and 20 000. |
| 2.3 | Three-port `Vessel` subclass with two vapor outlets in domains `gas` and `vent`; compared `gas_outlet_flow` against the sum of both attachments' `net_flow`. Same layout in one domain rejected by `_solvability_errors`. |
| 2.4 | Read `VesselCoupling.write_boundary_pressures`; confirmed by sweeping `head_at_full` over 0.0, 5.0 and 50.0 with the liquid attachment on an INLET port and observing bit-identical flow and level. |
| 2.5 | Read `Port.__slots__`, `FLOW_UNITS`, `DOMAIN_UNITS` and `_unit_at`. |
| 2.6 | Read the keyword list in the `app/plant/validate.py` module docstring and `_check` / `_check_object`. |

---

## Amendment 1 — a connection has four descriptors, not three

| | |
|---|---|
| **Status** | Accepted 20 September 2026, before T3-7 wrote anything. Amends D3.1, D3.3 and D3.4 of this record, and the T3-7 row of Section 8.1. Applied to `docs/BUILD_PLAN.html`, the live build-plan artifact, `docs/BUILD_PLAN_STATUS.json`, `docs/PROJECT_STATE.md` and `CLAUDE.md`. |
| **Raised by** | The review of this ADR held before T3-7 implementation began. |
| **Verified against** | `main` at `4ced83c`, 753 tests passing, `mypy` clean over 24 source files |
| **Amends** | D3.1 (`service` replaced by `purpose` + `control`), D3.3 (restated in the new vocabulary), D3.4 (widened and strengthened), Section 8.1's T3-7 row |

**Where the original three-axis `phase` / `direction` / `service` decision
conflicts with this amendment, Amendment 1 wins.**

### A.0 The defect: `service` was three concepts in one enum

D3.1 gave a port a single `service` axis with the values `process`,
`pressure_control`, `level_control`, `relief` and `drain`. Read closely, that
list answers three different questions at once:

- **what the connection physically is** — a process nozzle, a vent, a drain, a
  relief path;
- **what controlled variable it serves** — pressure, level, and later flow or
  temperature;
- and, in `process`, neither of the two.

The values are therefore not alternatives. A real connection routinely has a
value from more than one of those groups, and a single enum forces a choice
between them that the plant does not make:

- **a vessel drain under level control** is a drain *and* a level-control
  connection. `drain` and `level_control` are both true, and the enum admits
  one.
- **a vent under pressure control** — PV-101 — is a vent *and* a
  pressure-control connection, for the same reason.
- **a manual drain** is a drain and is under no control at all, which
  `level_control` would assert and `drain` would leave unsaid.
- **FV-101 on the normal process line** is a process connection under flow
  control, and the original enum had no way to say "flow" whatsoever.
- **a future temperature-control utility connection** was unrepresentable.

A vocabulary that cannot describe a manual drain and a level-controlled drain as
different things, while describing both as drains, is not yet a vocabulary. The
defect was caught before T3-7 turned it into a frozen contract, a schema and a
runtime attribute, which is the cheapest moment it could have been caught.

### A.1 — four descriptors, two of them orthogonal

> **A1.** A connection is described by **`name`**, **`direction`**, **`phase`**,
> **`purpose`** and an optional **`control`**. `purpose` and `control` are
> independent of each other, and neither is derived from the other or from
> `direction` or `phase`.

| Descriptor | Values | Meaning |
|---|---|---|
| `name` | arbitrary identifier | Human-readable identifier only. **Never behaviour.** |
| `direction` | `inlet`, `outlet` | Which way material crosses the equipment boundary. Unchanged from C1 as it stands. |
| `phase` | `liquid`, `vapor` | Which inventory the connection belongs to, and therefore which flow unit it carries. |
| `purpose` | `process`, `vent`, `drain`, `relief` | The physical purpose of the connection. |
| `control` | `flow`, `pressure`, `level`, `temperature`, or absent | The controlled variable this connection participates in, if any. |

`process`, `vent`, `drain` and `relief` name what a connection *is*. `flow`,
`pressure`, `level` and `temperature` name what it is *controlled on*. One
connection may carry both, so they cannot correctly occupy one enum — which is
precisely what A.0 found.

The seven examples that drove the amendment, written out:

```
normal process vapor outlet       phase=vapor   purpose=process  control=None
manual vessel vent                phase=vapor   purpose=vent     control=None
PV-101 connection                 phase=vapor   purpose=vent     control=pressure
manual vessel drain               phase=liquid  purpose=drain    control=None
LV-101 connection                 phase=liquid  purpose=drain    control=level
FV-101 manipulated process line   phase=liquid  purpose=process  control=flow
future temperature utility        phase=liquid  purpose=process  control=temperature
PSV connection                    phase=vapor   purpose=relief   control=None
```

`mixed` remains reserved and rejected at load, for the reason D3.1 gave: a
stream carrying both phases has to split, and splitting it is a flash
calculation, which D6 rules out.

### A.2 — `control` is not a controller

The descriptor is named `control`, deliberately **not** `controller`.
Controllers do not exist. It records the control function a connection is
associated with, so that an M8 controller can later *find* the final element it
manipulates. M8 owns controller objects, PID and loop execution, and D7 is
unchanged: nothing here brings any of that forward.

### A.3 — only `phase` and `direction` may participate in conservation

This restates D3.4, widens it to cover `control`, and is the load-bearing
invariant of the whole amendment.

> **A3.** The classification that decides a mass balance is **`phase` +
> `direction`**, and nothing else. `purpose` and `control` are descriptive
> metadata and **must never alter a conservation result.**

Every `vapor` + `outlet` connection contributes to the vapor withdrawal —
whether its purpose is `process`, `vent` or `relief`, and whether its control is
`pressure` or absent. Every `liquid` + `outlet` connection contributes to the
liquid withdrawal on the same terms. A vapor withdrawal does not become less of
a vapor withdrawal because someone labelled it a vent.

Any future code that lets `purpose` or `control` change a balance is a contract
violation, not an optimisation.

**T3-7 records this invariant. T5-6 implements the aggregation.** They are
separate tasks and the aggregation must not be written into T3-7.

### A.4 — D3.3 restated in the amended vocabulary

D3.3 is upheld unchanged in substance. Written in the new descriptors:

```
gas_outlet_flow  =  Σ flow over ports where phase = vapor  and direction = outlet
gas_inlet_flow   =  Σ flow over ports where phase = vapor  and direction = inlet
outlet_flow      =  Σ flow over ports where phase = liquid and direction = outlet
inlet_flow       =  Σ flow over ports where phase = liquid and direction = inlet
```

`purpose` and `control` appear nowhere in those four sums, which is A3 stated
arithmetically. A vessel may still carry any number of connections sharing a
`(phase, direction)` pair, and the coupling still sums rather than assigns.

### A.5 — no engineering restriction is imposed between the descriptors

The combinations that happen to appear in the V1 fixture — pressure control on
vapor, level control on liquid, relief on vapor — are **properties of that
plant, not of the connection contract**. `Port` and the C3 loader accept any
`(phase, purpose, control)` combination drawn from the accepted values.

A liquid relief path and a vapor drum drain are both real, and a generic
vocabulary that forbade them would be wrong. If a specific piece of equipment
ever needs such a restriction, it belongs to that equipment's own validation or
to a plant-configuration check at a higher layer, never to `Port`.

### A.6 — the typed C3 entry, and what stays untyped

A `ports` entry is **either** a node-id string **or** a typed object. The
alternation is enforced in the loader, naming the config path, because the C3
validator silently ignores `oneOf` (finding 2.6, unchanged).

The typed object requires `node`, `phase` and `purpose`, and takes `control`
optionally:

```yaml
ports:
  vapor_out:     {node: N-201, phase: vapor,  purpose: process}
  pressure_out:  {node: N-202, phase: vapor,  purpose: vent,  control: pressure}
  liquid_draw:   {node: N-103, phase: liquid, purpose: drain, control: level}
  relief:        {node: N-204, phase: vapor,  purpose: relief}
```

There is **one** typed form and no half-typed one: a `node` and a `phase` with
no `purpose` is rejected rather than defaulted. Absence of `control` means no
declared control role, and `control: none` is not how that is written.

The legacy string form stays valid and stays **untyped** — it declares
attachment and nothing more, exactly as A1 of ADR 0001 says. A legacy config is
never silently upgraded, and `to_config()` emits back the form it loaded:
a string round-trips as a string, a typed object round-trips as a typed object
with its metadata intact, and `node_in` / `node_out` sugar round-trips as sugar.

Typing comes from **configuration**, not from the device class. An equipment
class keeps declaring its structural ports by `name` and `direction`; the loader
places the semantic metadata on the runtime `Port`. No device-specific
inference — nothing that reads `isinstance(device, GasCompressor)` or
`port.name == "drain"` to decide a phase or a purpose — is permitted, because
that is the inference T3-7 exists to retire.

### A.7 — what `Port` may and may not carry

`Port` gains `phase`, `purpose` and `control` in `__slots__`. They are
**connection metadata**, of the same kind as the node a port is attached to.

The thing `Port` must remain structurally incapable of carrying is
**process state** — a pressure, a flow, a temperature, a level. Those are solver
outputs, and `__slots__` is what keeps a device from stashing one on a port even
by accident. D3.2's statement that a port name never drives behaviour is upheld
and, at T3-7, becomes an enforced guard test rather than a convention.

The older C1 wording that a port carries the node it is attached to *"and
nothing else"* is superseded by this paragraph: the distinction is between
connection metadata, which a port may carry, and process state, which it may
not.

### A.8 — preserved unchanged from the original record

Amendment 1 reopens nothing else. These conclusions stand exactly as written:

- port names never drive engineering behaviour (D3.2);
- phase is explicit rather than inferred (D3.1, D3.5's finding);
- several compatible connections on one `(phase, direction)` pair are valid, and
  the coupling sums them rather than assigning (D3.3);
- **T5-6** aggregates them; T3-7 creates the vocabulary and no more;
- metadata never changes conservation (D3.4, widened by A3);
- a controller manipulates a final element and never assigns a pressure (D3.5);
- no composition, flash, K-values or component balances in V1 (D6);
- T5-5 ships manual PV-101 and LV-101, and M8 owns closed-loop control (D7);
- the shared-node idiom, with no vessel-specific plumbing rules (D8);
- **T5-5 resumes at construction** once T3-7 and T5-6 have merged. Its design
  phase already happened and produced this record; there is no second one.

The sequencing is unchanged:

```
ADR 0002  →  T3-7  →  T5-6  →  T5-5  →  T5-7 (later)
```

### A.9 — consequences for Section 8.1

The T3-7 row of Section 8.1 is superseded by:

| id | m | name | files | branch | depends |
|---|---|---|---|---|---|
| **T3-7** | M3 | Typed ports — phase, purpose and control | `app/equipment/base.py`, `config/schema/plant.schema.json`, `app/plant/loader.py`, `tests/test_typed_ports.py`, `tests/test_port_name_guard.py`, `CLAUDE.md`, `docs/ADR_0002_TYPED_PORTS.md`, `docs/BUILD_PLAN.html`, `docs/BUILD_PLAN_STATUS.json`, `docs/PROJECT_STATE.md` | `feature/typed-ports` | T3-6 |

The T5-6, T5-7 and T7-5 rows are unchanged, and so are the dependency edges in
8.2. Section 8.3's `CLAUDE.md` addition is widened: the invariants recorded when
T3-7 lands are that port names are identifiers only, that `phase` and
`direction` own conservation classification, and that `purpose` and `control`
are descriptive and never change a balance.
