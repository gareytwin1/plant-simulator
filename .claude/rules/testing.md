---
paths:
  - "tests/**/*.py"
  - "tests/fixtures/golden/*.json"
---

# Writing tests in this repository

## Golden regressions

`tests/fixtures/golden/*.json` pin current physics. Tolerances (1e-5 relative,
1e-7 absolute) were chosen deliberately: ten orders above float noise, three
below the 1% drift the harness exists to catch.

**Do not regenerate a golden trace to make a test pass.** If a trace moves, stop
and explain why. A moved trace means behaviour changed — either that was the
point of the task and it needs justifying in the PR, or you have a bug.
Regenerating is legitimate only for non-numeric changes, such as a recorded
command-description string that names a renamed attribute.

## Style

- Flat `def test_*` functions. No classes.
- `pytest.approx` for **every** float comparison.
- Name the test for the behaviour it pins, not the function it calls.
- Tests are outside the `mypy` scope and stay lightly typed — annotate only
  where it makes the test clearer.

## Traps this suite has already fallen into

These are recorded because a session rediscovered each one the expensive way.

**Backflow hides inside a "flow rises" assertion.** On the reference gas plant
(60 → 480 psia), raising `K-101`'s load 0.8 → 0.9 → 1.0 gives −121.7, −73.8,
+70.7 SCFM. Flow does rise at every step, so a naive assertion passes while the
machine is running backwards. **Assert `flow > 0` in every compared state**, and
pick a boundary difference the machines clear — 60 → 300 psia gives forward flow
on the series gas plant from about 0.75 load.

**Assert only on settled states.** Load ramps at 0.05 per second, so a mid-ramp
state can sit in the backflow regime and an assertion written against it is
pinning a transient.

**Do not assert an idle flow of exactly zero.** A machine that has never run
reads exactly `0.0`, but a *stopped* one settles just off zero and stays there
(0.055 GPM pump, 0.004 SCFM compressor). At shutoff the branch curve is flat, so
the root is a double root and the solver's psia tolerance maps to
`sqrt(tolerance / resistance)` of flow. Assert against a bound **derived** from
the tolerance, never a recorded residual value — the value inside that bound is
path-dependent.

**Do not shorten a horizon to make a conservation test pass.** Stopping a run
before a level clamps is a test-horizon trick standing in for missing physics.
ADR 0002 §8.2 re-sequenced an entire task to remove exactly this.

**Assert a pressure asymptote rather than a final flow** near zero vapour flow.
Explicit Euler limit-cycles there (±0.0615 SCFM, ±7.5e-6 psi, bounded), so a
final-flow assertion is phase-dependent and will flake.

## The dynamic sweep

`tests/test_equipment_contract.py` and `tests/test_registry.py` discover
`Equipment` subclasses dynamically, so **adding one device class raises the
total by far more than the tests you wrote** — and removing one drops it the
same way. Re-measure with `python -m pytest --collect-only -q`; never adjust a
test count by hand.

`REGISTERED` in `test_equipment_contract.py` is computed at import time, so a
subclass defined in a later-imported test module silently escapes the
parametrized contract tests. Known; passes either way today.

## Before review

Run the **full** suite and the type check, not just what you added:

```bash
conda activate plant-simulator
python -m pytest -q && python -m mypy
```
