import sqlite3
from contextlib import closing
from types import MappingProxyType

import pytest

from app.envelope.evaluator import Severity
from app.envelope.tracker import Excursion
from app.scoring.metrics import RunMetrics
from app.scoring.score import Score
from app.scoring.store import MIGRATIONS, SchemaVersionError, ScoreStore

PENALTIES = {
    "trip_count": 0.5,
    "peak_severity": 1.0,
    "unnecessary_actions": 0.0,
    "time_outside_envelope": 0.25,
    "time_to_stabilise": 0.125,
    "alarm_count": 0.2,
    "time_to_recognise": 0.1,
    "production_lost": 0.3,
}


def _metrics(**overrides):
    fields = {
        "alarm_count": 3,
        "trip_count": 1,
        "time_to_recognise_s": 12.5,
        "time_to_stabilise_s": None,
        "peak_excursions": MappingProxyType(
            {
                "discharge pressure": Excursion(Severity.TRIP, 7.25, 41.0),
                "suction pressure": None,
            }
        ),
        "time_outside_envelope_s": 33.3,
        "production_lost": MappingProxyType({"flow": 1234.5, "level": 0.1}),
        "unnecessary_actions": 2,
    }
    fields.update(overrides)
    return RunMetrics(**fields)


def _score(total):
    return Score(total=total, penalties=MappingProxyType(dict(PENALTIES)))


def test_round_trips_across_close_and_reopen(tmp_path):
    path = tmp_path / "scores.db"
    score = _score(61.234567890123)
    metrics = _metrics()

    with ScoreStore(path) as store:
        stored = store.record("op-1", "compressor-trip", 100.0, score, metrics)

    with ScoreStore(path) as reopened:
        loaded = reopened.get(stored.id)

    assert loaded == stored
    assert loaded.score.total == pytest.approx(61.234567890123)
    assert loaded.metrics == metrics
    assert loaded.metrics.time_to_stabilise_s is None
    assert loaded.metrics.peak_excursions["suction pressure"] is None
    assert set(loaded.metrics.production_lost) == {"flow", "level"}
    assert dict(loaded.score.penalties) == PENALTIES


def test_get_unknown_id_is_none():
    with ScoreStore() as store:
        assert store.get(99) is None


def test_personal_best_is_highest_total_for_that_operator_and_scenario():
    with ScoreStore() as store:
        store.record("op-1", "s1", 1.0, _score(40.0), _metrics())
        best = store.record("op-1", "s1", 2.0, _score(80.0), _metrics())
        store.record("op-1", "s1", 3.0, _score(60.0), _metrics())
        store.record("op-1", "s2", 4.0, _score(99.0), _metrics())
        store.record("op-2", "s1", 5.0, _score(95.0), _metrics())

        assert store.personal_best("op-1", "s1") == best


def test_personal_best_tie_goes_to_earliest_recorded():
    with ScoreStore() as store:
        store.record("op-1", "s1", 9.0, _score(70.0), _metrics())
        earliest = store.record("op-1", "s1", 2.0, _score(70.0), _metrics())

        assert store.personal_best("op-1", "s1") == earliest


def test_identical_total_and_time_resolve_by_insertion_order():
    with ScoreStore() as store:
        first = store.record("op-1", "s1", 2.0, _score(70.0), _metrics())
        second = store.record("op-1", "s1", 2.0, _score(70.0), _metrics())

        assert store.personal_best("op-1", "s1") == first
        assert store.history("op-1", "s1") == [first, second]


