import os
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dse.contracts.agent import LifecycleState
from dse.contracts.experiment import load_manifest
from dse.engine.cognitionless import advance_cognitionless_ticks
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world


MANIFEST = Path("experiments/v0_1/empty-world.yaml")


def test_one_lifecycle_cycle_is_deterministic_and_bounded() -> None:
    manifest = load_manifest(MANIFEST)
    cycle_ticks = (
        manifest.agents.lifecycle.active_ticks_per_cycle
        + manifest.agents.lifecycle.sleep_ticks_per_cycle
    )

    world_a = create_world(manifest)
    world_b = create_world(manifest)

    events_a = advance_cognitionless_ticks(world_a, manifest, cycle_ticks)
    events_b = advance_cognitionless_ticks(world_b, manifest, cycle_ticks)

    assert world_state_hash(world_a) == world_state_hash(world_b)

    counts = Counter(event.event_type for event in events_a)
    agent_count = manifest.world.agent_count
    active = manifest.agents.lifecycle.active_ticks_per_cycle
    sleeping = manifest.agents.lifecycle.sleep_ticks_per_cycle

    assert counts["world.tick.advanced"] == cycle_ticks
    assert counts["agent.lifecycle.wake"] == agent_count * 2
    assert counts["agent.lifecycle.sleep"] == agent_count
    assert counts["resource.activity.consumed"] == agent_count * active
    assert counts["agent.activity.idle"] == agent_count * active
    assert counts["resource.sleep.elapsed"] == agent_count * sleeping

    for agent in world_a.agents.values():
        assert agent.lifecycle_state == LifecycleState.AWAKE
        assert agent.resources.activity_units_remaining == active
        assert agent.resources.sleep_ticks_remaining == 0
        assert agent.resources.cycles_completed == 1

    assert len(events_a) == len(events_b)
    assert world_a.last_sequence_number == len(events_a)


def test_cognitionless_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)
    cycle_ticks = (
        manifest.agents.lifecycle.active_ticks_per_cycle
        + manifest.agents.lifecycle.sleep_ticks_per_cycle
        + 1
    )

    expected = create_world(manifest)
    events = advance_cognitionless_ticks(expected, manifest, cycle_ticks)

    replayed = create_world(manifest)
    for event in events:
        apply_event(replayed, event)

    assert world_state_hash(replayed) == world_state_hash(expected)

    for agent in replayed.agents.values():
        assert agent.lifecycle_state == LifecycleState.IDLE
        assert agent.resources.activity_units_remaining == (
            manifest.agents.lifecycle.active_ticks_per_cycle - 1
        )
        assert agent.resources.cycles_completed == 1


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


def test_cognitionless_runtime_roundtrips_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)

    cycle_ticks = (
        manifest.agents.lifecycle.active_ticks_per_cycle
        + manifest.agents.lifecycle.sleep_ticks_per_cycle
    )

    expected = create_world(manifest)
    events = advance_cognitionless_ticks(expected, manifest, cycle_ticks)
    PostgresEventStore(postgres_engine).append_many(events)

    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert restored.last_sequence_number == len(events)
