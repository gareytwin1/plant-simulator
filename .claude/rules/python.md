---
paths:
  - "app/**/*.py"
---

# Python style and typing

Production code only — test style and conventions are
[.claude/rules/testing.md](testing.md), scoped separately so the two don't
co-fire and repeat each other.

## Style

Match the surrounding code:

- 4-space indent, no docstrings on equipment methods.
- Multi-line call formatting with trailing commas.
- Blank lines between logical blocks.
- Comments are rare — naming carries the explanation. Module-level docstrings
  explaining *why* a module exists are the exception and are welcome on spine
  files.

## Typing

**Type hints are required in new and modified production code under `app/`.**
This replaced the project's earlier "no type hints" rule: the interfaces the
solver milestones build against are worth stating explicitly, and they were
brought under a checker before M4 grew the architectural surface further.

- Public functions, methods, constructors and return values are typed.
  `-> None` counts.
- Type the attributes that carry the interface — `Equipment.tag`,
  `Equipment.ports`, a collection that holds devices. Not every attribute.
- Prefer Python 3.12 built-ins and unions: `list[str]`, `dict[str, float]`,
  `str | None`. Never `typing.List` or `Optional`.
- Do not annotate obvious locals to raise coverage. Annotate one only where
  inference genuinely needs help, e.g. `errors: list[str] = []`.
- `Any` needs a reason stated beside it. Decoded JSON of a shape nothing knows
  yet is a reason; silencing the checker is not.
- The JSON-safe row every `get_state()` returns is `StateRow` in
  `app/statetypes.py`. Use it rather than a hand-rolled dict type — a dict
  return type is invariant, so a device narrowing its row to
  `dict[str, float]` would not be a valid override.
- Tests may stay lightly typed; annotate one only where it makes the test
  clearer. `mypy` is not configured over `tests/`.
- New code passes `python -m mypy` before review. The configuration lives in
  `pyproject.toml` — do not loosen it to land a change; propose a change to it
  as its own task.

Existing production code is typed; new modules join the checked scope by
default. `app/config.py` carries no annotations because its constants infer
exactly.
