# Unit Convention

This document freezes the dimensional units for all numerical values in the simulator. Once a model constant is written, its units cannot change without a data migration. This convention applies to all equipment models, the solver, and the database of scenarios and envelopes.

## Absolute rules

1. **Every numeric constant in equipment models is dimensioned.** No unit means the constant is wrong.
2. **Every state variable carries its unit in code comments** or in docstring, readable at a glance.
3. **No unit conversions occur inside equipment models or the solver.** All inputs and outputs use the same unit system.
4. **Boundary conditions (external pressures, supply conditions) respect the same units** as the plant state they couple to.

## Reference system

### Pressure

**Unit: psia (absolute pressure, pounds per square inch)**

- All pressures in equipment state, solver inputs/outputs, and boundary conditions are in psia.
- Why psia: Absolute pressure is needed for thermodynamic calculations (temperature rise from compression, vapor pressure checks, compressibility). Gauge pressure would require tracking atmospheric reference.
- Range in simulator: 50–1000 psia across all equipment.
- Historical reference: API 610 (centrifugal pump) and API 617 (gas compressor) specifications use psia.

### Temperature

**Unit: °F (Fahrenheit)**

- All temperatures are in degrees Fahrenheit.
- Why Fahrenheit: US industrial process control convention. Equipment manuals (compressors, exchangers, furnaces) specify operating ranges and limits in °F.
- Range in simulator: 50–300°F.
- Note: Thermodynamic properties inside the solver may convert to Rankine (°F + 459.67) for ratio calculations, but state variables report °F.

### Flow

**Unit: Equipment-specific, normalized at the model boundary**

Each equipment type has a characteristic flow dimension:
- **Compressor (gas):** SCFM (standard cubic feet per minute at 60°F, 1 atm)
- **Pump (liquid):** GPM (US gallons per minute)
- **Heat exchanger duty:** BTU/hr
- **Pipe/restriction:** Flow variable is whatever enters it; resistance coefficients are dimensioned accordingly.

**Normalization rule:** Inside equipment models, flow is used only in pressure-drop correlations of the form Δp = R·Q². The resistance coefficient R is dimensioned such that the product is always in psia:
- Gas: Δp(psia) = R(psia·min²/ft⁶) × Q²(SCFM²) → R is order 0.001–0.01
- Liquid: Δp(psia) = R(psia·min²/gal²) × Q²(GPM²) → R is order 0.00001–0.0001

Do not introduce a "normalized flow" [0–1] separate from the physical units. Actual flow in the equipment's native unit is the canonical state variable.

**Equipment-domain flow and connected networks.** A device's
`characteristic(flow)` takes flow in that equipment's own process-domain unit —
SCFM for the gas compressor, GPM for the centrifugal pump — and always returns
Δp in **psia**. Pressure is the one unit shared across every device, which is
what lets the solver compare them at all.

Flow is *not* shared across process domains. A connected hydraulic network may
only solve over branches whose flows are in a compatible domain: a liquid line
in GPM, a gas line in SCFM. Where the V1 train crosses phase — liquid through
P-101, gas through K-101, with the V-101 separator between them — those are
**separate hydraulic problems** coupled through the vessel's inventory, not one
network with a single flow variable.

No cross-phase flow conversion exists, and none should be invented to make the
solver or this document simpler. When T4-1/T4-2 define the branch
characteristic and the network solver, the correct treatment is for the solver
to work per process domain and for the coupling to happen through mass balance
at the vessel.

### Dimensionless

**Load, speed, valve position: [0, 1]**

These are normalized control or state variables:
- `load` — fraction of maximum compressor load (0 = idle, 1 = full load)
- `speed` — fraction of maximum pump speed (0 = off, 1 = 100% speed)
- `valve_position` — fraction of valve opening (0.10 = min safe opening, 1.0 = fully open)

These are NOT pressures or flows; they are pure numbers. Their meaning is defined only within the equipment's characteristic curve.

**Resistance coefficients:**

Resistance in pressure-drop correlations is always dimensioned such that Δp = R·Q² gives pressure in psia. The exact form depends on the flow unit:

```python
# Gas compressor: Q in SCFM, R in psia·min²/ft⁶
suction_pressure_drop = suction_resistance * flow ** 2

# Liquid pump: Q in GPM, R in psia·min²/gal²
suction_pressure_drop = suction_resistance * flow ** 2
```

### Time

**Unit: seconds**

- `dt` in `step(dt)` is in seconds.
- All rates are "per second" (e.g., `LOAD_RATE_PER_SECOND = 0.05` means load changes by 0.05 per second).
- Simulation time `sim_time` accumulated in the engine is in seconds since scenario start.

### Other quantities

| Quantity | Unit | Notes |
|---|---|---|
| Pressure rise, spread | psia | Δp = discharge - suction |
| Pressure drop (valve, pipe) | psia | Negative of rise; always ≥ 0 for consumption |
| Flow velocity | ft/s (gas), ft/min (liquid) | Used in property lookups, not stored in state |
| Compressibility factor Z | dimensionless | Z < 1 for real gases; Z = 1 for ideal |
| Isentropic exponent k | dimensionless | ≈ 1.4 for air |
| Dynamic viscosity | cP (centipoise) | Used in friction correlations; not in state |

