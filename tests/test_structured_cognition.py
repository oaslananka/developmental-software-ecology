import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from dse.contracts.agent import LifecycleState
from dse.contracts.experiment import load_manifest
from dse.contracts.model import CognitionDecision, ModelRequest
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.structured_cognition import advance_structured_cognition_ticks
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider


MANIFEST = Path("experiments/v0_1/structured-cognition-fake.yaml")


def test_cognition_decision_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CognitionDecision.model_validate(
            {
                "decision": "idle",
                "reason_summary": "Nothing useful to do.",
                "confidence": 0.5,
                "unexpected_field": "not allowed",
            }
        )


def test_fake_provider_is_deterministic() -> None:
    request = ModelRequest(
        call_id="test-call",
        experiment_id="test-experiment",
        agent_id="agent-0001",
        world_tick=200,
        context={"activity_units_remaining": 760},
    )

    response_a = asyncio.run(DeterministicFakeProvider().generate(request))
    response_b = asyncio.run(DeterministicFakeProvider().generate(request))

    assert response_a == response_b
    assert response_a.request_hash == response_b.request_hash
    assert response_a.response_hash == response_b.response_hash


def test_model_call_budget_is_bounded_and_sleep_has_no_calls() -> None:
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
    expected_calls = (
        manifest.world.agent_count
        * manifest.agents.cognition.model_calls_per_cycle
    )

    assert provider.call_count == expected_calls
    assert counts["model.call.completed"] == expected_calls
    assert counts["resource.model_call.consumed"] == expected_calls
    assert counts["agent.cognition.decided"] == expected_calls

    for agent in world.agents.values():
        assert agent.lifecycle_state == LifecycleState.SLEEPING
        assert agent.resources.model_calls_remaining == 0
        assert agent.cognition.calls_completed == 4
        assert agent.cognition.last_cognition_tick == 800

    calls_before_sleep = provider.call_count
    asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            200,
        )
    )
    assert provider.call_count == calls_before_sleep


def test_structured_cognition_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()

    expected = create_world(manifest)
    events = asyncio.run(
        advance_structured_cognition_ticks(
            expected,
            manifest,
            provider,
            800,
        )
    )

    replayed = create_world(manifest)
    for event in events:
        apply_event(replayed, event)

    assert world_state_hash(replayed) == world_state_hash(expected)
    assert all(
        event.payload.get("provider") == "deterministic-fake"
        for event in events
        if event.event_type == "model.call.completed"
    )


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


def test_structured_cognition_roundtrips_through_postgres(
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
            400,
        )
    )

    PostgresEventStore(postgres_engine).append_many(events)
    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert provider.call_count == 10
    assert world_state_hash(restored) == world_state_hash(expected)
