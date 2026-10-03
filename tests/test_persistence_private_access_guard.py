"""persistence.py must not reach into another class's private state.

T12-1 saved a plant by reading and writing `_primed`, `_integral`, `_band` and
a dozen other underscore attributes of six classes, so renaming one in its own
module silently broke a save. T12-5 gave each class `checkpoint()`,
`validate_checkpoint()` and `restore_checkpoint()` to own its state; this guard
keeps `persistence.py` on the public side of them.

**What it flags**: an attribute access whose name starts with a single
underscore (`engine._primed`), and an import of an underscore-prefixed name
(`from app.envelope.evaluator import _Band`). Dunders - `device.__dict__`,
`Severity.__members__` - are language protocol, not private state, and pass;
a device is saved through its `__dict__` by design (see the module docstring).

Module-level helpers of persistence.py itself are plain names, not attributes,
so they are not flagged.

Written in the style of tests/test_port_name_guard.py.
"""

import ast
from pathlib import Path

PERSISTENCE = Path(__file__).resolve().parent.parent / "app" / "engine" / "persistence.py"


def is_private(name):
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def private_reaches(source):
    tree = ast.parse(source)
    found = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and is_private(node.attr):
            found.append((node.lineno, f".{node.attr}"))

        if isinstance(node, ast.ImportFrom):
            found.extend(
                (node.lineno, f"import {alias.name}")
                for alias in node.names
                if is_private(alias.name)
            )

    return found


def test_persistence_reads_no_private_attribute_of_another_class():
    assert private_reaches(PERSISTENCE.read_text()) == []


def test_the_guard_flags_a_private_attribute_read_and_write():
    assert private_reaches("engine._primed.add(tag)") == [(1, "._primed")]
    assert private_reaches("loop._entering = True") == [(1, "._entering")]


def test_the_guard_flags_a_private_import():
    assert private_reaches("from app.envelope.evaluator import Limits, _Band") == [
        (1, "import _Band"),
    ]


def test_the_guard_passes_dunders_and_plain_names():
    source = "rows = device.__dict__\nkinds = Severity.__members__\nok = _helper(x)"

    assert private_reaches(source) == []
