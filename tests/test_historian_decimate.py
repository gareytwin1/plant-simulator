import pytest

from app.historian.buffer import Historian, Sample
from app.historian.decimate import decimate


def _ramp(n, value=lambda i: float(i)):
    return [Sample(float(i), value(i)) for i in range(n)]


def test_single_sample_spike_survives_maximum_decimation():
    samples = _ramp(1000, lambda i: 100.0)
    samples[537] = Sample(537.0, 250.0)

    result = decimate(samples, 2)

    assert len(result) == 2
    assert Sample(537.0, 250.0) in result


def test_single_sample_dip_survives_maximum_decimation():
    samples = _ramp(1000, lambda i: 100.0)
    samples[12] = Sample(12.0, -40.0)

    assert Sample(12.0, -40.0) in decimate(samples, 2)


@pytest.mark.parametrize("position", range(0, 100))
def test_spike_survives_wherever_it_falls_relative_to_bucket_boundaries(position):
    samples = _ramp(100, lambda i: 1.0)
    samples[position] = Sample(float(position), 9.0)

    for max_points in (2, 4, 6, 10, 20):
        assert Sample(float(position), 9.0) in decimate(samples, max_points)


def test_peaks_keep_their_original_timestamps():
    samples = _ramp(60, lambda i: float((i * 7) % 11))

    for kept in decimate(samples, 10):
        assert samples[int(kept.timestamp)] == kept


def test_every_bucket_keeps_its_own_extremes():
    samples = _ramp(40, lambda i: float((i * 13) % 17))

    result = decimate(samples, 8)

    for bucket in range(4):
        window = samples[bucket * 10 : (bucket + 1) * 10]
        kept = result[bucket * 2 : (bucket + 1) * 2]
        assert min(s.value for s in window) == pytest.approx(min(s.value for s in kept))
        assert max(s.value for s in window) == pytest.approx(max(s.value for s in kept))


def test_output_length_matches_request_when_even():
    assert len(decimate(_ramp(1000), 100)) == 100
    assert len(decimate(_ramp(1001), 100)) == 100


def test_output_length_is_one_under_an_odd_request():
    assert len(decimate(_ramp(1000), 99)) == 98


def test_constant_signal_still_returns_the_requested_length():
    assert len(decimate(_ramp(500, lambda i: 5.0), 20)) == 20


def test_output_is_in_timestamp_order():
    samples = _ramp(200, lambda i: float((i * 31) % 23))

    timestamps = [s.timestamp for s in decimate(samples, 30)]

    assert timestamps == sorted(timestamps)


def test_input_at_or_under_the_request_is_returned_unchanged():
    samples = _ramp(10)

    assert decimate(samples, 10) == tuple(samples)
    assert decimate(samples, 50) == tuple(samples)
    assert decimate([], 4) == ()


def test_rejects_a_request_too_small_to_hold_both_extremes():
    with pytest.raises(ValueError):
        decimate(_ramp(10), 1)


def test_decimates_a_historian_history():
    historian = Historian(capacity=500)
    for i in range(500):
        historian.record("PT-101", float(i), 3.0 if i != 321 else 8.0)

    assert Sample(321.0, 8.0) in decimate(historian.history("PT-101"), 6)


def test_nan_at_bucket_start_does_not_hide_the_real_extremes():
    nan = float("nan")
    samples = _ramp(20, lambda i: 1.0)
    samples[0] = Sample(0.0, nan)
    samples[7] = Sample(7.0, 9.0)
    samples[3] = Sample(3.0, -4.0)

    result = decimate(samples, 2)

    assert Sample(7.0, 9.0) in result
    assert Sample(3.0, -4.0) in result


def test_nan_mid_bucket_is_ignored_when_picking_extremes():
    nan = float("nan")
    samples = _ramp(20, lambda i: float(i))
    samples[10] = Sample(10.0, nan)

    result = decimate(samples, 2)

    assert [s.value for s in result] == [0.0, 19.0]


def test_all_nan_or_single_finite_bucket_keeps_two_distinct_samples():
    nan = float("nan")
    all_nan = _ramp(10, lambda i: nan)
    one_finite = _ramp(10, lambda i: nan)
    one_finite[4] = Sample(4.0, 2.0)

    assert len(decimate(all_nan, 2)) == 2
    kept = decimate(one_finite, 2)
    assert len(kept) == 2
    assert kept[0] != kept[1]
    assert Sample(4.0, 2.0) in kept
