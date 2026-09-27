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
or scoring", and it is easy to violate by accident once a device or the
engine wants to know whether it is currently faulted. Two checks enforce it:

1. **Import direction** - no *physics* module imports `app.disturbances` or
   `app.scoring`, absolute or relative. "Physics" excludes not just those two
   layers themselves but also the orchestration layer above them -
   `app/api` (T15-1's C5 action endpoint legitimately imports
   `app.scoring.actionlog` to log what an operator did, and will eventually
   dispatch into `app.disturbances`/`app.scenarios` per C8's route list too;
   that is coordination, not physics reaching backwards) and `app/main.py`,
   the composition root. Equipment, the engine, the plant graph, controllers,
   the envelope evaluator and alarms have no legitimate reason to know either
   layer exists, and that is what stays guarded.
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

DISTURBANCE_LAYER = APP / "disturbances"
SCORING_LAYER = APP / "scoring"
API_LAYER = APP / "api"
MAIN_MODULE = APP / "main.py"

FORBIDDEN_LAYERS = frozenset({"disturbances", "scoring"})

# PHYSICS_MODULES is everything guarded against FORBIDDEN_LAYERS - which is
# not "all of app/". Excluded: the two forbidden layers' own internals (their
# own imports of each other, or of themselves, are not the violation this
# guards against), and the orchestration layer above them - app/api (T15-1's
# C5 action endpoint legitimately imports app.scoring.actionlog, and will
# eventually dispatch into app.disturbances/app.scenarios too) and
# app/main.py, the Flask composition root. Neither is physics; both exist to
# coordinate between layers, which is a different direction than physics
# reaching backwards into disturbances or scoring.
ORCHESTRATION_LAYERS = (DISTURBANCE_LAYER, SCORING_LAYER, API_LAYER)

APP_MODULES = sorted(APP.rglob("*.py"))
PHYSICS_MODULES = [
    path
    for path in APP_MODULES
    if path != MAIN_MODULE
    and not any(layer in path.parents for layer in ORCHESTRATION_LAYERS)
]


def enclosing_package(path):
    """The dotted package a relative import in `path` resolves against.

    Mirrors Python's own `__package__` rule: drop the file's own component
    (the module name, or `__init__` for a package). Both a regular module and
    its package's `__init__.py` land on the same directory either way, which
    is exactly right - a package's `__package__` equals its own dotted name,
    not its parent's.

    Requires `path` to sit under `APP.parent` (i.e. be a real module inside
    this repo's `app/` tree) - there is no package to climb from otherwise.
    """
    if APP.parent not in path.parents:
        raise ValueError(f"{path} is not under {APP.parent}, so it has no enclosing package")

    parts = list(path.relative_to(APP.parent).with_suffix("").parts)

    return parts[:-1]


def tree_imports_forbidden_layer(tree, package_parts):
    """Does the parsed module in package `package_parts` reach a forbidden
    layer - by an absolute import, or a relative one resolved against its own
    package?
    """
    for node in ast.walk(tree):
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

            module = node.module.split(".") if node.module else []
            target = [*base, *module]

            if FORBIDDEN_LAYERS & set(target):
                return True

            if any(FORBIDDEN_LAYERS & set([*base, alias.name]) for alias in node.names):
                return True

    return False


def source_imports_forbidden_layer(source, package_parts):
    return tree_imports_forbidden_layer(ast.parse(source), package_parts)


def imports_forbidden_layer(path):
    tree = ast.parse(path.read_text())
    has_relative_import = any(
        isinstance(node, ast.ImportFrom) and node.level > 0 for node in ast.walk(tree)
    )
    # Only resolved when actually needed: a synthetic module built for a test
    # (outside app/, all-absolute imports) has no package of its own to climb
    # from, and none of these guarded modules use a relative import today.
    package_parts = enclosing_package(path) if has_relative_import else []

    return tree_imports_forbidden_layer(tree, package_parts)


def test_the_guard_sees_the_modules_it_is_guarding():
    assert APP / "equipment" / "base.py" in PHYSICS_MODULES
    assert DISTURBANCE_LAYER / "malfunction.py" in APP_MODULES
    assert DISTURBANCE_LAYER / "malfunction.py" not in PHYSICS_MODULES
    assert len(PHYSICS_MODULES) > 10


def test_the_guard_excludes_the_orchestration_layer_it_does_not_guard():
    # app/api coordinates between physics and scoring/disturbances - that is
    # its job, not the violation this guard exists to catch. Same for the
    # Flask composition root.
    assert (API_LAYER / "action.py").exists()
    assert (API_LAYER / "action.py") not in PHYSICS_MODULES
    assert MAIN_MODULE not in PHYSICS_MODULES


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


def test_the_physics_filter_excludes_scorings_own_internals():
    # app/scoring's own internal imports must not trip the guard on
    # themselves, the same way app/disturbances's already do not.
    assert (SCORING_LAYER / "actionlog.py") in APP_MODULES
    assert (SCORING_LAYER / "actionlog.py") not in PHYSICS_MODULES

    # `Path.parents` needs no file to exist, so a future scoring submodule is
    # covered too, not just the one that happens to exist today.
    hypothetical = SCORING_LAYER / "report.py"

    assert SCORING_LAYER in hypothetical.parents
    assert DISTURBANCE_LAYER not in hypothetical.parents


def test_enclosing_package_refuses_a_path_outside_the_app_tree(tmp_path):
    with pytest.raises(ValueError, match="enclosing package"):
        enclosing_package(tmp_path / "m.py")


# ---- the allowlist never doubles as a way to move slow state --------------


def integrate_targets(cls):
    """Attribute names `integrate` assigns on `self` — its slow state.

    Resolved through the MRO (`cls.integrate`, not `cls.__dict__`), so a class
    that inherits `integrate` from a shared base rather than overriding it is
    still checked against the base's actual attribute writes, not silently
    skipped as if it had none.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls.integrate)))
    targets = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            if isinstance(node, ast.Assign):
                assign_targets = node.targets
            else:
                assign_targets = [node.target]

            for target in assign_targets:
                # `self.a, self.b = x, y` - a tuple/list target unpacks into
                # its own elements, each of which may itself be `self.attr`.
                elements = (
                    target.elts if isinstance(target, (ast.Tuple, ast.List)) else [target]
                )

                for element in elements:
                    if (
                        isinstance(element, ast.Attribute)
                        and isinstance(element.value, ast.Name)
                        and element.value.id == "self"
                    ):
                        targets.add(element.attr)

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


def test_the_guard_sees_a_tuple_unpacking_self_assignment():
    class FakeDevice:
        def integrate(self, dt):
            self.speed, self.load = self.speed + dt, self.load + dt

    assert integrate_targets(FakeDevice) == frozenset({"speed", "load"})


def test_the_guard_sees_an_annotated_self_assignment():
    class FakeDevice:
        def integrate(self, dt: float) -> None:
            self.speed: float = self.speed + dt

    assert integrate_targets(FakeDevice) == frozenset({"speed"})


def test_the_guard_sees_integrate_inherited_from_a_base_class():
    # Not resolvable via cls.__dict__.get("integrate") - only cls.integrate
    # (through the MRO) finds it.
    class BaseDevice:
        def integrate(self, dt):
            self.speed = self.speed + dt

    class SubDevice(BaseDevice):
        pass

    assert "integrate" not in SubDevice.__dict__
    assert integrate_targets(SubDevice) == frozenset({"speed"})
