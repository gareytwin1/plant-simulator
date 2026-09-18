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

## Implicit model structure

Equipment models use the following pattern:

```python
class Equipment:
    def __init__(self):
        # Boundary conditions (psia)
        self.supply_pressure = 750.0  # psia
        self.discharge_header_pressure = 750.0  # psia
        
        # Actuator state (dimensionless [0, 1] or normalized)
        self.load = 0.0  # [0, 1]
        self.load_target = 0.0  # [0, 1]
        
        # Performance curves (psia for shutoff, SCFM/GPM for max flow)
        self.shutoff_pressure_rise = 220.0  # psia at zero flow
        self.max_flow = 120.0  # SCFM (gas) or GPM (liquid)
        
        # Resistance coefficients (dimensioned: Δp = R·Q²)
        self.suction_resistance = 0.0075  # psia·min²/ft⁶ or psia·min²/gal²
        
        # Temperature (°F)
        self.base_temperature = 75.0  # °F
        self.max_temperature = 120.0  # °F
    
    def get_state(self):
        return {
            "pressure": self.discharge_pressure,  # psia
            "flow": self.flow,  # SCFM or GPM
            "temperature": self.temperature,  # °F
            "load": self.load,  # [0, 1]
        }
```

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

- **Equipment models:** [app/equipment/compressor.py](../app/equipment/compressor.py), [app/equipment/pump.py](../app/equipment/pump.py)
- **Interface contract C4 (State snapshot):** Defined in the build plan; state dicts are JSON and carry no unit information, so unit must be frozen before the snapshot is finalized.
- **Configuration schema C3:** Plant config references equipment design parameters; all numeric values in the config respect the same units.

---

**Last frozen:** 18 September 2026 (T0-4 merged as commit 55bd80d)

**Next review:** Before T1-2 (Equipment base class) merges; any equipment added after this date must respect these units exactly.
