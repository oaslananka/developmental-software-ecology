import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dse.contracts.agent import EpisodicMemory
from dse.contracts.experiment import load_manifest
from dse.contracts.model import (
    CognitionDecision,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from dse.engine.hashing import state_hash
from dse.engine.memory import retrieve_episodes
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.structured_cognition import advance_structured_cognition_ticks
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider


MANIFEST = Path("experiments/v0_1/bounded-memory-fake.yaml")


class CapturingProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        decision = CognitionDecision(
            decision="observe",
            reason_summary="Inspect the environment carefully.",
            confidence=0.9,
            focus="environment",
        )
        request_hash = state_hash(request.model_dump(mode="json"))
        response_material = {
            "provider": "capturing-fake",
            "model": "capturing-v0.1",
            "decision": decision.model_dump(mode="json"),
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "request_hash": request_hash,
        }
        return ModelResponse(
            call_id=request.call_id,
            provider="capturing-fake",
            model="capturing-v0.1",
            model_version="1",
            decision=decision,
            usage=ModelUsage(input_tokens=0, output_tokens=0),
            request_hash=request_hash,
            response_hash=state_hash(response_material),
        )


def test_retrieval_prefers_semantic_overlap_over_unrelated_salience() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)
    agent = world.agents["agent-0001"]

    agent.memory.episodes.extend(
        [
            EpisodicMemory(
                memory_id="matching",
                created_tick=100,
                source_event_id="event-1",
                content="Inspect the environment for useful signals.",
                salience=0.2,
                decision="observe",
                focus="environment",
            ),
            EpisodicMemory(
                memory_id="unrelated",
                created_tick=199,
                source_event_id="event-2",
                content="No higher-value action is required.",
                salience=0.95,
                decision="idle",
                focus=None,
            ),
        ]
    )

    results = retrieve_episodes(
        agent,
        query_text="inspect environment",
        current_tick=200,
        limit=1,
    )

    assert [memory.memory_id for memory in results] == ["matching"]


def test_memory_capacity_is_bounded_and_eviction_is_explicit() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()
    world = create_world(manifest)

    events = asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            800,
        )
    )

    counts = Counter(event.event_type for event in events)
    assert counts["memory.episode.recorded"] == 20
    assert counts["memory.episode.evicted"] == 5

    for agent in world.agents.values():
        assert len(agent.memory.episodes) == manifest.agents.memory.capacity
        assert agent.cognition.calls_completed == 4


def test_retrieved_memories_are_added_to_later_model_context() -> None:
    manifest = load_manifest(MANIFEST)
    provider = CapturingProvider()
    world = create_world(manifest)

    asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            400,
        )
    )

    first_wave = [
        request
        for request in provider.requests
        if request.world_tick == 200
    ]
    second_wave = [
        request
        for request in provider.requests
        if request.world_tick == 400
    ]

    assert len(first_wave) == 5
    assert len(second_wave) == 5
    assert all(request.context["episodic_memories"] == [] for request in first_wave)
    assert all(len(request.context["episodic_memories"]) == 1 for request in second_wave)
    assert all(
        request.context["episodic_memories"][0]["content"]
        == "Inspect the environment carefully."
        for request in second_wave
    )


def test_memory_event_stream_replays_exactly() -> None:
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
        len(agent.memory.episodes) == manifest.agents.memory.capacity
        for agent in replayed.agents.values()
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


def test_memory_roundtrips_through_postgres(postgres_engine: Engine) -> None:
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

    assert world_state_hash(restored) == world_state_hash(expected)
    assert all(len(agent.memory.episodes) == 2 for agent in restored.agents.values())
