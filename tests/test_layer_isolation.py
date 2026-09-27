"""
Layer isolation guard (T13-5, contract C8's structural half).

`app/disturbances/malfunction.py`'s `WRITABLE` allowlist already enforces, at
runtime, that a malfunction can only move a parameter an engineer could
change - never a solver output, never the slow state `integrate` owns. That
half is tested end to end in `tests/test_malfunction.py`; this file does not
repeat it.

What is still open is the *direction* of the dependency. Nothing stops a
physics module from importing the disturbance layer back - reaching into a
`MalfunctionRegistry` or calling something the allowlist was built to guard
against from the other side. The rule is "physics never imports disturbances
(or scoring)", and it is easy to violate by accident once a device or the
engine wants to know whether it is currently faulted. Two checks enforce it:

1. **Import direction** - no module outside `app/disturbances` imports
   `app.disturbances` or `app.scoring`, absolute or relative. `scoring` does
   not exist yet (M15+), but the same rule applies the moment it does, so it
   is excluded from the guarded set pre-emptively - the same way
   `app/disturbances` is - rather than left to be remembered later.
2. **Allowlist vs. slow state** - derived structurally rather than
   hand-listed: for every device class in `WRITABLE`, none of its allowlisted
   parameters are among the attributes that class's own `integrate()` writes.
   `integrate` is the only method allowed to move slow state (AGENTS.md); a
   name it writes and `WRITABLE` also lists would be a malfunction reaching
   state through the front door that the allowlist was supposed to keep it
   away from.

Written in the style of tests/test_random_source_guard.py and
tests/test_port_name_guard.py.
"""

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from app.disturbances.malfunction import WRITABLE
from app.equipment.base import Equipment


APP = Path(__file__).resolve().parent.parent / "app"

# Excluded from PHYSICS_MODULES the same way DISTURBANCE_LAYER is: once
# app/scoring exists, its own internal imports would otherwise mention
# "scoring" and trip the guard on themselves.
DISTURBANCE_LAYER = APP / "disturbances"
SCORING_LAYER = APP / "scoring"

FORBIDDEN_LAYERS = frozenset({"disturbances", "scoring"})

APP_MODULES = sorted(APP.rglob("*.py"))
PHYSICS_MODULES = [
    path
    for path in APP_MODULES
    if DISTURBANCE_LAYER not in path.parents and SCORING_LAYER not in path.parents
]


def enclosing_package(path):
    """The dotted package a relative import in `path` resolves against.

    Mirrors Python's own `__package__` rule: drop the file's own component
    (the module name, or `__init__` for a package). Both a regular module and
    its package's `__init__.py` land on the same directory either way, which
    is exactly right - a package's `__package__` equals its own dotted name,
    not its parent's.
    """
    parts = list(path.relative_to(APP.parent).with_suffix("").parts)

    return parts[:-1]


def source_imports_forbidden_layer(source, package_parts):
    """Does `source` (the text of a module in package `package_parts`) reach a
    forbidden layer - by an absolute import, or a relative one resolved
    against its own package?
    """
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if FORBIDDEN_LAYERS & set(alias.name.split(".")):
                    return True

        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = []
            else:
                # `from . import x` (level 1) resolves against this module's
                # own package; each extra level climbs one package further up.
                climb = node.level - 1
                base = (
                    package_parts[: len(package_parts) - climb]
                    if climb <= len(package_parts)
                    else []
                )

            module = (node.module or "").split(".") if node.module else []
            target = [*base, *module]

            if FORBIDDEN_LAYERS & set(target):
                return True

            if any(FORBIDDEN_LAYERS & set([*base, alias.name]) for alias in node.names):
                return True

    return False


def imports_forbidden_layer(path):
    source = path.read_text()
    has_relative_import = any(
        isinstance(node, ast.ImportFrom) and node.level > 0
        for node in ast.walk(ast.parse(source))
    )
    # Only resolved when actually needed: a synthetic module built for a test
    # (outside app/, all-absolute imports) has no package of its own to climb
    # from, and none of these guarded modules use a relative import today.
    package_parts = enclosing_package(path) if has_relative_import else []

    return source_imports_forbidden_layer(source, package_parts)


def test_the_guard_sees_the_modules_it_is_guarding():
    assert APP / "equipment" / "base.py" in PHYSICS_MODULES
    assert DISTURBANCE_LAYER / "malfunction.py" in APP_MODULES
    assert DISTURBANCE_LAYER / "malfunction.py" not in PHYSICS_MODULES
    assert len(PHYSICS_MODULES) > 10


