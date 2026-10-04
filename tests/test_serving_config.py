"""The production serving config pins one worker process (T18-1, R7)."""

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _serving_config():
    return runpy.run_path(str(ROOT / "gunicorn.conf.py"))


def test_serving_config_pins_one_worker_process():
    assert _serving_config()["workers"] == 1


def test_serving_config_uses_threads_for_concurrency():
    config = _serving_config()
    assert config["worker_class"] == "gthread"
    assert config["threads"] > 1


def test_serving_config_does_not_preload_the_app():
    assert not _serving_config().get("preload_app", False)


def test_requirements_pin_a_wsgi_server():
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    assert any(line.startswith("gunicorn==") for line in lines)
