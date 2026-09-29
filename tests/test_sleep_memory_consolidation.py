import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dse.contracts.agent import LifecycleState
from dse.contracts.experiment import load_manifest
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.structured_cognition import advance_structured_cognition_ticks
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider


MANIFEST = Path("experiments/v0_1/sleep-memory-consolidation-fake.yaml")
M3_MANIFEST = Path("experiments/v0_1/bounded-memory-fake.yaml")


def test_sleep_memory_mechanic_is_disabled_for_m3_manifest() -> None:
    manifest = load_manifest(M3_MANIFEST)
    assert manifest.agents.sleep_memory.enabled is False


def test_sleep_entry_consolidates_and_forgets_once() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()
    world = create_world(manifest)

    events = asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            manifest.agents.lifecycle.active_ticks_per_cycle,
        )
    )

    counts = Counter(event.event_type for event in events)

    assert counts["memory.episode.recorded"] == 20
    assert counts["memory.episode.evicted"] == 5
    assert counts["memory.episode.consolidated"] == 10
    assert counts["memory.episode.forgotten"] == 5
    assert counts["agent.lifecycle.sleep"] == 5

    for agent in world.agents.values():
        assert agent.lifecycle_state == LifecycleState.SLEEPING
        assert len(agent.memory.episodes) == 2
        assert sorted(
            episode.salience
            for episode in agent.memory.episodes
        ) == [0.85, 0.85]

    second_batch = asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            100,
        )
    )
    second_counts = Counter(event.event_type for event in second_batch)
    assert second_counts["memory.episode.consolidated"] == 0
    assert second_counts["memory.episode.forgotten"] == 0


def test_consolidation_and_forgetting_replay_exactly() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()

    expected = create_world(manifest)
    events = asyncio.run(
        advance_structured_cognition_ticks(
            expected,
            manifest,
            provider,
            manifest.agents.lifecycle.active_ticks_per_cycle,
        )
    )

    replayed = create_world(manifest)
    for event in events:
        apply_event(replayed, event)

    assert world_state_hash(replayed) == world_state_hash(expected)


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


def test_sleep_memory_roundtrips_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)

    provider = DeterministicFakeProvider()
    expected = create_world(manifest)
    events = asyncio.run(
        advance_structured_cognition_ticks(
            expected,
            manifest,
            provider,
            manifest.agents.lifecycle.active_ticks_per_cycle,
        )
    )

    PostgresEventStore(postgres_engine).append_many(events)
    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert all(
        len(agent.memory.episodes) == 2
        for agent in restored.agents.values()
    )
