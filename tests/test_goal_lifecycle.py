import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from dse.contracts.agent import GoalStatus
from dse.contracts.experiment import load_manifest
from dse.contracts.model import (
    CognitionDecision,
    GoalClosure,
    GoalProgressUpdate,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from dse.engine.hashing import state_hash
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.structured_cognition import advance_structured_cognition_ticks
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider


MANIFEST = Path("experiments/v0_1/goal-lifecycle-fake.yaml")
M4_MANIFEST = Path("experiments/v0_1/self-generated-goals-fake.yaml")


class CapturingProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return await self.inner.generate(request)


class AbandoningProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.context.get("active_goal") is None:
            return await self.inner.generate(request)

        request_hash = state_hash(request.model_dump(mode="json"))
        decision = CognitionDecision(
            decision="abandon_goal",
            reason_summary="The current objective is no longer worth pursuing.",
            confidence=0.9,
            focus="environment",
            goal_closure=GoalClosure(
                summary="The objective was intentionally abandoned after reassessment.",
                confidence=0.9,
            ),
        )
        response_material = {
            "provider": "abandoning-fake",
            "model": "abandoning-v0.1",
            "decision": decision.model_dump(mode="json"),
            "request_hash": request_hash,
        }
        return ModelResponse(
            call_id=request.call_id,
            provider="abandoning-fake",
            model="abandoning-v0.1",
            model_version="1",
            decision=decision,
            usage=ModelUsage(input_tokens=0, output_tokens=0),
            request_hash=request_hash,
            response_hash=state_hash(response_material),
        )


def test_m4_manifest_keeps_goal_lifecycle_disabled() -> None:
    manifest = load_manifest(M4_MANIFEST)
    assert manifest.agents.goals.enabled is True
    assert manifest.agents.goals.lifecycle_enabled is False

    provider = CapturingProvider()
    world = create_world(manifest)
    asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            200,
        )
    )

    assert {request.response_schema for request in provider.requests} == {
        "CognitionDecision/v0.2"
    }


def test_v03_goal_lifecycle_schema_is_strict() -> None:
    with pytest.raises(ValidationError):
        CognitionDecision(
            decision="update_goal",
            reason_summary="Progress happened.",
            confidence=0.8,
        )

    with pytest.raises(ValidationError):
        CognitionDecision(
            decision="complete_goal",
            reason_summary="Done.",
            confidence=0.9,
            goal_update=GoalProgressUpdate(
                progress=0.8,
                progress_summary="Wrong payload type for completion.",
                confidence=0.8,
            ),
        )


def test_fake_provider_completes_goal_through_monotonic_progress() -> None:
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
    assert counts["agent.goal.created"] == 5
    assert counts["agent.goal.progressed"] == 10
    assert counts["agent.goal.completed"] == 5
    assert counts["agent.goal.abandoned"] == 0
    assert counts["agent.goal.proposal_rejected"] == 0

    for agent in world.agents.values():
        assert agent.goals.active_goal_id is None
        assert len(agent.goals.goals) == 1

        goal = agent.goals.goals[0]
        assert goal.status == GoalStatus.COMPLETED
        assert goal.progress == 1.0
        assert goal.last_progress_tick == 800
        assert goal.completion_tick == 800
        assert goal.completion_summary


def test_completed_goal_remains_in_history_and_new_goal_can_start() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()
    world = create_world(manifest)

    asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            1600,
        )
    )

    for agent in world.agents.values():
        assert len(agent.goals.goals) == 2

        first, second = agent.goals.goals
        assert first.status == GoalStatus.COMPLETED
        assert first.progress == 1.0

        assert second.status == GoalStatus.ACTIVE
        assert second.progress == 0.0
        assert agent.goals.active_goal_id == second.goal_id
        assert second.created_tick == 1600


def test_active_goal_can_be_abandoned_without_deleting_history() -> None:
    manifest = load_manifest(MANIFEST)
    provider = AbandoningProvider()
    world = create_world(manifest)

    events = asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            400,
        )
    )

    counts = Counter(event.event_type for event in events)
    assert counts["agent.goal.created"] == 5
    assert counts["agent.goal.abandoned"] == 5

    for agent in world.agents.values():
        assert agent.goals.active_goal_id is None
        assert len(agent.goals.goals) == 1
        goal = agent.goals.goals[0]
        assert goal.status == GoalStatus.ABANDONED
        assert goal.abandonment_tick == 400
        assert goal.abandonment_summary
        assert goal.progress == 0.0


def test_goal_lifecycle_event_stream_replays_exactly() -> None:
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


def test_goal_lifecycle_roundtrips_through_postgres(
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
            800,
        )
    )

    PostgresEventStore(postgres_engine).append_many(events)
    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert all(
        agent.goals.active_goal_id is None
        and len(agent.goals.goals) == 1
        and agent.goals.goals[0].status == GoalStatus.COMPLETED
        for agent in restored.agents.values()
    )
