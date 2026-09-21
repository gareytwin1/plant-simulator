"""A port name is an identifier. No module under app/ may branch on one.

T3-7 gave a connection explicit descriptors — direction, phase, purpose,
control — precisely so that nothing has to read meaning into what a port was
called. The rule only holds if it is enforced, because the tempting shortcut
(`if port.name == "drain"`) is one line and looks harmless until a plant names
its drain something else.

The check is structural rather than a word list: it flags any comparison of a
`.name` attribute against a string literal, any string method called on one
with a literal argument, and any port looked up by a hard-coded name. What
stays legal is *declaring* a port — `self.add_port("drain", OUTLET)` — and
reading a name for a message a human will see.

Written in the style of tests/test_random_source_guard.py, and like it, it
passes today and guards forever.
"""

import ast
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parent.parent / "app"

APP_MODULES = sorted(APP.rglob("*.py"))


def is_name_attribute(node):
    return isinstance(node, ast.Attribute) and node.attr == "name"


def is_string_literal(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)

    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(is_string_literal(element) for element in node.elts)

    return False


def name_driven_behaviour(source):
    """Every place `source` lets a port's name decide something."""
    findings = []

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]

            if any(is_name_attribute(o) for o in operands) and any(
                is_string_literal(o) for o in operands
            ):
                findings.append(
                    f"line {node.lineno}: compares a .name against a string literal",
                )

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if is_name_attribute(node.func.value) and any(
                is_string_literal(argument) for argument in node.args
            ):
                findings.append(
                    f"line {node.lineno}: matches a .name with "
                    f".{node.func.attr}() on a string literal",
                )

            if node.func.attr == "port" and any(
                is_string_literal(argument) for argument in node.args
            ):
                findings.append(
                    f"line {node.lineno}: looks a port up by a hard-coded name",
                )

        if isinstance(node, ast.Subscript):
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "ports"
                and is_string_literal(node.slice)
            ):
                findings.append(
                    f"line {node.lineno}: indexes .ports by a hard-coded name",
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
def test_no_module_branches_on_a_port_name(path):
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
        'if self.name == "discharge": pass',
        'if port.name.startswith("vapor"): pass',
        'if port.name.endswith("_out"): pass',
        'device.port("drain").connect(node)',
        'device.ports["relief"].disconnect()',
    ],
)
def test_the_guard_catches_each_way_of_branching_on_a_name(source):
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
        # Reaching a port by a name the caller supplied.
        "device.port(name).connect(node)",
        "device.ports[name].disconnect()",
        # Behaviour from the descriptors, which is what replaces the names.
        "if port.direction == OUTLET: pass",
        'if port.phase == "vapor": pass',
        'if port.purpose == "relief": pass',
        'if port.control == "level": pass',
    ],
)
def test_the_guard_leaves_legitimate_declarations_alone(source):
    assert not name_driven_behaviour(source), source
