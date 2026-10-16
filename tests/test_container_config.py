"""The container serves with Gunicorn only, never a dev server (T18-1)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _runtime_cmd() -> list[str]:
    stage = (ROOT / "Dockerfile").read_text().split("AS runtime", 1)[1]
    cmd = [line for line in stage.splitlines() if line.startswith("CMD ")]
    assert len(cmd) == 1
    return yaml.safe_load(cmd[0][len("CMD "):])


def test_runtime_image_serves_with_gunicorn_and_the_pinned_config():
    assert _runtime_cmd() == ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]


def test_dockerfile_never_starts_a_dev_server():
    text = (ROOT / "Dockerfile").read_text()
    assert "flask run" not in text
    assert "app.run" not in text


def test_compose_runs_one_runtime_service_without_replicas():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    (service,) = compose["services"].values()
    assert service["build"]["target"] == "runtime"
    assert "replicas" not in service.get("deploy", {})
    assert "scale" not in service


def test_the_container_keeps_the_score_database_on_a_volume():
    runtime = (ROOT / "Dockerfile").read_text().split("AS runtime", 1)[1]
    env = [line for line in runtime.splitlines() if "PLANT_SCORE_DB=" in line]
    assert len(env) == 1
    path = Path(env[0].split("PLANT_SCORE_DB=", 1)[1].split()[0])

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    (service,) = compose["services"].values()
    mounts = [volume.split(":") for volume in service["volumes"]]

    assert [str(path.parent)] == [target for _, target in mounts]
    (name,) = [source for source, _ in mounts]
    assert name in compose["volumes"]
