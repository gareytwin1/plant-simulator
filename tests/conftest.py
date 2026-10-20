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


@pytest.fixture(scope="module")
def olefins_free_play():
    """Free play on olefins_lite at normal_operation, for the tests that read
    its tags through a plain `TrainingSession` or a fresh console cookie.
    Module scope, so a module-scoped fixture that builds a session sees it."""
    from app import config

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(config, "FREE_PLAY_PLANT", "olefins_lite")
        patch.setattr(config, "FREE_PLAY_CONDITION", "normal_operation")
        yield
