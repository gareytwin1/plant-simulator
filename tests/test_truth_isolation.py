"""
No consumer accidentally reads true - T13-2.

`Snapshot.truth` is what the physics says; every other reading in a snapshot
is what the instruments indicate. A controller, alarm, envelope or console
that read truth would be immune to instrument faults, and the hidden cause
the split exists for would stop being hidden. So nothing in app/ may reach
for truth unless it is listed here, deliberately, as a consumer entitled to
know what really happened - scoring and the debrief, when they land.

Three ways in are guarded: the attribute (`snapshot.truth`), its name as a
string (`getattr(snapshot, "truth")`), and `dataclasses.asdict`/`astuple`,
which would carry truth out with everything else. `Snapshot.as_dict()` is the
sanctioned serialisation and carries the indicated view only.
"""

import ast
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parent.parent / "app"

TRUTH_READERS: frozenset[Path] = frozenset()

APP_MODULES = sorted(APP.rglob("*.py"))


def truth_reads(path):
    reads = []

    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Attribute) and node.attr == "truth":
            reads.append(f"line {node.lineno}: .truth")

        if isinstance(node, ast.Constant) and node.value == "truth":
            reads.append(f"line {node.lineno}: 'truth'")

        if isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr

            if name in ("asdict", "astuple"):
                reads.append(f"line {node.lineno}: {name}")

    return reads


def test_the_guard_sees_the_modules_it_is_guarding():
    assert APP / "engine" / "snapshot.py" in APP_MODULES
    assert len(APP_MODULES) > 10
    assert TRUTH_READERS <= set(APP_MODULES)


@pytest.mark.parametrize(
    "path",
    [path for path in APP_MODULES if path not in TRUTH_READERS],
    ids=lambda path: str(path.relative_to(APP)),
)
def test_no_consumer_reads_the_truth(path):
    assert truth_reads(path) == [], (
        f"{path.relative_to(APP)} reads Snapshot.truth - a consumer reads the "
        f"indicated view, or it cannot be fooled by an instrument fault; list "
        f"it in TRUTH_READERS only if it is entitled to know what really "
        f"happened"
    )


def test_the_guard_catches_each_way_of_reading_the_truth(tmp_path):
    for source in (
        "snapshot.truth.nodes",
        "x = snapshot.truth",
        "getattr(snapshot, 'truth')",
        "vars(snapshot)['truth']",
        "from dataclasses import asdict\nasdict(snapshot)",
        "import dataclasses\ndataclasses.astuple(snapshot)",
    ):
        module = tmp_path / "m.py"
        module.write_text(source)

        assert truth_reads(module), source

    module = tmp_path / "clean.py"
    module.write_text(
        "snapshot.nodes['N-1']['pressure']\n"
        "snapshot.as_dict()\n"
        "truthful = 'the truth is out there'\n"
        "def f(truth):\n    return truth\n",
    )

    assert truth_reads(module) == []
