import threading

import pytest

from app.historian.buffer import Historian


def test_retains_exactly_n_samples_and_evicts_oldest_first():
    historian = Historian(capacity=3)

    for i in range(5):
        historian.record("PT-101", timestamp=float(i), value=float(i))

    history = historian.history("PT-101")

    assert len(history) == 3
    assert [s.timestamp for s in history] == [2.0, 3.0, 4.0]
    assert [s.value for s in history] == [2.0, 3.0, 4.0]


def test_history_is_oldest_first():
    historian = Historian(capacity=5)

    for i in range(3):
        historian.record("PT-101", timestamp=float(i), value=float(i) * 10.0)

    history = historian.history("PT-101")

    assert [s.timestamp for s in history] == [0.0, 1.0, 2.0]


def test_unknown_tag_returns_empty_history():
    historian = Historian(capacity=3)

    assert historian.history("PT-999") == ()


def test_tags_keep_independent_buffers():
    historian = Historian(capacity=2)

    historian.record("PT-101", timestamp=0.0, value=1.0)
    historian.record("FT-201", timestamp=0.0, value=2.0)
    historian.record("PT-101", timestamp=1.0, value=1.5)

    assert len(historian.history("PT-101")) == 2
    assert len(historian.history("FT-201")) == 1


def test_zero_sample_period_records_every_call():
    historian = Historian(capacity=10, sample_period=0.0)

    for i in range(4):
        historian.record("PT-101", timestamp=float(i) * 0.1, value=float(i))

    assert len(historian.history("PT-101")) == 4


def test_sample_period_drops_calls_inside_the_window():
    historian = Historian(capacity=10, sample_period=1.0)

    historian.record("PT-101", timestamp=0.0, value=0.0)
    historian.record("PT-101", timestamp=0.4, value=1.0)  # dropped, < 1.0 since last
    historian.record("PT-101", timestamp=0.9, value=2.0)  # dropped, < 1.0 since last
    historian.record("PT-101", timestamp=1.0, value=3.0)  # kept, exactly the period

    history = historian.history("PT-101")

    assert [s.value for s in history] == [0.0, 3.0]


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        Historian(capacity=0)


def test_sample_period_must_be_non_negative():
    with pytest.raises(ValueError):
        Historian(capacity=3, sample_period=-1.0)


def test_timestamp_must_be_non_decreasing_per_tag():
    historian = Historian(capacity=3)
    historian.record("PT-101", timestamp=5.0, value=1.0)

    with pytest.raises(ValueError):
        historian.record("PT-101", timestamp=4.0, value=2.0)


def test_timestamp_regression_is_caught_even_inside_a_throttled_window():
    historian = Historian(capacity=3, sample_period=10.0)
    historian.record("PT-101", timestamp=0.0, value=1.0)
    historian.record("PT-101", timestamp=5.0, value=2.0)  # dropped, inside the window

    with pytest.raises(ValueError):
        historian.record("PT-101", timestamp=3.0, value=3.0)  # behind the dropped call


def test_memory_bounded_over_a_simulated_multi_hour_run():
    capacity = 60
    historian = Historian(capacity=capacity, sample_period=60.0)  # one sample/minute

    dt = 1.0
    six_hours_in_seconds = 6 * 60 * 60
    steps = int(six_hours_in_seconds / dt)

    for step in range(steps):
        timestamp = step * dt
        historian.record("PT-101", timestamp=timestamp, value=float(step))
        assert len(historian.history("PT-101")) <= capacity

    history = historian.history("PT-101")
    assert len(history) == capacity


def test_concurrent_read_during_write_is_safe():
    capacity = 50
    historian = Historian(capacity=capacity)
    write_count = 5000
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for i in range(write_count):
                historian.record("PT-101", timestamp=float(i), value=float(i))
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(write_count):
                history = historian.history("PT-101")
                assert len(history) <= capacity
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)

    writer_thread.start()
    reader_thread.start()
    writer_thread.join()
    reader_thread.join()

    assert errors == []
    assert len(historian.history("PT-101")) == capacity