## Model structure and state ownership

Equipment models implement the C1 contract (`app/equipment/base.py`). Units
attach to each category of state, and which *component* owns that state matters
as much as its unit — see [ARCHITECTURE.md](ARCHITECTURE.md) for the full
ownership table.

```python
class GasCompressor(Equipment):
    def __init__(self, tag="K-101"):
        super().__init__(
            tag,
            ports={
                "suction": INLET,      # wiring only — a Port never carries
                "discharge": OUTLET,   # a pressure or a flow
            },
        )

        # Actuator / slow state — advanced ONLY by integrate(dt)
        self.load = 0.0                        # [0, 1]
        self.load_target = 0.0                 # [0, 1]
        self.load_rate = config.LOAD_RATE_PER_SECOND   # per second

        # Performance curve
        self.shutoff_pressure_rise = 220.0     # psia at zero flow
        self.max_flow = 120.0                  # SCFM (gas) or GPM (liquid)

        # Resistance coefficients (dimensioned: Δp = R·Q²)
        self.suction_resistance = 0.0075       # psia·min²/ft⁶ or psia·min²/gal²

        # Temperature
        self.base_temperature = 75.0           # °F
        self.max_temperature = 120.0           # °F

    def characteristic(self, flow):
        """Pure: pressure change across the device at this flow, in psia.
        Positive = rise (machine), negative = drop (valve, pipe)."""
        return max(
            self.shutoff_pressure_rise * self.load ** 2
            - self.compressor_resistance * flow ** 2,
            0.0,
        )

    def get_state(self):
        return {
            "pressure": self.discharge_pressure,   # psia
            "flow": self.flow,                     # SCFM or GPM
            "temperature": self.temperature,       # °F
            "load": self.load,                     # [0, 1]
        }
```

### Boundary pressures are not device state

Before the C1 refactor, devices owned `supply_pressure` and
`discharge_header_pressure` and solved their own operating point against them.
**That ownership model is retired.** A pressure at a point in the plant is a
solver output; it belongs to a `Node` in the topology
(`app/plant/topology.py`), not to a device.

Both devices currently carry interim `upstream_boundary_pressure` /
`downstream_boundary_pressure` attributes, in psia, feeding only the legacy
standalone `step()` path. These are **temporary** and are retired by T4-2 when
the network solver takes over. Do not add new device attributes that hold a
plant pressure, and do not treat the interim ones as the pattern to copy.

In the target model:

- Node pressures — **psia**, owned by `Node`, written only by the solver.
- Stream flow — the branch's process-domain flow unit, owned by `Stream`,
  written only by the solver.
- A device publishes `characteristic(flow) -> Δp in psia` and nothing else about
  the plant's state.

## Testing and validation

When writing tests, use `pytest.approx` for float comparisons:

```python
assert compressor.temperature == pytest.approx(88.5, abs=0.1)  # °F
assert compressor.discharge_pressure == pytest.approx(750.3, abs=0.5)  # psia
```

Tolerance is chosen to match the precision of physical measurements, not machine epsilon.

## Migration path

If a future refactor needs to change units (unlikely but possible):

1. Add a new equipment constant with a `_v2` suffix, do NOT reuse the old name.
2. Write a conversion layer that translates the state dict on I/O.
3. Bump the snapshot schema version in C4.
4. Update this document with a "versions" section.

**Never silently change units; the breakage must be visible in the code.**

## References

- **Equipment contract C1:** [app/equipment/base.py](../app/equipment/base.py) — `characteristic(flow)` returns Δp in psia; `integrate(dt)` takes dt in seconds.
- **Equipment models:** [app/equipment/compressor.py](../app/equipment/compressor.py), [app/equipment/pump.py](../app/equipment/pump.py)
- **Topology C2:** [app/plant/topology.py](../app/plant/topology.py) — `Node.pressure` in psia, `Stream.flow` in the branch's process-domain unit. These are solver-owned.
- **State snapshot C4:** [app/engine/snapshot.py](../app/engine/snapshot.py) — state dicts are JSON and carry no unit information, which is exactly why this convention must stay frozen.
- **Configuration schema C3:** [config/schema/plant.schema.json](../config/schema/plant.schema.json) — all numeric values in plant config respect these units.
- **State ownership:** [ARCHITECTURE.md](ARCHITECTURE.md)

---

**Units last frozen:** 18 September 2026 (T0-4, merged as `e1430f4`). The unit
system itself is unchanged since then and is **not** up for casual revision —
see the migration path above.

**Document last reviewed:** 19 September 2026, after T1-4 merged. This revision
replaced the pre-C1 examples (`supply_pressure`, `discharge_header_pressure`
owned by a device) with the current contract-based ownership model, and added
the equipment-domain flow / connected-network note. No unit values changed.

**Next review:** when **T4-1/T4-2** land. The network solver is the first thing
that reads flows across branches, so it is the point at which the
process-domain rule above stops being advisory and starts being enforced by
code. Any equipment added before then must respect these units exactly.
