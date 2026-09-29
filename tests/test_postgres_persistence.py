import os
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dse.contracts.event import make_tick_event
from dse.contracts.experiment import load_manifest
from dse.engine.scheduler import advance_ticks
from dse.engine.snapshot import world_state_hash
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import EventSequenceError, PostgresEventStore
from dse.persistence.experiment_store import ManifestConflictError, register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.persistence.snapshot_store import PostgresSnapshotStore


MANIFEST = Path("experiments/v0_1/empty-world.yaml")


@pytest.fixture
def postgres_engine() -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    engine = create_database_engine(database_url)
    drop_schema(engine)
    create_schema(engine)
    yield engine
    drop_schema(engine)
    engine.dispose()


def test_durable_snapshot_plus_event_replay_reconstructs_world(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)

    event_store = PostgresEventStore(postgres_engine)
    snapshot_store = PostgresSnapshotStore(postgres_engine)

    initial = create_world(manifest)

    at_snapshot = deepcopy(initial)
    first_half = advance_ticks(at_snapshot, 5_000)
    event_store.append_many(first_half)
    snapshot_store.save(at_snapshot)

    expected = deepcopy(at_snapshot)
    second_half = advance_ticks(expected, 5_000)
    event_store.append_many(second_half)

    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert event_store.count_events(manifest.experiment.id) == 10_000
    assert restored.tick == 10_000
    assert restored.last_sequence_number == 10_000
    assert world_state_hash(restored) == world_state_hash(expected)


def test_event_store_rejects_sequence_gaps(postgres_engine: Engine) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)

    event = make_tick_event(
        experiment_id=manifest.experiment.id,
        sequence_number=2,
        world_tick=1,
    )

    with pytest.raises(EventSequenceError):
        PostgresEventStore(postgres_engine).append(event)


def test_experiment_manifest_is_immutable(postgres_engine: Engine) -> None:
    manifest = load_manifest(MANIFEST)
    first_hash = register_experiment(postgres_engine, manifest)

    same_hash = register_experiment(postgres_engine, manifest)
    assert same_hash == first_hash

    changed_identity = manifest.experiment.model_copy(update={"seed": 999})
    changed_manifest = manifest.model_copy(update={"experiment": changed_identity})

    with pytest.raises(ManifestConflictError):
        register_experiment(postgres_engine, changed_manifest)
