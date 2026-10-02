"""
Weighted scoring (T15-3) - a `RunMetrics` turned into one number, 0 to 100.

A pure function of a `RunMetrics` and a `ScoringConfig`: no clock, no log, no
history, no `random`, so the same inputs always give the same score. It reads
the metrics and never recomputes them.

    score = 100 * (1 - sum(weight * penalty) / sum(weight))

Every penalty is in [0, 1]. Weights and scales live in `config/scoring.yaml`,
so the balance is tunable without a code change. The weights put a trip well
above any amount of slow recovery: someone who stabilises slowly with no
further alarm outscores someone who acts fast and causes a trip.

**What `None` means.** `time_to_recognise_s` and `time_to_stabilise_s` are
`None` both when nothing alarmed and when something alarmed and the operator
never responded or the plant never settled. `alarm_count` tells them apart:
no alarms means nothing to recognise or stabilise (penalty 0); alarms with a
`None` time means it never happened (full penalty).

**Peak severity.** `Excursion.magnitude` is in each variable's own unit, so it
is not comparable across variables; the worst `Excursion.severity` reached is.

**Production lost** is per-label in that variable's own unit, so each label is
normalised by its own scale from the config and the penalties are averaged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import yaml

from app.envelope.evaluator import Severity
from app.scoring.metrics import RunMetrics

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scoring.yaml"

_WEIGHT_KEYS = (
    "trip_count",
    "peak_severity",
    "unnecessary_actions",
    "time_outside_envelope",
    "time_to_stabilise",
    "alarm_count",
    "time_to_recognise",
    "production_lost",
)
_SCALE_KEYS = (
    "trip_count",
    "unnecessary_actions",
    "time_outside_envelope_s",
    "time_to_stabilise_s",
    "alarm_count",
    "time_to_recognise_s",
)


@dataclass(frozen=True)
class ScoringConfig:
    weights: Mapping[str, float]
    scales: Mapping[str, float]
    peak_severity_penalty: Mapping[Severity, float]
    production_lost_scales: Mapping[str, float]

    def __post_init__(self) -> None:
        for name, keys, table in (
            ("weights", _WEIGHT_KEYS, self.weights),
            ("scales", _SCALE_KEYS, self.scales),
        ):
            missing = [key for key in keys if key not in table]
            if missing:
                raise ValueError(f"scoring {name} missing {missing}")
            unknown = [key for key in table if key not in keys]
            if unknown:
                raise ValueError(f"scoring {name} has unknown keys {unknown}")
        if any(weight < 0.0 for weight in self.weights.values()):
            raise ValueError("scoring weights must be non-negative")
        if sum(self.weights.values()) <= 0.0:
            raise ValueError("scoring weights must not all be zero")
        for scale in (*self.scales.values(), *self.production_lost_scales.values()):
            if scale <= 0.0:
                raise ValueError("scoring scales must be positive")
        for severity in (Severity.WARNING, Severity.ALARM, Severity.TRIP):
            if not 0.0 <= self.peak_severity_penalty.get(severity, -1.0) <= 1.0:
                raise ValueError(f"peak_severity_penalty[{severity.name}] must be in [0, 1]")


@dataclass(frozen=True)
class Score:
    total: float
    penalties: Mapping[str, float]


def load_scoring_config(path: Path | str = DEFAULT_CONFIG_PATH) -> ScoringConfig:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    return ScoringConfig(
        weights=MappingProxyType({k: float(v) for k, v in raw["weights"].items()}),
        scales=MappingProxyType({k: float(v) for k, v in raw["scales"].items()}),
        peak_severity_penalty=MappingProxyType(
            {Severity[k]: float(v) for k, v in raw["peak_severity_penalty"].items()}
        ),
        production_lost_scales=MappingProxyType(
            {k: float(v) for k, v in (raw.get("production_lost_scales") or {}).items()}
        ),
    )


def score_run(metrics: RunMetrics, config: ScoringConfig) -> Score:
    alarmed = metrics.alarm_count > 0
    scales = config.scales

    penalties = {
        "trip_count": _saturate(metrics.trip_count, scales["trip_count"]),
        "peak_severity": _peak_severity(metrics, config),
        "unnecessary_actions": _saturate(metrics.unnecessary_actions, scales["unnecessary_actions"]),
        "time_outside_envelope": _saturate(
            metrics.time_outside_envelope_s, scales["time_outside_envelope_s"]
        ),
        "time_to_stabilise": _time_penalty(
            metrics.time_to_stabilise_s, alarmed, scales["time_to_stabilise_s"]
        ),
        "alarm_count": _saturate(metrics.alarm_count, scales["alarm_count"]),
        "time_to_recognise": _time_penalty(
            metrics.time_to_recognise_s, alarmed, scales["time_to_recognise_s"]
        ),
        "production_lost": _production_lost(metrics, config),
    }

    total_weight = sum(config.weights.values())
    weighted = sum(config.weights[name] * penalty for name, penalty in penalties.items())

    return Score(
        total=100.0 * (1.0 - weighted / total_weight),
        penalties=MappingProxyType(penalties),
    )


def _saturate(value: float, scale: float) -> float:
    return min(1.0, max(0.0, value / scale))


def _time_penalty(value: float | None, alarmed: bool, scale: float) -> float:
    if value is None:
        return 1.0 if alarmed else 0.0
    return _saturate(value, scale)


def _peak_severity(metrics: RunMetrics, config: ScoringConfig) -> float:
    return max(
        (
            config.peak_severity_penalty[peak.severity]
            for peak in metrics.peak_excursions.values()
            if peak is not None
        ),
        default=0.0,
    )


def _production_lost(metrics: RunMetrics, config: ScoringConfig) -> float:
    if not metrics.production_lost:
        return 0.0

    unscaled = [label for label in metrics.production_lost if label not in config.production_lost_scales]
    if unscaled:
        raise ValueError(f"no production_lost_scales entry for {unscaled}")

    return sum(
        _saturate(lost, config.production_lost_scales[label])
        for label, lost in metrics.production_lost.items()
    ) / len(metrics.production_lost)
