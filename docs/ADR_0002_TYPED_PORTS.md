# ADR 0002 — Typed ports and the vessel connection model

| | |
|---|---|
| **Status** | Accepted 20 September 2026, **amended three times**. [Amendment 1](#amendment-1--a-connection-has-four-descriptors-not-three), before T3-7, replaces the single `service` axis with independent `purpose` and `control` descriptors. [Amendment 2](#amendment-2--the-node-is-the-unit-of-account-not-the-port), before T5-6, makes the **node** the unit of account for the aggregation D3.3 wrote as a sum over ports. [Amendment 3](#amendment-3--t5-7-comes-before-t5-5-and-a-vessel-may-take-its-ports-from-configuration), before T5-5 and T5-7, corrects the Section 6 sequencing, puts **T5-7 before T5-5**, and lets an opted-in device take its structural port set from configuration. It also specifies T5-7. Read Section 3 together with all three: **where they disagree, the later amendment wins.** |
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
- **Amended — a vessel's structure also comes from C3.** This section assumed
  the separator fixture fitted the existing two-port `Vessel`. It does not.
  [Amendment 3](#amendment-3--t5-7-comes-before-t5-5-and-a-vessel-may-take-its-ports-from-configuration)
  adds `direction` to a typed entry for a device that opts in to
  configured-port mode (only `Vessel`), and T5-7 builds it before T5-5.
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

> **Corrected by [Amendment 3](#amendment-3--t5-7-comes-before-t5-5-and-a-vessel-may-take-its-ports-from-configuration).**
> The sequence originally written here was `T3-7 → T5-6 → T5-5 → T5-7 (later)`,
> with the claim that *"T5-7 does not block the separator fixture"*. That claim
> was false. It assumed the fixture fitted the existing vessel, but the
> production `Vessel` declares only `inlet` and `outlet`, and the loader refuses
> any other port name (C.0).

```
ADR 0002  →  T3-7  →  T5-6  →  T5-7  →  T5-5  →  T5-4
```

**T5-7 blocks the separator fixture.** The typed connection contract is proven
by T5-6's aggregation tests. What T5-5 lacks is a production vessel that can
declare the three ports a separator needs, and that is exactly what T5-7
provides. Designing T5-7 first is therefore not designing against an untested
contract: it is the missing piece of the contract.

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
| **T5-7** | M5 | Vessel configurable nozzle set | `app/equipment/vessel.py` *(superseded by [C.10](#c10--t5-7-owns-the-loader-and-leaves-c1-alone))* | `feature/vessel-nozzles` | T5-5 *(now T5-6, Amendment 3)* |
| **T7-5** | M7 | Relief device | `app/equipment/relief.py` | `feature/relief-valve` | T3-7 |

T3-7 is **spine** (`app/equipment/base.py`). T5-6 modifies an existing
`app/engine/` module and is spine by the rule in `CLAUDE.md`.

### 8.2 Edited dependencies

- **T5-5** gains `T3-7` and `T5-6` *(Amendment 3 replaces both edges, and
  `T5-2`, with a single `T5-7` edge that carries all three)*; its acceptance
  changes from a horizon-bounded
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

The sequencing is unchanged *(corrected by Amendment 3, which puts T5-7
before T5-5)*:

```
ADR 0002  →  T3-7  →  T5-6  →  T5-7  →  T5-5
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

---

## Amendment 2 — the node is the unit of account, not the port

| | |
|---|---|
| **Status** | Accepted 20 September 2026, before T5-6 wrote anything. Amends D3.3, D3.8 and Amendment 1 A.4 of this record. Applied to `app/engine/coupling.py`, `docs/BUILD_PLAN.html`, the live build-plan artifact, `docs/BUILD_PLAN_STATUS.json`, `docs/ARCHITECTURE.md`, `docs/PROJECT_STATE.md` and `CLAUDE.md`. |
| **Raised by** | The review of T3-7 held before T5-6 implementation began. |
| **Verified against** | `main` at `1848d53`, 868 tests passing, `mypy` clean over 24 source files |
| **Amends** | D3.3 and Amendment 1 A.4 (the four sums are over *nodes*, not ports), D3.8 (the shared-node idiom gains a stated accounting rule), and D5's `DOMAIN_UNITS` retirement (now an acceptance criterion with a replacement rule) |

**Where D3.3, D3.8 or Amendment 1 A.4 conflicts with Amendment 2, Amendment 2
wins.**

### B.0 The defect: a port has no flow of its own

D3.3 and A.4 write the four aggregates as sums **over ports**:

```
gas_outlet_flow  =  Σ flow over ports where phase = vapor and direction = outlet
```

That wording was written against `Attachment.net_flow`, which is not a port
quantity at all. It is derived from **every branch meeting the attachment
node**:

```
arrivals   = Σ branch.flow for branches terminating at the node
departures = Σ branch.flow for branches originating at the node
net        = arrivals - departures        (read through an INLET declaration)
```

That is a **node exchange**. A port is a declaration about a connection; the
hydraulics belong to the node the connection lands on. Two ports of one vessel
attached to one node therefore do **not** own two independent flows, and
summing `net_flow` once per port would count the same node twice — a silent
doubling of real plant inventory transfer, which is the same class of defect as
the one T5-6 exists to fix.

Netting each node into a single signed quantity is not the answer either. It
discards gross directional information the `Vessel` already exposes and uses:
`residence_time` is `volume / outlet_flow`, and a vessel fed at 357 GPM and
drained at 357 GPM through one node has zero net exchange and 357 GPM of real
throughput. Net-only accounting would report no outflow and no residence time
for a vessel that is plainly flowing.

Amendment 2 therefore defines **node-based accounting with gross directional
components**.

### B.1 — account each hydraulic node exactly once

> **B1.** The fundamental accounting entity is the unique plant **node**. Every
> coupled node contributes to the aggregates exactly once, however many ports
> the device declares on it.

Node ids are unique plant-wide, so the node id alone is the key. Aggregation is
**not** keyed by port, and not by `(topology, node, phase)`.

All of a device's typed ports on one node must agree on `phase`. Two ports on
one node declaring conflicting phases are refused at Engine construction, naming
the node and both ports. A node may not be counted once as liquid and again as
vapor. The refusal applies even where no machine is present to expose the
contradiction — on a valve-only node, or on a node with no branches at all.

### B.2 — gross components are signed sums over branch orientation

For every coupled node, computed once:

```
arrivals   = Σ branch.flow for branches terminating at this node
departures = Σ branch.flow for branches originating at this node
```

**"Gross" means separated by branch orientation. It does not mean positive
magnitude.** No `abs()`, no `max(flow, 0)`, no clamping. A feed branch running
backwards contributes a negative `arrivals`, and that is required: §7.3 records
cold-start pump backflow as real physics, not an artifact, and clamping it would
invent inventory the plant never received.

### B.3 — what a node contributes depends on which directions are declared

Having computed the node's signed gross components, inspect which of the
device's port directions are declared **on that node**, for that phase.

**Case 1 — both an inlet and an outlet are declared.** Gross directional
exchange survives:

```
phase inlet aggregate  += arrivals
phase outlet aggregate += departures
```

Each component is added once for the node, regardless of how many matching ports
exist. A vessel fed and drained through one node at steady state reads
`inlet_flow = 357.414`, `outlet_flow = 357.414`, net zero — conserving exactly,
while keeping throughput, `residence_time` and the §2.2 self-regulating
behaviour intact.

**Case 2 — only one direction is declared.** The node's **net signed exchange**
is written through that declaration:

```
INLET-only:   inlet aggregate  += arrivals - departures
OUTLET-only:  outlet aggregate += departures - arrivals
```

This preserves the historical shared-node idiom and is a compatibility
requirement, not a convenience. §2.2's successful probe used a *single* vessel
port on a node carrying both a feed branch and a drain branch. Dropping the
unmatched component there would delete a real feed from the vessel's balance.
Such a layout is **not** refused merely because the node is hydraulically
two-sided; it is existing, working architecture.

The sign convention is the one `Attachment.net_flow` already used, so every
existing single-port configuration reduces to exactly the number it produced
before aggregation existed.

**Case 3 — duplicate ports of the same direction.** Several ports on one node
sharing a `(phase, direction)` pair are semantic duplicates with respect to
hydraulics. They do not multiply the node's exchange; the node is counted once.
This is distinct from several matching ports on **different** nodes, whose
exchanges are independent and do sum.

### B.4 — the conservation invariant

For each phase, after every successfully updated aggregate:

```
liquid net = inlet_flow     - outlet_flow
gas net    = gas_inlet_flow - gas_outlet_flow
```

equals the sum of the corresponding node exchanges, with **each hydraulic node
represented exactly once**. No port count may multiply a node's exchange, and no
unmatched branch component may disappear.

### B.5 — distinct nodes still sum, which is the original defect

Finding 2.3 stands unchanged. Two vapor withdrawals on two different nodes or
domains are two independent exchanges and both contribute:

```
node A withdrawal = 141.4214 SCFM
node B withdrawal = 400.0000 SCFM
gas_outlet_flow   = 541.4214 SCFM
```

Nothing is overwritten and nothing is lost. The distinction B1 draws is between
*several ports on one node* — counted once — and *several nodes* — summed.

### B.6 — two inventory devices may not claim one node

Current coupling semantics cannot conserve a node shared by two independent
inventory devices: each `VesselCoupling` would read the whole node exchange as
its own, and the same transfer would be added to two inventories.

This is refused at Engine construction, naming the node, both device tags and
the ports involved. The exchange is not silently divided and no split is
guessed, because there is no basis on which to guess one.

### B.7 — boundary-pressure writes are per node, not per port

Node deduplication governs writing as well as reading.

- **Gas.** A vapor attachment writes the vessel's absolute pressure to the node.
  Where several of one vessel's vapor ports reference one node the write is
  idempotent, and it happens once.
- **Liquid.** Vessel head is applied only where the vessel *supplies* the
  hydraulic system — a node carrying an **outlet** declaration. A node with no
  liquid outlet declaration keeps its configured battery limit. A node carrying
  one or several liquid outlet declarations from the same vessel gets
  `configured_pressure + head` **once**. Duplicate nozzles must not apply a head
  twice.

### B.8 — declared phase is primary, and `DOMAIN_UNITS` is retired

> **B8.** A connection's flow unit comes from its declared `phase`:
> `liquid` → GPM, `vapor` → SCFM. Domain names carry no engineering meaning.

`DOMAIN_UNITS` is deleted, which is D5's retirement made an acceptance
criterion. A domain may be called `gas`, `vent`, `flare`, `relief_header`,
`process_water` or anything else without its name selecting a unit, and it may
not be reintroduced as a hidden legacy fallback.

**Known machines still confirm.** `CentrifugalPump` writes its characteristic in
GPM and `GasCompressor` in SCFM. A declared `phase` that contradicts the
machines at its node is refused at Engine construction, naming the vessel port —
`phase: vapor` beside a GPM-confirmed node, or `phase: liquid` beside an
SCFM-confirmed one.

**`ControlValve` is unit-neutral, not unknown.** A resistance has no intrinsic
flow unit and is valid in either service, so it contributes neither a confirming
nor a contradicting unit. That is represented explicitly in the flow-unit table.
It must never be generalised into "anything unrecognised is acceptable": an
**unknown equipment model** encountered while confirming a node is still an
error, and restoring domain-name inference is not an alternative.

**Legacy untyped ports.** A port with no declared phase is classified from the
machines around its node where they settle it unambiguously — untyped beside a
pump is GPM, untyped beside a compressor is SCFM, both exactly as before. Untyped
beside a valve-only node, or on a node with no branches, can no longer be
classified at all and is refused, naming the port and asking for a `phase`.

**Branchless typed attachments couple.** A typed vapor vent or flare boundary
with no branch on it classifies from its declaration and is a real attachment:
it carries the vessel's pressure and contributes a zero exchange. It is no longer
silently ignored. The same holds for a typed liquid attachment.

### B.9 — `purpose` and `control` remain invisible to conservation

Amendment 1 A.3 is upheld without qualification, and T5-6 is where it becomes
executable. Nothing in the coupling reads `purpose` or `control` to decide an
aggregate target, a phase, a unit, a sign, or whether a flow counts. These three
vapor outlets participate in the vapor balance identically:

```
phase=vapor  purpose=process
phase=vapor  purpose=vent     control=pressure
phase=vapor  purpose=relief
```

Only `phase`, `direction` and the hydraulics of the node matter.

### B.10 — a failed domain holds a whole aggregate

T4-3's rationale — a failed solve does not own a new physical state — is
strengthened from per-attachment to **per aggregate**.

Candidate aggregates are computed before anything is written. If any node
contributing to an aggregate belongs to a domain that did not converge, that
**entire** attribute keeps its previous value. The sum of only the converged
contributors is never written: it would mix numbers from two different steps and
report a plant that never existed.

An unrelated aggregate whose own contributors all converged still updates. If
`gas_outlet_flow` draws on a converged process-gas node and a failed vent node,
`gas_outlet_flow` holds while `inlet_flow` may advance.

### B.11 — determinism

Existing configurations must remain **bit-identical**. Every coupled node on
`main` at `1848d53` carries one vessel attachment and a one-sided declaration, so
Case 2 reduces exactly to the value the previous code produced.

The rules that keep it exact: deterministic port and node ordering; a single
contribution assigned directly rather than added to a zero seed, so a lone `-0.0`
survives and no extra rounding step appears; no reordering of sums through sets.
No golden trace may move. If one does, stop and explain.

### B.12 — what Amendment 2 does not reopen

Unchanged: the four descriptors and their meanings (A.1); `control` is not a
controller (A.2); conservation is `phase` + `direction` only (A.3); any number of
connections may share a `(phase, direction)` pair (A.4, now correctly scoped to
distinct nodes); the typed C3 entry and the untyped legacy string (A.6); what
`Port` may carry (A.7); no composition, flash or K-values (D6); manual valves in
T5-5 and M8 owning control (D7); no change to `NetworkSolver`, to C2, or to the
single-domain solver rule (§9).

§7.1 and §7.2 are **not** fixed here and must not be folded in. The sequencing is
unchanged *(corrected by Amendment 3, which puts T5-7 before T5-5)*:

```
ADR 0002  →  T3-7  →  T5-6  →  T5-7  →  T5-5
```

---

## Amendment 3 — T5-7 comes before T5-5, and a vessel may take its ports from configuration

| | |
|---|---|
| **Status** | Accepted 21 September 2026, before T5-5 or T5-7 wrote anything. **Implemented by T5-7** (`app/equipment/vessel.py`, `app/plant/loader.py`, on branch `feature/vessel-nozzles`) — see `docs/PROJECT_STATE.md` for whether it has reached `main` yet. Amends Section 5, Section 6, Section 8.1's T5-7 row, Section 8.2's T5-5 edges, Amendment 1 A.6, the sequencing diagrams in A.8 and B.12, and the `CLAUDE.md` invariant on where a port's structure comes from. `CLAUDE.md` and `docs/ARCHITECTURE.md` are updated by T5-7 directly; `docs/BUILD_PLAN.html`, the live build-plan artifact, `docs/BUILD_PLAN_STATUS.json` and `docs/PROJECT_STATE.md` are refreshed once T5-7 merges, per the project's standard practice of recording "Complete" only with a merge SHA. |
| **Raised by** | The review of T5-5's readiness held after T5-6 merged. |
| **Verified against** | `main` at `e028280`, 971 tests passing, `mypy` clean over 24 source files |
| **Amends** | Section 6 (sequencing), Section 5 (C3 consequences), Amendment 1 A.6 (structure from the device class), Section 8.1 and 8.2 (T5-7 row, T5-5 edges), and it specifies T5-7 (C.4–C.11) |

**Where Section 5, Section 6 or Amendment 1 A.6 conflicts with Amendment 3,
Amendment 3 wins.** Amendment 3 changes nothing about what a port *means*, how
a node is counted, or which descriptors participate in a balance.

### C.0 The defect: the fixture does not fit the vessel on `main`

Section 6 sequenced T5-5 before T5-7 and said *"T5-7 does not block the
separator fixture."* That assumed the separator could be wired with the
existing `Vessel`. It cannot:

- `Vessel.__init__` declares exactly two ports, `inlet` (INLET) and `outlet`
  (OUTLET), in `app/equipment/vessel.py`.
- `_build_named` in `app/plant/loader.py` rejects any configured port name the
  device lacks (*"has no port"*) and any device port left unwired (*"is not
  wired to any node"*).
- A port's `direction` lives on the device class. A typed C3 entry carries
  `node`, `phase`, `purpose` and `control`, and `_typed_port_errors` rejects
  `direction` as an unexpected property.
- T5-6's tests only run because `tests/test_coupling_aggregation.py` defines
  seven test-only `Separator(Vessel)` subclasses. The base subclass's docstring
  says so: *"T5-7 gives the real Vessel this. Until then a vessel has exactly
  two ports and T5-6 cannot be tested at all."*

With the edges as written (T5-5 on T5-6, T5-7 on T5-5), T5-5 had no way to wire
the separator with the production vessel. T5-4, T7-2, T8-3, T9-2 and T11-1 all
wait on T5-5, so the whole M5–M11 path was stalled behind a task that could
not be built honestly.

### C.1 — the dependency graph

> **C1.** T5-7 depends on T5-6. T5-5 depends on T3-4 and T5-7. T5-4, T7-2, T8-3,
> T9-2 and T11-1 keep their T5-5 edges.

```
T3-6 → T3-7 → T5-6 → T5-7 → T5-5 → T5-4
          T5-2 ──┘          ↑   └──→ T7-2, T8-3, T9-2, T11-1
                     T3-4 ──┘
```

T5-7's only direct dependency is T5-6. T5-6 carries T3-7 and T5-2. T5-5's
explicit `T5-2`, `T3-7` and `T5-6` edges are removed because T5-7 now carries
all three. This follows the plan's convention against transitive edges, which
T5-5's own note already applied to T5-1, T4-4 and T3-5. The explicit edges had
documentary value while nothing sat between T5-6 and T5-5; that value now
lives in T5-5's note, which names T5-7 as the blocker and says why. `T3-4`
stays, because nothing on the T5-7 path carries it. The graph was checked to
be acyclic over all 101 tasks.

Statuses: **T5-7 Not Started, and startable** (T5-6 is Complete as `7643281`).
**T5-5 Blocked**, with a note naming T5-7.

### C.2 — Amendment 1 A.6 amended: structure may come from configuration

A.6 says, verbatim:

> Typing comes from **configuration**, not from the device class. An equipment
> class keeps declaring its structural ports by `name` and `direction`; the
> loader places the semantic metadata on the runtime `Port`. No
> device-specific inference — nothing that reads
> `isinstance(device, GasCompressor)` or `port.name == "drain"` to decide a
> phase or a purpose — is permitted, because that is the inference T3-7 exists
> to retire.

Its second sentence is amended. The rest stands. The rule becomes:

> **C2.** **Fixed-port equipment** keeps declaring its structural ports, by name
> and direction, in its class. A **configurable-port device**, one whose class
> explicitly opts in (C.6), may instead receive its structural port set from
> C3. In that mode configuration supplies `direction` for **every** port. Typing
> still comes from configuration in both modes, and no behaviour is ever
> inferred from a port name.

The first and third sentences of A.6 are unchanged. Configured-port mode adds
no inference: `direction` is *declared*, the same way `phase` is, and the
loader places it on the runtime `Port` exactly as it places `phase`.

### C.3 — the `CLAUDE.md` invariant amended

`CLAUDE.md`, under *A connection is described, never inferred*, says:

> A device declares its ports by name and direction; the C3 loader puts the
> descriptors on the runtime `Port`.

This becomes C2's rule. **Until T5-7 merges, `CLAUDE.md` and every other
document presents configured-port mode as a ruling not yet implemented.** No
document may imply that `main` accepts `direction` in configuration before it
does. T5-7 moves the wording to the present tense when it lands.

### C.4 — the grammar (frozen)

Configured-port mode is decided per equipment item, from the `direction` keys
in its `ports` map:

| `ports` entries | Result |
|---|---|
| no entry carries `direction` | **Fixed-port mode.** The device's own ports are used, exactly as on `main` today. This is what every existing `Vessel` config does. |
| every entry carries `direction`, device opts in | **Configured-port mode.** The port set is built from configuration. |
| some entries carry `direction`, some do not | **Rejected, as a whole item.** Every entry that lacks `direction` is named by its path. |
| any entry carries `direction`, device does not opt in | **Rejected.** Configuration cannot redefine a pump's, compressor's or valve's structure. Every entry carrying `direction` is named by its path. |

- **Order.** Configured ports are created in config order, which is the order
  of the `ports` mapping as parsed (YAML and JSON both preserve it).
- **Round trip.** `to_config()` emits `direction` on every configured entry,
  with the entries in their loaded order.
- **No upgrade.** A fixed-port config round-trips without gaining a
  `direction`. `direction` is emitted only where the entry declared one, which
  mirrors A.6's rule that a legacy string is never silently upgraded.
- `node_in` / `node_out` sugar has nowhere to put a `direction`, so it is
  always fixed-port mode, and it is unchanged.

### C.5 — typed entries only, and the spelling of `direction`

**A bare node-id string cannot carry a direction.** Configured-port mode
therefore requires the typed object form for every entry: `node`, `phase`,
`purpose` and `direction`, plus `control` where the connection has a control
role. All the A.6 rules still apply: `phase`, `purpose` and `node` are
required, `control` is optional, absence means no control role, and
`control: none` is refused. A string entry inside a configured item is a
"some entries lack `direction`" case (C.4), and its error says that a string
cannot declare a direction.

**`direction` takes exactly the values of `INLET` (`"inlet"`) and `OUTLET`
(`"outlet"`) in `app/equipment/base.py`**, validated against `PORT_DIRECTIONS`.
There are no new aliases: no `in` / `out`, no `feed` / `draw`, and no casing
variants. An unknown value is rejected at `…ports.<name>.direction`, naming
the allowed values.

`direction` joins the keys `_typed_port_errors` accepts. The *mode* rules in
C.4 then decide whether its presence is allowed on a given item. Today a
`direction` key is refused as an *"unexpected property"*. After T5-7, a
`direction` key on fixed-port equipment gets the specific refusal from C.4
instead of that generic one.

### C.6 — the opt-in: a class-level declaration, on `Vessel` only

> **C6.** A device class opts in by declaring
> `accepts_configured_ports: ClassVar[bool] = True`. Only `Vessel` declares it.
> The loader reads it **from the class** with
> `getattr(device_type, "accepts_configured_ports", False) is True`, where
> `device_type` is `types[item["type"]]` in the reference pass and
> `type(device)` in the build pass. It never reads it from an instance.

**Why this mechanism.** It is an explicit statement the class makes about
itself, so no inference is involved. It needs no change to C1: `base.py`
declares no default, and the `getattr` default is what makes every other class
fixed-port. It also follows `load_plant`'s existing `device_types` override,
because the opt-in belongs to whatever class a type string actually resolves
to. A subclass of `Vessel` inherits the opt-in, which is correct: a subclass of
a vessel is a vessel.

**Rejected alternatives:**

- *A default `accepts_configured_ports = False` on `Equipment`.* This would
  give cleaner typing, but it changes C1, a spine file, for a capability one
  device has. The `getattr` default gives the same behaviour with `base.py`
  untouched.
- *A loader-side list such as `CONFIGURED_PORT_TYPES = (Vessel,)` checked with
  `isinstance` or `issubclass`.* This is class-identity inference, which A.6
  forbids. It also puts knowledge of one device inside the generic loader.
- *Keying on the config `type` string (`type == "vessel"`).* The string's
  mapping to a class can be overridden, so the opt-in would detach from the
  class actually built. It is also a name-based rule.
- *A constructor argument (`Vessel(tag, ports=...)`).* The loader builds every
  device the same way, `types[type](tag)`. This would need a device-specific
  construction path.
- *A `design` key.* Design is for values, and `_apply_design` round-trips
  whatever it sets, so structure would become a tunable design value.

**`accepts_configured_ports` is an internal capability marker, not a design
parameter, and configuration may not set it.** `_apply_design` accepts any
attribute visible through `hasattr(device, key)`. On `main`, a
`design: {accepts_configured_ports: false}` entry on a vessel would therefore
be accepted and round-tripped by `to_config()`. It would also be silently
ignored, because the loader reads the marker from the class and never from the
instance. That is a valid-looking configuration control that does nothing,
which is worse than a refusal.

> **C6a.** The loader **rejects** a `design.accepts_configured_ports` key on
> any equipment item, with the error at
> `$.equipment[<i>].design.accepts_configured_ports`. The error says the name is
> an internal capability marker, not a design parameter, and that a device's
> port structure cannot be set through `design`.

The check lives in `_apply_design`, keyed on the marker's name, and runs before
the `hasattr` lookup. It therefore gives the same specific refusal for every
device type, including those with no such attribute, which would otherwise get
a generic *"has no such attribute"* error. A rejected key is never set and
never round-trips.

**The registry contract.** The sweeps in `tests/test_equipment_contract.py` and
`tests/test_registry.py` build every registered class with no arguments and
require at least one port. The opt-in does not change construction: `Vessel()`
still has `inlet` and `outlet`, so the contract holds unchanged.

**A vessel never has zero ports.** Only the loader replaces a port set, and only
in configured-port mode. That mode is entered only when at least one entry
carries `direction`, and the schema already requires `ports` to have at least
one property (`minProperties: 1`). A configured item that fails validation
never reaches the build pass, because the loader raises first.

### C.7 — what the loader does in configured-port mode

**Design pass** (`_apply_design`): `design.accepts_configured_ports` is refused
at its path before any attribute lookup (C6a).

**Reference pass** (`_reference_errors` → `_named_reference_errors`, which gains
the resolved device class as an argument):

1. `_port_declarations` parses `direction` into `PortDeclaration.direction`
   (`str | None`, default `None`), validating the value.
2. If any entry carries `direction`:
   - if the class does not opt in, report each such entry;
   - otherwise report each entry that lacks one.

   An unknown `type` is already reported, and the mode check is skipped for
   that item.

**Build pass** (`_build_named`), when every declaration carries a direction:

1. `device.ports.clear()`, then `device.add_port(name, declaration.direction)`
   for each declaration in config order.
2. From there, the existing code runs unchanged:
   - the "has no port" and "not wired" checks pass by construction;
   - `Port.declare` places `phase`, `purpose` and `control`;
   - `paths` are validated and built;
   - unclaimed ports are connected.

**`paths` rules are unchanged.** A path must still start at an inlet and end at
an outlet, checked against the configured directions, and `Vessel` configs
keep using `paths: []`.

**`to_config()`**: `_port_config` adds `"direction": port.direction` to a typed
entry whose declaration carried a direction, placed immediately after `node`.
The key's presence comes from the declaration, and its value from the live
`Port`. That is the same form-versus-value split the method already uses.

`app/plant/loader.py` is the only production module that changes behaviour.
`app/equipment/vessel.py` gains the `ClassVar` and a docstring paragraph.
`config/schema/plant.schema.json` gains description text only, because the
validator implements no `oneOf` (finding 2.6).

### C.8 — reset preserves the configured set

`PRESERVED_ON_RESET` in `app/equipment/base.py` is `("ports",
"_construction_state")`. `Equipment.reset()` skips those names when it clears
`__dict__`, and `_snapshot` excludes them from the construction state. The
port dictionary the loader filled therefore survives `reset()` untouched: the
same names, the same order, and the same `direction`, `phase`, `purpose`,
`control` and `node` on each port. This was verified by reading
`Equipment.reset` and `_snapshot`. No change is needed, and T5-7 adds a test
that asserts it.

### C.9 — determinism

`device.ports` is built in config order, and `app/engine/coupling.py` already
iterates `device.ports.values()`. `_exchanges` groups nodes in first-appearance
port order, so every aggregate is summed in an order fixed entirely by the
config. That makes a run bit-identical across loads of the same file.

Reordering a config's `ports` entries may change the last bit of a sum. That
is not a defect: order is part of the configuration, and `to_config()`
preserves it.

### C.10 — T5-7 owns the loader and leaves C1 alone

**Files.**
- Code and schema: `app/equipment/vessel.py`, `app/plant/loader.py`,
  `config/schema/plant.schema.json` (description text only).
- Tests: `tests/test_typed_ports.py`, `tests/test_vessel.py`,
  `tests/test_coupling_aggregation.py`.
- Documents: `CLAUDE.md`, `docs/ADR_0002_TYPED_PORTS.md` (status row only),
  `docs/ARCHITECTURE.md`, `docs/PROJECT_STATE.md`, `docs/BUILD_PLAN.html` and
  `docs/BUILD_PLAN_STATUS.json`.

**Loader ownership is stated in T5-7's build-plan note**, not by adding
`loader.py` to `CLAUDE.md`'s file-ownership table. T5-7 holds exclusive
ownership of `app/plant/loader.py` while it is open. The loader is not a spine
file, and a permanent table entry would overstate a rule that lasts only for
one task.

**`app/equipment/base.py` stays untouched.** `add_port` exists, `ports` is
public C1 state, and `reset()` already preserves ports. If implementation seems
to need a C1 change, that is an escalation; T5-7 does not take the spine lock.
`app/engine/coupling.py` is also untouched. The coupling already reads whatever
ports a device has, in order.

**The C3 contract text is amended additively.** Both the build plan's C3 block
and the C3 row of `CLAUDE.md` name configured-port mode as a ruling that T5-7
implements.

### C.11 — T5-7 acceptance, and how the test rewrite is checked

- Existing `Vessel` behaviour is unchanged, and every existing vessel, loader,
  typed-port and coupling test stays green.
- **A production-configured `Vessel` reproduces the T5-6 shared-node result**:
  361.15756 GPM, level 0.15217391 and 113.04348 psia. It uses the existing
  approximate-stability assertions (`rel=1e-6`) and makes no bit-stability
  claim, because the solver's tolerance band makes that steady state a bounded
  sawtooth.
- **The test-only `Separator` subclasses are removed.** Each one is replaced by
  the production `Vessel` with `direction` added to the same entries:

  | Removed subclass | Configured ports |
  |---|---|
  | `TwinVaporOutlets` | `vapor_a` outlet, `vapor_b` outlet |
  | `FedAndVapors` | `feed` inlet, `vapor_a` outlet, `vapor_b` outlet |
  | `FedAndDrained` | `feed` inlet, `drain` outlet |
  | `DrainOnly` | `drain` outlet |
  | `FeedOnly` | `feed` inlet |
  | `TwinDrains` | `drain_a` outlet, `drain_b` outlet |
  | `Separator` | none (base class) |

  `MysteryDevice` is not a vessel and stays. `SeparatorDouble` in
  `tests/test_typed_ports.py` is a fixed-port `Equipment` double, not a
  vessel, and stays too.
- **The collection count is calculated from `pytest --collect-only`, never
  hand-adjusted.** On `e028280`, each of the seven removed classes contributes
  9 items to the contract sweep and 1 to the registry sweep. They are in both
  sweeps only because `test_coupling_aggregation.py` is imported before the
  sweep modules collect. Removing them takes 70 items away. T5-7 reports the
  measured total and splits it into that reduction and the tests it added. The
  `PROJECT_STATE` count table is regenerated from the measurement.
- **The mutation checks are re-run after the rewrite.** Retiring the subclasses
  is the moment these checks could silently stop biting. Each mutation is
  applied temporarily to `app/engine/coupling.py` in the worktree, then
  reverted. `tests/test_coupling_aggregation.py` must fail on each of:
  - naive per-port summing;
  - net-only node aggregation;
  - counting a node once per port.

  The PR records the outcome.
- Fixed-port equipment given `direction` is rejected, naming the path.
- `design.accepts_configured_ports` is rejected at
  `$.equipment[<i>].design.accepts_configured_ports`, with either `true` or
  `false`, on a `Vessel` and on a fixed-port device alike. It is never set on
  the instance and never round-trips (C6a).
- Mixed configured and fixed entries are rejected, naming every offending path.
  So are a bare string in configured mode and an unknown `direction` value.
- `reset()` preserves the configured port set, its order and its descriptors.
- `tests/test_port_name_guard.py` passes. No port name drives behaviour.
- A fixed-port config round-trips without gaining `direction`. A configured
  config round-trips with `direction` and order intact.
- No golden trace moves.

### C.12 — T5-5 corrected: three vessel ports, not four

T5-5's merge criterion said V-101 carries *"a process vapor outlet alongside a
separate pressure-control outlet"*. That wording is withdrawn. D3.8 settles it:

> One node per phase. Every device exchanging that phase with the vessel takes a
> branch on that node. The vessel takes one typed port per (phase, direction).

K-101 and PV-101 are **two branches on the one vapor node**, not two vessel
nozzles. D3.3's aggregation of several branches on a node already covers them,
and B.3 counts the node once. V-101 therefore takes **three** typed ports:

- a liquid inlet and a liquid outlet, both on its one liquid node (B.3 Case 1,
  exactly T5-6's `shared_liquid_node` fixture: feed and drain on `N-102`);
- one vapor outlet on its one vapor node.

The shared liquid node is **not a design value T5-5 chooses**. It follows from
D3.8's "one node per phase". Every design value is still T5-5's to choose and
justify: node pressures, sizes, capacities, valve positions and head. T5-5
uses the production `Vessel` in configured-port mode and writes no test-only
subclass.

### C.13 — what Amendment 3 does not reopen

These are unchanged:
- the typed-port vocabulary and its meanings (A.1);
- `control` is not a controller (A.2);
- conservation is `phase` + `direction` only (A.3), and the four sums (A.4 as
  amended by Amendment 2);
- node accounting, gross components, per-node writes, and `DOMAIN_UNITS`
  retired (B.1–B.11);
- phase-first classification;
- the untyped legacy string and form-preserving round trip (A.6, apart from
  its second sentence);
- what `Port` may carry (A.7);
- manual PV-101 and LV-101 in T5-5, with M8 owning control (D7);
- no composition (D6);
- the placement of T7-5.

§7.1 and §7.2 are still not fixed here, and must not be folded in.

After this amendment merges, T5-7 is implemented on Sonnet against C.4–C.11.
T5-5 then runs against the corrected specification, also on Sonnet.