def test_the_disturbance_layer_is_the_one_that_imports_physics():
    # The dependency runs one way: malfunction.py reaches into equipment and
    # the engine snapshot to do its job. That is the allowed direction, and
    # it is what makes the other direction worth guarding.
    assert not imports_forbidden_layer(DISTURBANCE_LAYER / "malfunction.py")


@pytest.mark.parametrize(
    "path",
    PHYSICS_MODULES,
    ids=lambda path: str(path.relative_to(APP)),
)
def test_no_physics_module_imports_the_disturbance_layer(path):
    assert not imports_forbidden_layer(path), (
        f"{path.relative_to(APP)} imports the disturbance (or scoring) layer "
        f"— physics must never depend on it; a malfunction reaches a device "
        f"only through the allowlist in app/disturbances/malfunction.py "
        f"(WRITABLE), never the other way around"
    )


def test_the_guard_catches_each_way_of_importing_the_forbidden_layer(tmp_path):
    for source in (
        "import app.disturbances.malfunction",
        "import app.disturbances.malfunction as mal",
        "from app.disturbances.malfunction import Malfunction",
        "from app.disturbances import malfunction",
        "from app import disturbances",
        "from app import scoring as s",
        "import app.scoring.board",
    ):
        module = tmp_path / "m.py"
        module.write_text(source)

        assert imports_forbidden_layer(module), source

    module = tmp_path / "clean.py"
    module.write_text(
        "from app.equipment.base import Equipment\n"
        "import app.engine.snapshot\n"
        "from app.envelope.evaluator import Evaluator\n",
    )

    assert not imports_forbidden_layer(module)


@pytest.mark.parametrize(
    ("source", "package_parts"),
    [
        # from app/equipment/base.py: climbs one level to app, into disturbances.
        ("from ..disturbances import malfunction", ["app", "equipment"]),
        ("from ..disturbances.malfunction import Malfunction", ["app", "equipment"]),
        ("from .. import disturbances", ["app", "equipment"]),
        # from app/disturbances/malfunction.py reaching a sibling scoring package.
        ("from ..scoring import board", ["app", "disturbances"]),
    ],
    ids=[
        "relative-from-module",
        "relative-from-submodule",
        "relative-bare-import",
        "relative-sibling-package",
    ],
)
def test_the_guard_catches_a_relative_import_of_the_forbidden_layer(source, package_parts):
    assert source_imports_forbidden_layer(source, package_parts), source


def test_a_relative_import_that_stays_inside_its_own_package_is_not_flagged():
    # from app/equipment/base.py: `from .valve import ControlValve` - a
    # perfectly ordinary same-package relative import.
    source = "from .valve import ControlValve"

    assert not source_imports_forbidden_layer(source, ["app", "equipment"])


def test_the_physics_filter_would_exclude_a_future_scoring_module():
    # Guards the fix itself: once app/scoring exists, its own internal
    # imports must not trip the guard on themselves, the same way
    # app/disturbances's already do not. `Path.parents` needs no file to
    # exist, so this is checkable before app/scoring is ever written.
    hypothetical = SCORING_LAYER / "report.py"

    assert SCORING_LAYER in hypothetical.parents
    assert DISTURBANCE_LAYER not in hypothetical.parents


# ---- the allowlist never doubles as a way to move slow state --------------


def integrate_targets(cls):
    """Attribute names `cls`'s own `integrate` assigns on `self` — its slow state."""
    method = cls.__dict__.get("integrate")

    if method is None:
        return frozenset()

    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    targets = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            assign_targets = node.targets if isinstance(node, ast.Assign) else [node.target]

            for target in assign_targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    targets.add(target.attr)

    return frozenset(targets)


EQUIPMENT_CLASSES = [
    cls for cls in WRITABLE if isinstance(cls, type) and issubclass(cls, Equipment)
]


def test_the_guard_sees_the_classes_it_is_guarding():
    assert len(EQUIPMENT_CLASSES) >= 5


@pytest.mark.parametrize("cls", EQUIPMENT_CLASSES, ids=lambda cls: cls.__name__)
def test_no_allowlisted_parameter_is_slow_state_integrate_writes(cls):
    collision = WRITABLE[cls] & integrate_targets(cls)

    assert not collision, (
        f"{cls.__name__} allowlists {sorted(collision)} for a malfunction, but "
        f"its own integrate() also writes {sorted(collision)} - that makes it "
        f"slow state (AGENTS.md's integrate/characteristic split), which a "
        f"malfunction may never move directly"
    )


def test_the_guard_catches_a_deliberate_collision():
    class FakeDevice:
        def integrate(self, dt):
            self.speed = self.speed + dt

    assert integrate_targets(FakeDevice) == frozenset({"speed"})
