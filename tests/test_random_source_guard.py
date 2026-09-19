import ast
from pathlib import Path

import pytest


APP = Path(__file__).resolve().parent.parent / "app"

ALLOWED = APP / "engine" / "rng.py"


def imports_random(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "random" for alias in node.names):
                return True

        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] == "random":
                return True

    return False


APP_MODULES = sorted(APP.rglob("*.py"))


def test_the_guard_sees_the_modules_it_is_guarding():
    assert ALLOWED in APP_MODULES
    assert len(APP_MODULES) > 10


def test_the_seeded_source_is_the_module_that_imports_random():
    assert imports_random(ALLOWED)


@pytest.mark.parametrize(
    "path",
    [path for path in APP_MODULES if path != ALLOWED],
    ids=lambda path: str(path.relative_to(APP)),
)
def test_no_other_module_imports_random(path):
    assert not imports_random(path), (
        f"{path.relative_to(APP)} imports random — draw from a SeededRNG "
        f"(app/engine/rng.py) so scenario replay stays deterministic"
    )


def test_the_guard_catches_each_way_of_importing_random(tmp_path):
    for source in (
        "import random",
        "import random as r",
        "import os, random",
        "from random import choice",
        "import random.foo",
    ):
        module = tmp_path / "m.py"
        module.write_text(source)

        assert imports_random(module), source

    module = tmp_path / "clean.py"
    module.write_text("from app.engine.rng import SeededRNG\nimport randomness\n")

    assert not imports_random(module)
