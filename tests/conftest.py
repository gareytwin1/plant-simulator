import pytest

from app.api import validate
from app.scoring.store import ScoreStore


def unlimited():
    return validate.RateLimiter(rate=1e9, burst=1e9)


@pytest.fixture(autouse=True)
def unlimited_live_app(monkeypatch):
    """The live app's limiter runs on the wall clock and every test client is
    127.0.0.1, so whether a test got a 429 would depend on suite speed. The
    tests that exercise the limiter install their own."""
    from app import main

    monkeypatch.setattr(main, "rate_limiter", unlimited())


@pytest.fixture(autouse=True)
def empty_score_store(monkeypatch):
    """Every test starts with no run history, whatever an earlier test stored."""
    from app import main

    store = ScoreStore()
    monkeypatch.setattr(main, "store", store)

    yield store

    store.close()
