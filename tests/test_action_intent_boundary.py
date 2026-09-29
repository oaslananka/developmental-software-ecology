import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from dse.contracts.agent import ActionStatus
from dse.contracts.experiment import load_manifest
from dse.contracts.model import (
    ActionProposal,
    CognitionDecision,
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


MANIFEST = Path("experiments/v0_1/action-intent-fake.yaml")
M5_MANIFEST = Path("experiments/v0_1/goal-lifecycle-fake.yaml")


class CapturingProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return await self.inner.generate(request)


class BudgetIgnoringActionProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.context.get("active_goal") is None:
            return await self.inner.generate(request)

        request_hash = state_hash(request.model_dump(mode="json"))
        decision = CognitionDecision(
            decision="propose_action",
            reason_summary="Keep proposing the same bounded inspection intent.",
            confidence=0.9,
            focus="workspace",
            action=ActionProposal(
                kind="inspect_workspace",
                summary="Inspect workspace structure.",
                target="workspace",
                rationale="The active goal could benefit from another inspection.",
                expected_value=0.6,
                estimated_cost=0.2,
            ),
        )
        response_material = {
            "provider": "budget-ignoring-fake",
            "model": "action-v0.1",
            "decision": decision.model_dump(mode="json"),
            "request_hash": request_hash,
        }
        return ModelResponse(
            call_id=request.call_id,
            provider="budget-ignoring-fake",
            model="action-v0.1",
            model_version="1",
            decision=decision,
            usage=ModelUsage(input_tokens=0, output_tokens=0),
            request_hash=request_hash,
            response_hash=state_hash(response_material),
        )


def test_m5_manifest_keeps_actions_disabled_and_v03_schema() -> None:
    manifest = load_manifest(M5_MANIFEST)
    assert manifest.agents.actions.enabled is False
    assert manifest.agents.actions.proposals_per_cycle == 0

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
        "CognitionDecision/v0.3"
    }


def test_action_proposal_schema_rejects_unsafe_shape() -> None:
    with pytest.raises(ValidationError):
        ActionProposal(
            kind="draft_artifact",
            summary="Draft something.",
            target="notes/example.md",
            rationale="Externalize work.",
            expected_value=0.8,
            estimated_cost=0.3,
        )

    with pytest.raises(ValidationError):
        ActionProposal(
            kind="inspect_workspace",
            summary="Inspect.",
            target="workspace",
            rationale="Need context.",
            expected_value=0.8,
            estimated_cost=0.2,
            draft_content="not allowed",
        )

    with pytest.raises(ValidationError):
        ActionProposal(
            kind="shell_command",
            summary="Run arbitrary command.",
            target="host",
            rationale="Should never be accepted.",
            expected_value=1.0,
            estimated_cost=0.1,
        )


def test_m6_proposes_bounded_actions_without_execution() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()
    world = create_world(manifest)

    events = asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            900,
        )
    )

    counts = Counter(event.event_type for event in events)

    assert counts["agent.goal.created"] == 5
    assert counts["agent.action.proposed"] == 10
    assert counts["resource.action_proposal.consumed"] == 10
    assert counts["agent.goal.progressed"] == 10
    assert counts["agent.goal.completed"] == 5
    assert counts["agent.action.rejected"] == 0

    assert all(
        "execut" not in event.event_type
        and "tool." not in event.event_type
        for event in events
    )

    for agent in world.agents.values():
        assert agent.resources.action_proposals_remaining == 0
        assert len(agent.actions.proposals) == 2
        assert [action.kind for action in agent.actions.proposals] == [
            "inspect_workspace",
            "draft_artifact",
        ]
        assert all(
            action.status == ActionStatus.PROPOSED
            for action in agent.actions.proposals
        )
        assert agent.actions.proposals[0].draft_content is None
        assert agent.actions.proposals[1].draft_content is not None


def test_action_budget_resets_on_wake() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()
    world = create_world(manifest)

    asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            1440,
        )
    )

    for agent in world.agents.values():
        assert agent.resources.cycles_completed == 1
        assert (
            agent.resources.action_proposals_remaining
            == manifest.agents.actions.proposals_per_cycle
        )


def test_over_budget_action_proposals_are_explicitly_rejected() -> None:
    manifest = load_manifest(MANIFEST)
    provider = BudgetIgnoringActionProvider()
    world = create_world(manifest)

    events = asyncio.run(
        advance_structured_cognition_ticks(
            world,
            manifest,
            provider,
            900,
        )
    )

    counts = Counter(event.event_type for event in events)

    assert counts["agent.action.proposed"] == 10
    assert counts["resource.action_proposal.consumed"] == 10
    assert counts["agent.action.rejected"] == 15

    rejected = [
        event
        for event in events
        if event.event_type == "agent.action.rejected"
    ]
    assert all(
        event.payload["reason"] == "budget_exhausted"
        for event in rejected
    )

    for agent in world.agents.values():
        assert agent.resources.action_proposals_remaining == 0
        assert len(agent.actions.proposals) == 2


def test_action_intent_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()

    expected = create_world(manifest)
    events = asyncio.run(
        advance_structured_cognition_ticks(
            expected,
            manifest,
            provider,
            900,
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


def test_action_intents_roundtrip_through_postgres(
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
            900,
        )
    )

    PostgresEventStore(postgres_engine).append_many(events)
    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert all(
        len(agent.actions.proposals) == 2
        and agent.resources.action_proposals_remaining == 0
        for agent in restored.agents.values()
    )
