"""The stream's stuck-client bound under a real Gunicorn gthread server (T18-9).

Gunicorn exposes the client socket as `environ["gunicorn.socket"]`, not
Werkzeug's key, so a bound that only looked for `werkzeug.socket` left a
client that stops reading holding a request thread under the T18-1
deployment. Observed end to end: one worker thread, one stalled stream, and
whether an ordinary request can still be served.
"""

import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("gunicorn")

from app.api.stream import MIN_DROPOUT_SECONDS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

APP_SOURCE = textwrap.dedent(
    """
    from flask import Flask

    from app.api.stream import create_stream_blueprint
    from app.engine.snapshot import build_snapshot


    class PaddedSource:
        def snapshot(self):
            return build_snapshot(
                sim_time=0.0,
                speed=1.0,
                running=True,
                equipment={"PAD": {"filler": "x" * 4000}},
            )


    app = Flask(__name__)
    app.register_blueprint(create_stream_blueprint(lambda: PaddedSource(), 0.001))


    @app.get("/ping")
    def ping():
        return "pong"
    """
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _ping(port: int, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=timeout) as reply:
            return bool(reply.read() == b"pong")
    except OSError:
        return False


def test_a_client_that_never_reads_frees_its_request_thread_under_gunicorn(tmp_path):
    (tmp_path / "stream_app.py").write_text(APP_SOURCE)
    port = _free_port()
    server = subprocess.Popen(
        [
            sys.executable, "-m", "gunicorn",
            "--worker-class", "gthread", "--workers", "1", "--threads", "1",
            "--bind", f"127.0.0.1:{port}",
            "--pythonpath", f"{tmp_path},{REPO_ROOT}",
            "stream_app:app",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        deadline = time.monotonic() + 15.0
        while not _ping(port, 1.0):
            assert server.poll() is None, "gunicorn exited before serving"
            assert time.monotonic() < deadline, "gunicorn never came up"
            time.sleep(0.1)

        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        client.settimeout(5.0)
        client.connect(("127.0.0.1", port))
        client.sendall(
            b"GET /api/stream HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
        )

        # The only worker thread is now serving the stalled stream. It is
        # free again only once the socket bound drops that client.
        assert not _ping(port, 0.5), "the stream never took the only thread"
        deadline = time.monotonic() + MIN_DROPOUT_SECONDS + 10.0
        while not _ping(port, 1.0):
            assert time.monotonic() < deadline, (
                "a client that never reads still holds the request thread"
            )
    finally:
        client.close()
        server.kill()
        server.wait(timeout=10.0)