def test_migration_statements_may_contain_semicolons_in_literals_and_triggers(tmp_path):
    path = tmp_path / "scores.db"
    version_two = MIGRATIONS + (
        """
        ALTER TABLE results ADD COLUMN note TEXT NOT NULL DEFAULT 'a;b';
        CREATE TABLE audit (n INTEGER);
        CREATE TRIGGER results_audit AFTER INSERT ON results
        BEGIN
            INSERT INTO audit VALUES (1);
            INSERT INTO audit VALUES (2);
        END;
        """,
    )

    with ScoreStore(path, migrations=version_two) as store:
        store.record("op-1", "s1", 1.0, _score(10.0), _metrics())

        assert store.schema_version == 2
        assert store._connection.execute("SELECT count(*) FROM audit").fetchone() == (2,)
        assert store._connection.execute("SELECT note FROM results").fetchone() == ("a;b",)


def test_personal_best_none_without_results():
    with ScoreStore() as store:
        store.record("op-1", "s1", 1.0, _score(10.0), _metrics())

        assert store.personal_best("op-1", "other") is None
        assert store.personal_best("nobody", "s1") is None


def test_history_is_in_recorded_order():
    with ScoreStore() as store:
        later = store.record("op-1", "s1", 5.0, _score(10.0), _metrics())
        earlier = store.record("op-1", "s1", 1.0, _score(20.0), _metrics())
        store.record("op-1", "s2", 3.0, _score(30.0), _metrics())

        assert store.history("op-1", "s1") == [earlier, later]


def test_new_database_is_at_current_schema_version(tmp_path):
    with ScoreStore(tmp_path / "scores.db") as store:
        assert store.schema_version == len(MIGRATIONS)


def test_upgrade_from_version_one_keeps_data(tmp_path):
    path = tmp_path / "scores.db"
    version_two = MIGRATIONS + ("ALTER TABLE results ADD COLUMN note TEXT NOT NULL DEFAULT 'none';",)

    with ScoreStore(path) as v1:
        stored = v1.record("op-1", "s1", 1.0, _score(55.0), _metrics())
        assert v1.schema_version == 1

    with ScoreStore(path, migrations=version_two) as v2:
        assert v2.schema_version == 2
        assert v2.get(stored.id) == stored

    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("SELECT note FROM results").fetchone() == ("none",)


def test_failed_migration_rolls_back(tmp_path):
    path = tmp_path / "scores.db"
    with ScoreStore(path):
        pass
    broken = MIGRATIONS + ("ALTER TABLE results ADD COLUMN ok TEXT; SELECT * FROM missing_table;",)

    with pytest.raises(sqlite3.OperationalError):
        ScoreStore(path, migrations=broken)

    with ScoreStore(path) as store:
        assert store.schema_version == 1
        columns = [c[1] for c in store._connection.execute("PRAGMA table_info(results)")]
        assert "ok" not in columns


def test_refuses_database_newer_than_code(tmp_path):
    path = tmp_path / "scores.db"
    newer = MIGRATIONS + ("ALTER TABLE results ADD COLUMN note TEXT;",)
    with ScoreStore(path, migrations=newer):
        pass

    with pytest.raises(SchemaVersionError):
        ScoreStore(path)


def _stale_until_locked(monkeypatch, stale):
    real = ScoreStore.schema_version.fget

    def read(self):
        return real(self) if self._connection.in_transaction else stale

    monkeypatch.setattr(ScoreStore, "schema_version", property(read))


def test_migration_rereads_version_under_lock(tmp_path, monkeypatch):
    path = tmp_path / "scores.db"
    version_two = MIGRATIONS + ("ALTER TABLE results ADD COLUMN note TEXT;",)
    with ScoreStore(path):
        pass

    _stale_until_locked(monkeypatch, 0)
    with ScoreStore(path, migrations=version_two) as store:
        assert store._connection.execute("PRAGMA user_version").fetchone() == (2,)


def test_migration_refuses_file_that_became_newer_under_lock(tmp_path, monkeypatch):
    path = tmp_path / "scores.db"
    newer = MIGRATIONS + ("ALTER TABLE results ADD COLUMN note TEXT;",)
    with ScoreStore(path, migrations=newer):
        pass

    _stale_until_locked(monkeypatch, 0)
    with pytest.raises(SchemaVersionError):
        ScoreStore(path)
