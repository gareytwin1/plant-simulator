import io
import json
import logging
import subprocess
import sys

import pytest
from flask import Flask

from app import logging as plant_logging


@pytest.fixture
def streams():
    engine, request = io.StringIO(), io.StringIO()
    plant_logging.configure(
        sim_time=lambda: 42.5, engine_stream=engine, request_stream=request
    )

    yield engine, request

    plant_logging.configure()


def lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_output_is_one_json_object_per_line_with_the_standard_fields(streams):
    engine, _ = streams

    logging.getLogger("app.engine.scheduler").warning("slow step %d", 3)

    (row,) = lines(engine)
    assert row["message"] == "slow step 3"
    assert row["level"] == "WARNING"
    assert row["logger"] == "app.engine.scheduler"
    assert row["stream"] == "engine"
    assert row["ts"].endswith("+00:00")


def test_engine_and_request_records_go_to_separate_streams(streams):
    engine, request = streams

    logging.getLogger("app.engine.scheduler").error("engine says")
    logging.getLogger("plant.engine").info("so does this")
    logging.getLogger("plant.request").info("request says")

    assert [r["message"] for r in lines(engine)] == ["engine says", "so does this"]
    assert [r["message"] for r in lines(request)] == ["request says"]
    assert {r["stream"] for r in lines(request)} == {"request"}


def test_records_carry_sim_time_from_the_provider(streams):
    engine, request = streams

    logging.getLogger("app.engine.scheduler").error("x")
    logging.getLogger("plant.request").info("y")

    assert lines(engine)[0]["sim_time"] == 42.5
    assert lines(request)[0]["sim_time"] == 42.5


def test_a_caller_supplied_sim_time_wins_over_the_provider(streams):
    engine, _ = streams

    logging.getLogger("plant.engine").info("x", extra={"sim_time": 7.0})

    assert lines(engine)[0]["sim_time"] == 7.0


def test_a_provider_with_no_answer_or_that_raises_gives_null_sim_time():
    out = io.StringIO()

    def broken():
        raise RuntimeError("no plant")

    for provider in (lambda: None, broken):
        plant_logging.configure(sim_time=provider, engine_stream=out, request_stream=out)
        logging.getLogger("plant.engine").info("x")

    try:
        assert [r["sim_time"] for r in lines(out)] == [None, None]
    finally:
        plant_logging.configure()


def test_extra_fields_and_exceptions_are_included(streams):
    engine, _ = streams

    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("plant.engine").exception("failed", extra={"tag": "K-101"})

    (row,) = lines(engine)
    assert row["tag"] == "K-101"
    assert "ValueError: boom" in row["exc"]


def test_configure_twice_does_not_duplicate_lines():
    out = io.StringIO()

    for _ in range(3):
        plant_logging.configure(engine_stream=out, request_stream=out)

    try:
        logging.getLogger("plant.engine").info("once")
        assert len(lines(out)) == 1
    finally:
        plant_logging.configure()


def test_log_requests_writes_one_request_line_with_sim_time(streams):
    _, request = streams
    app = Flask(__name__)
    plant_logging.log_requests(app, lambda: 99.0)

    @app.get("/ping")
    def ping():
        return "pong"

    app.test_client().get("/ping")

    (row,) = lines(request)
    assert row["stream"] == "request"
    assert (row["method"], row["path"], row["status"]) == ("GET", "/ping", 200)
    assert row["sim_time"] == 99.0
    assert row["duration_ms"] >= 0


def test_the_stdlib_logging_module_is_not_shadowed():
    code = "import logging, sys; print(logging.__file__); import app.logging; print(app.logging.__file__)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()

    assert out[0].replace("\\", "/").endswith("/logging/__init__.py")
    assert out[1].replace("\\", "/").endswith("app/logging.py")
