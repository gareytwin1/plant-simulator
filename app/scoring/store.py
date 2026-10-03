"""
Score persistence (T15-4) - results and personal bests in SQLite.

A `StoredResult` is one scored run: the `Score` (T15-3) and the `RunMetrics`
(T15-2) it was computed from, filed under a caller-chosen `operator` and
`scenario`. Standard-library `sqlite3` only; no live caller exists yet, since
nothing in a run produces a score or stores one automatically.

**Row shape.** One row per result. `operator`, `scenario`, `total` and
`recorded_at` are real indexed columns because they are all a personal-best
query needs; the full `Score` and `RunMetrics` ride alongside as JSON text, so
adding a metric never touches the schema. `None` times round-trip as `None`,
label mappings keep their keys, and floats round-trip exactly.

**Personal best.** The highest `Score.total` for one `(operator, scenario)`.
Ties go to the earliest `recorded_at`, then the earliest stored. `operator` and
`scenario` are plain strings the caller passes in; there are no accounts.

**No clock.** `recorded_at` is supplied by the caller (sim time or an injected
timestamp); this module never reads a wall clock.

**Config version (T18-5).** Every result is stamped with the `ConfigVersion`
(`config/VERSION`) the store was opened under, and `get` and `history` return
each result with the version it carries (`None` for a row recorded before
versioning). `personal_best` considers only results whose config major
equals the store's: a result from an incomparable plant never stands as a
best, and an unversioned row is never comparable. Separating boards by
version is the leaderboard's business (T19-1).

**Schema versioning.** `PRAGMA user_version` holds the schema version, which
is the number of applied migrations. Opening an older file applies the missing
migrations in order inside one transaction, re-reading the version once the
write lock is held so two openers cannot both apply one; opening a newer one raises
`SchemaVersionError` rather than guessing at a layout it does not know.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any

from app.configversion import ConfigVersion, read_config_version
from app.envelope.evaluator import Severity
from app.envelope.tracker import Excursion
from app.scoring.metrics import RunMetrics
from app.scoring.score import Score

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operator TEXT NOT NULL,
        scenario TEXT NOT NULL,
        total REAL NOT NULL,
        recorded_at REAL NOT NULL,
        score_json TEXT NOT NULL,
        metrics_json TEXT NOT NULL
    );
    CREATE INDEX results_best ON results (operator, scenario, total DESC, recorded_at, id);
    """,
    """
    ALTER TABLE results ADD COLUMN config_version TEXT;
    ALTER TABLE results ADD COLUMN config_major INTEGER;
    DROP INDEX results_best;
    CREATE INDEX results_best ON results (operator, scenario, config_major, total DESC, recorded_at, id);
    """,
)

_COLUMNS = "id, operator, scenario, recorded_at, score_json, metrics_json, config_version"


class SchemaVersionError(Exception):
    """The database was written by a newer version of the code."""


@dataclass(frozen=True)
class StoredResult:
    id: int
    operator: str
    scenario: str
    recorded_at: float
    score: Score
    metrics: RunMetrics
    config_version: ConfigVersion | None


