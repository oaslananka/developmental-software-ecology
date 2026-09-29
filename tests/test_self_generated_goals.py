import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from dse.contracts.agent import GoalStatus, LifecycleState
from dse.contracts.experiment import load_manifest
from dse.contracts.model import CognitionDecision, GoalProposal, ModelRequest
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.structured_cognition import advance_structured_cognition_ticks
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider


MANIFEST = Path("experiments/v0_1/self-generated-goals-fake.yaml")
PREVIOUS_MANIFEST = Path("experiments/v0_1/bounded-memory-fake.yaml")


class CapturingProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest):
        self.requests.append(request)
        return await self.inner.generate(request)


def test_goals_are_disabled_by_default_for_previous_manifests() -> None:
    manifest = load_manifest(PREVIOUS_MANIFEST)
    assert manifest.agents.goals.enabled is False


def test_goal_schema_requires_proposal_only_for_propose_goal() -> None:
    with pytest.raises(ValidationError):
        CognitionDecision(
            decision="propose_goal",
            reason_summary="Create a durable objective.",
            confidence=0.8,
        )

    proposal = GoalProposal(
        title="Study recurring signals",
        description="Track recurring signals and refine an explanation.",
        motivation_summary="Persistent inquiry may reveal useful structure.",
        expected_value=0.7,
        estimated_cost=0.4,
        confidence=0.8,
    )

    with pytest.raises(ValidationError):
        CognitionDecision(
            decision="observe",
            reason_summary="Observe first.",
            confidence=0.7,
            goal=proposal,
        )


def test_fake_provider_proposes_goal_only_when_enabled_and_absent() -> None:
    provider = DeterministicFakeProvider()
    request = ModelRequest(
        call_id="goal-call",
        experiment_id="goal-exp",
        agent_id="agent-0001",
        world_tick=200,
        context={
            "goal_generation_enabled": True,
            "active_goal": None,
        },
        response_schema="CognitionDecision/v0.2",
    )

    response = asyncio.run(provider.generate(request))

    assert response.decision.decision == "propose_goal"
    assert response.decision.goal is not None
    assert response.decision.goal.title


def test_goal_is_created_once_and_enters_later_context() -> None:
    manifest = load_manifest(MANIFEST)
    provider = CapturingProvider()
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
    assert counts["agent.goal.proposal_rejected"] == 0

    first_wave = [
        request for request in provider.requests if request.world_tick == 200
    ]
    second_wave = [
        request for request in provider.requests if request.world_tick == 400
    ]

    assert len(first_wave) == 5
    assert len(second_wave) == 5
    assert all(request.response_schema == "CognitionDecision/v0.2" for request in first_wave)
    assert all(request.context["active_goal"] is None for request in first_wave)
    assert all(request.context["active_goal"] is not None for request in second_wave)

    for agent in world.agents.values():
        assert len(agent.goals.goals) == 1
        assert agent.goals.active_goal_id is not None

        goal = agent.goals.goals[0]
        assert goal.goal_id == agent.goals.active_goal_id
        assert goal.source == "self_generated"
        assert goal.status == GoalStatus.ACTIVE
        assert goal.created_tick == 200


def test_goal_persists_across_sleep_and_wake() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()
    world = create_world(manifest)

    events = asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            1440,
        )
    )

    counts = Counter(event.event_type for event in events)
    assert counts["agent.goal.created"] == 5

    for agent in world.agents.values():
        assert agent.lifecycle_state == LifecycleState.AWAKE
        assert agent.resources.cycles_completed == 1
        assert len(agent.goals.goals) == 1
        assert agent.goals.active_goal_id == agent.goals.goals[0].goal_id
        assert agent.goals.goals[0].status == GoalStatus.ACTIVE


def test_goal_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)
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

    replayed = create_world(manifest)
    for event in events:
        apply_event(replayed, event)

    assert world_state_hash(replayed) == world_state_hash(expected)
    assert all(
        agent.goals.active_goal_id is not None
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


def test_goals_roundtrip_through_postgres(postgres_engine: Engine) -> None:
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
    assert all(
        len(agent.goals.goals) == 1
        and agent.goals.active_goal_id is not None
        for agent in restored.agents.values()
    )
