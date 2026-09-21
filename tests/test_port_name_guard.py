"""A Port's name must not determine engineering behaviour.

T3-7 gave a connection explicit descriptors — direction, phase, purpose,
control — precisely so that nothing has to read meaning into what a port was
called. The rule only holds if it is enforced, because the tempting shortcut
(`if port.name == "drain"`) is one line and looks harmless until a plant names
its drain something else.

**What the guard flags** is a *Port's* name deciding something: comparing one
against a string literal, or string-matching one with a literal. It works out
whether a `.name` belongs to a Port from the expression it is read off —
`Port(...)`, `.port(...)`, `.from_port` / `.to_port`, a `.ports` subscript, a
loop over `.ports.values()` / `.items()`, a parameter annotated `Port`, `self`
inside `class Port`, or an identifier named `port` / `*_port`.

**What it deliberately leaves alone**, because neither is the invariant:

- *Any other object's* `name`. An alarm, a controller, a scenario, a sequence
  or a malfunction may perfectly well be identified by name, and a guard that
  outlawed `self.name == "discharge"` everywhere would fail legitimate code in
  subsystems that do not exist yet.
- *Looking a port up* by a hard-coded name — `device.port("drain")`. Using an
  identifier to reach a thing is not the same as letting that identifier decide
  engineering behaviour, and a device reaching its own declared port is
  ordinary. What would be a violation is branching on the name it finds, and
  that is caught by the rule above.

The check is structural rather than a list of suspicious words, so a
comparison against a port name nobody thought of is caught too.

Written in the style of tests/test_random_source_guard.py, and like it, it
passes today and guards forever.
"""

import ast
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parent.parent / "app"

APP_MODULES = sorted(APP.rglob("*.py"))

# Attributes that hold a Port, and the identifier convention for one.
PORT_ATTRIBUTES = ("port", "from_port", "to_port")


def named_like_a_port(identifier):
    return identifier == "port" or identifier.endswith("_port")


def annotation_mentions_port(annotation):
    if annotation is None:
        return False

    return any(
        (isinstance(node, ast.Name) and node.id == "Port")
        or (isinstance(node, ast.Attribute) and node.attr == "Port")
        for node in ast.walk(annotation)
    )


def scope_nodes(scope):
    """Every node belonging to this scope, not descending into nested ones."""
    stack = list(ast.iter_child_nodes(scope))

    while stack:
        node = stack.pop()

        yield node

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        stack.extend(ast.iter_child_nodes(node))


def is_a_port(node, bound):
    """Does this expression evaluate to a Port?"""
    if isinstance(node, ast.Name):
        return node.id in bound or named_like_a_port(node.id)

    if isinstance(node, ast.Attribute):
        return node.attr in PORT_ATTRIBUTES

    # device.ports[name]
    if isinstance(node, ast.Subscript):
        return isinstance(node.value, ast.Attribute) and node.value.attr == "ports"

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id == "Port"

        if isinstance(node.func, ast.Attribute):
            return node.func.attr == "port"

    return False


def iteration_bindings(node):
    """`for port in device.ports.values()` and its .items() form."""
    iterable = node.iter

    if not (isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Attribute)):
        return set()

    source = iterable.func.value

    if not (isinstance(source, ast.Attribute) and source.attr == "ports"):
        return set()

    target = node.target

    if iterable.func.attr == "values" and isinstance(target, ast.Name):
        return {target.id}

    if (
        iterable.func.attr == "items"
        and isinstance(target, ast.Tuple)
        and len(target.elts) == 2
        and isinstance(target.elts[1], ast.Name)
    ):
        return {target.elts[1].id}

    return set()


def port_bindings(scope, in_port_class):
    """Identifiers in this scope that hold a Port."""
    bound = set()

    if in_port_class:
        bound.add("self")

    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = scope.args

        for argument in [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ]:
            if annotation_mentions_port(argument.annotation):
                bound.add(argument.arg)

    for node in scope_nodes(scope):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if annotation_mentions_port(node.annotation):
                bound.add(node.target.id)

        elif isinstance(node, ast.Assign) and is_a_port(node.value, bound):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)

        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            bound |= iteration_bindings(node)

    return bound


def reads_a_port_name(node, bound):
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "name"
        and is_a_port(node.value, bound)
    )


def is_string_literal(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)

    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(is_string_literal(element) for element in node.elts)

    return False