class ScoreStore:
    def __init__(
        self,
        path: Path | str = ":memory:",
        migrations: Sequence[str] = MIGRATIONS,
        config_version: ConfigVersion | None = None,
    ) -> None:
        self.config_version = config_version if config_version is not None else read_config_version()
        self._connection = sqlite3.connect(str(path), isolation_level=None)
        try:
            self._migrate(migrations)
        except BaseException:
            self._connection.close()
            raise

    @property
    def schema_version(self) -> int:
        return int(self._connection.execute("PRAGMA user_version").fetchone()[0])

    def record(
        self,
        operator: str,
        scenario: str,
        recorded_at: float,
        score: Score,
        metrics: RunMetrics,
    ) -> StoredResult:
        cursor = self._connection.execute(
            "INSERT INTO results (operator, scenario, total, recorded_at, score_json, metrics_json,"
            " config_version, config_major) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operator,
                scenario,
                score.total,
                recorded_at,
                json.dumps(_score_to_json(score)),
                json.dumps(_metrics_to_json(metrics)),
                str(self.config_version),
                self.config_version.major,
            ),
        )
        assert cursor.lastrowid is not None

        return StoredResult(
            cursor.lastrowid, operator, scenario, recorded_at, score, metrics, self.config_version
        )

    def get(self, result_id: int) -> StoredResult | None:
        row = self._connection.execute(
            f"SELECT {_COLUMNS}"
            " FROM results WHERE id = ?",
            (result_id,),
        ).fetchone()

        return _row_to_result(row) if row else None

    def history(self, operator: str, scenario: str) -> list[StoredResult]:
        rows = self._connection.execute(
            f"SELECT {_COLUMNS}"
            " FROM results WHERE operator = ? AND scenario = ? ORDER BY recorded_at, id",
            (operator, scenario),
        ).fetchall()

        return [_row_to_result(row) for row in rows]

    def personal_best(self, operator: str, scenario: str) -> StoredResult | None:
        row = self._connection.execute(
            f"SELECT {_COLUMNS}"
            " FROM results WHERE operator = ? AND scenario = ? AND config_major = ?"
            " ORDER BY total DESC, recorded_at, id LIMIT 1",
            (operator, scenario, self.config_version.major),
        ).fetchone()

        return _row_to_result(row) if row else None

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ScoreStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _migrate(self, migrations: Sequence[str]) -> None:
        target = len(migrations)

        if self.schema_version > target:
            raise self._too_new(target)
        if self.schema_version == target:
            return

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.schema_version
            if current > target:
                raise self._too_new(target)
            for script in migrations[current:]:
                for statement in _statements(script):
                    self._connection.execute(statement)
            self._connection.execute(f"PRAGMA user_version = {target}")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    def _too_new(self, target: int) -> SchemaVersionError:
        return SchemaVersionError(
            f"database is schema version {self.schema_version}, this code only knows up to {target}"
        )


def _statements(script: str) -> list[str]:
    statements: list[str] = []
    pending = ""

    for fragment in script.split(";"):
        pending += fragment + ";"
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip())
            pending = ""

    if pending.strip(" ;\n\t"):
        statements.append(pending.strip().removesuffix(";"))

    return [statement for statement in statements if statement.strip(" ;\n\t")]


def _score_to_json(score: Score) -> dict[str, Any]:
    return {"total": score.total, "penalties": dict(score.penalties)}


def _metrics_to_json(metrics: RunMetrics) -> dict[str, Any]:
    return {
        "alarm_count": metrics.alarm_count,
        "trip_count": metrics.trip_count,
        "time_to_recognise_s": metrics.time_to_recognise_s,
        "time_to_stabilise_s": metrics.time_to_stabilise_s,
        "peak_excursions": {
            label: None
            if excursion is None
            else {
                "severity": excursion.severity.name,
                "magnitude": excursion.magnitude,
                "timestamp": excursion.timestamp,
            }
            for label, excursion in metrics.peak_excursions.items()
        },
        "time_outside_envelope_s": metrics.time_outside_envelope_s,
        "production_lost": dict(metrics.production_lost),
        "unnecessary_actions": metrics.unnecessary_actions,
    }


def _row_to_result(row: tuple[Any, ...]) -> StoredResult:
    result_id, operator, scenario, recorded_at, score_json, metrics_json, config_version = row
    score = json.loads(score_json)
    metrics = json.loads(metrics_json)

    return StoredResult(
        id=result_id,
        operator=operator,
        scenario=scenario,
        recorded_at=recorded_at,
        score=Score(total=score["total"], penalties=MappingProxyType(score["penalties"])),
        metrics=RunMetrics(
            alarm_count=metrics["alarm_count"],
            trip_count=metrics["trip_count"],
            time_to_recognise_s=metrics["time_to_recognise_s"],
            time_to_stabilise_s=metrics["time_to_stabilise_s"],
            peak_excursions=MappingProxyType(_excursions(metrics["peak_excursions"])),
            time_outside_envelope_s=metrics["time_outside_envelope_s"],
            production_lost=MappingProxyType(metrics["production_lost"]),
            unnecessary_actions=metrics["unnecessary_actions"],
        ),
        config_version=None if config_version is None else ConfigVersion.parse(config_version),
    )


def _excursions(raw: Mapping[str, Any]) -> dict[str, Excursion | None]:
    return {
        label: None
        if peak is None
        else Excursion(
            severity=Severity[peak["severity"]],
            magnitude=peak["magnitude"],
            timestamp=peak["timestamp"],
        )
        for label, peak in raw.items()
    }
