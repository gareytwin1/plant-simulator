import pytest

from app.scoring.throughput import ThroughputTracker


def test_shortfall_below_target_is_integrated_over_time():
    tracker = ThroughputTracker(target=100.0)

    tracker.update(80.0, dt=2.0)
    tracker.update(90.0, dt=3.0)

    assert tracker.lost == pytest.approx(20.0 * 2.0 + 10.0 * 3.0)


def test_running_above_target_earns_nothing_back():
    tracker = ThroughputTracker(target=100.0)

    tracker.update(80.0, dt=1.0)
    tracker.update(150.0, dt=10.0)

    assert tracker.lost == pytest.approx(20.0)


def test_negative_dt_is_refused_and_reset_clears():
    tracker = ThroughputTracker(target=100.0)

    with pytest.raises(ValueError):
        tracker.update(80.0, dt=-1.0)

    tracker.update(80.0, dt=1.0)
    tracker.reset()

    assert tracker.lost == pytest.approx(0.0)
