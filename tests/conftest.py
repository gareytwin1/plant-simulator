import pytest

from app import main
from app.api import validate


@pytest.fixture(autouse=True)
def unlimited_live_app(monkeypatch):
    """The live app's limiter runs on the wall clock and every test client is
    127.0.0.1, so whether a test got a 429 would depend on suite speed. The
    tests that exercise the limiter install their own."""
    monkeypatch.setattr(main, "rate_limiter", validate.RateLimiter(rate=1e9, burst=1e9))
