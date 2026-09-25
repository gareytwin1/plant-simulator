---
paths:
  - "app/plant/**/*.py"
  - "app/equipment/vessel.py"
  - "config/schema/**"
  - "config/plants/**"
---

# Typed ports and plant configuration

AGENTS.md states the invariant this file implements: port names never drive
behaviour, and only `phase` and `direction` classify a connection. This file
carries the mechanics.

## The four descriptors

```text
name       identifier only, never behaviour
direction  inlet | outlet
phase      liquid | vapor          (mixed is reserved and refused at load)
purpose    process | vent | drain | relief
control    flow | pressure | level | temperature, or absent
```

`purpose` and `control` are **orthogonal**: a level-controlled drain declares
both, a manual drain declares only `purpose`. Never collapse them back into one
field, and never branch code on a port's `name` —
`tests/test_port_name_guard.py` fails the build if any does.

A C3 `ports` entry may be a node-id string **or** a typed object
`{node, phase, purpose, control?}`. There is no half-typed form; the
alternation is a **loader** check (the C3 validator ignores `oneOf`), naming
paths down to `$.equipment[2].ports.pressure_out.phase`. `to_config()` is
form-preserving per entry — a string returns a string, a typed object returns
typed, sugar stays sugar. A legacy config is never silently upgraded, and
typed and untyped entries may sit in one `ports` map.

## Fixed ports vs. configured ports (ADR 0002 Amendment 3)

**Fixed-port equipment** — every device except `Vessel` — declares its ports
by name and direction in its class, and the loader puts the C3 descriptors
onto the runtime `Port`. An `isinstance(device, ...)` check or a port-name test
deciding a phase is the inference T3-7 exists to retire.

**Configured-port mode** is an opt-in a device class declares through a
class-level `accepts_configured_ports: ClassVar[bool] = True` marker — only
`Vessel` does. In that mode:

- A `ports` map whose **every** entry carries `direction` (a typed entry)
  replaces the device's default port set, in config order.
- No `direction` anywhere keeps the fixed ports, exactly as before.
- `direction` on only some entries, or on fixed-port equipment, is rejected,
  naming every offending path.
- `reset()` preserves the configured set; `to_config()` round-trips it
  (`direction` appears only where the loader put it there).

**`accepts_configured_ports` is an internal capability marker, never a design
value.** `design.accepts_configured_ports` is rejected on *any* device — ahead
of and independent from the generic `hasattr` check — because it would
otherwise be silently-ignored-but-valid-looking configuration.

## What consumes this vocabulary

T3-7 built the vocabulary; **the coupling (`app/engine/coupling.py`, T5-6)
consumes it** — classifying a connection from its declared `phase` and
aggregating each hydraulic node exactly once, never once per port. See
[ARCHITECTURE.md §2](../../docs/ARCHITECTURE.md) for the aggregation rules
themselves (node-as-unit-of-account, `FLOW_UNITS`) — they live there, not
here, because they are coupling behaviour rather than configuration shape.
`purpose` and `control` are read nowhere on that path.

Full ADR text: [ADR_0002_TYPED_PORTS.md](../../docs/ADR_0002_TYPED_PORTS.md),
Amendment 1 (the four descriptors), Amendment 2 (node-based aggregation),
Amendment 3 (configured-port mode, this file).