def enclosing_classes(tree):
    return {
        child: node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def name_driven_behaviour(source):
    """Every place `source` lets a Port's name decide something."""
    tree = ast.parse(source)
    classes = enclosing_classes(tree)

    scopes = [tree] + [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]

    findings = []

    for scope in scopes:
        in_port_class = classes.get(scope) == "Port" or (
            isinstance(scope, ast.ClassDef) and scope.name == "Port"
        )
        bound = port_bindings(scope, in_port_class)

        for node in scope_nodes(scope):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]

                if any(reads_a_port_name(o, bound) for o in operands) and any(
                    is_string_literal(o) for o in operands
                ):
                    findings.append(
                        f"line {node.lineno}: compares a port's name "
                        f"against a string literal",
                    )

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and reads_a_port_name(node.func.value, bound)
                and any(is_string_literal(argument) for argument in node.args)
            ):
                findings.append(
                    f"line {node.lineno}: matches a port's name with "
                    f".{node.func.attr}() on a string literal",
                )

    return findings


def test_the_guard_sees_the_modules_it_is_guarding():
    assert APP / "equipment" / "base.py" in APP_MODULES
    assert len(APP_MODULES) > 10


@pytest.mark.parametrize(
    "path",
    APP_MODULES,
    ids=lambda path: str(path.relative_to(APP)),
)
def test_no_module_lets_a_port_name_decide_behaviour(path):
    findings = name_driven_behaviour(path.read_text())

    assert not findings, (
        f"{path.relative_to(APP)} lets a port name decide behaviour "
        f"({'; '.join(findings)}) — a port name is an identifier, so branch on "
        f"direction, phase, purpose or control instead (ADR 0002, D3.2)"
    )


@pytest.mark.parametrize(
    "source",
    [
        'if port.name == "drain": pass',
        'if port.name != "suction": pass',
        'if "vent" == port.name: pass',
        'if port.name in ("vapor_out", "liquid_out"): pass',
        'if port.name not in ["relief"]: pass',
        'if port.name.startswith("vapor"): pass',
        'if port.name.endswith("_out"): pass',
        'if liquid_port.name == "drain": pass',
        # Reached through the expressions that yield a Port.
        'if branch.from_port.name == "suction": pass',
        'if device.port(name).name == "drain": pass',
        'if device.ports[key].name == "relief": pass',
        # Bound by iteration, by annotation, and by assignment.
        'for p in device.ports.values():\n    if p.name == "drain": pass',
        'for n, p in device.ports.items():\n    if p.name == "drain": pass',
        'def f(p: Port):\n    return p.name == "drain"',
        'def f(device):\n    p = device.port(n)\n    return p.name == "drain"',
        # A Port deciding on its own name is the same violation.
        'class Port:\n    def kind(self):\n        return self.name == "drain"',
    ],
)
def test_the_guard_catches_each_way_of_branching_on_a_port_name(source):
    assert name_driven_behaviour(source), source


@pytest.mark.parametrize(
    "source",
    [
        # Declaring a port is the whole point of naming one.
        'self.add_port("drain", OUTLET)',
        'self.add_port("relief", OUTLET, phase="vapor", purpose="relief")',
        'Port("suction", INLET)',
        'super().__init__(tag, ports={"suction": INLET, "discharge": OUTLET})',
        # Reading a name for a human to read.
        'raise ValueError(f"{device.tag}.{port.name} is not wired")',
        "names = sorted(port.name for port in matching)",
        'return f"Port({self.name!r}, {self.direction!r})"',
        # Reaching a port by name — an identifier is for looking things up.
        "device.port(name).connect(node)",
        'device.port("drain").connect(node)',
        'device.ports["relief"].disconnect()',
        # Some OTHER object's name. These subsystems do not exist yet, and the
        # guard must not fail them when they arrive.
        'if self.name == "discharge": pass',
        'if alarm.name == "PAHH-101": pass',
        'if controller.name == "PIC-101": pass',
        'if scenario.name == "loss-of-cooling": pass',
        'if malfunction.name == "stuck-valve": pass',
        'if node.name == "N-01": pass',
        'class Alarm:\n    def kind(self):\n        return self.name == "trip"',
        # Behaviour from the descriptors, which is what replaces the names.
        "if port.direction == OUTLET: pass",
        'if port.phase == "vapor": pass',
        'if port.purpose == "relief": pass',
        'if port.control == "level": pass',
    ],
)
def test_the_guard_leaves_everything_else_alone(source):
    assert not name_driven_behaviour(source), source
