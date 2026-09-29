import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from dse.contracts.agent import ActionStatus
from dse.contracts.event import make_event
from dse.contracts.experiment import load_manifest
from dse.contracts.model import ActionProposal, ModelRequest, ModelResponse
from dse.engine.forge_actions import (
    advance_forge_action_runtime_ticks,
    process_forge_actions,
)
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.structured_cognition import advance_structured_cognition_ticks
from dse.engine.tool_broker import process_tool_broker
from dse.engine.world import create_world
from dse.forge.deterministic import DeterministicMemoryForgeProvider
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider
from dse.tools.fake import DeterministicFakeExecutor


MANIFEST = Path("experiments/v0_1/agent-forge-actions-fake.yaml")
M11_MANIFEST = Path("experiments/v0_1/forge-world-deterministic.yaml")


class CapturingProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return await self.inner.generate(request)


def test_m11_manifest_keeps_agent_forge_actions_disabled() -> None:
    manifest = load_manifest(M11_MANIFEST)

    assert manifest.agents.actions.enabled is False
    assert manifest.runtime.forge.enabled is True
    assert manifest.runtime.forge.operations_per_cycle == 0


def test_forge_action_schema_is_strict() -> None:
    common = {
        "summary": "test",
        "target": "target",
        "rationale": "test contract",
        "expected_value": 0.8,
        "estimated_cost": 0.2,
    }

    with pytest.raises(ValidationError):
        ActionProposal(
            kind="forge_publish_artifact",
            draft_content="content",
            **common,
        )

    with pytest.raises(ValidationError):
        ActionProposal(
            kind="forge_create_repository",
            draft_content="not allowed",
            **common,
        )

    with pytest.raises(ValidationError):
        ActionProposal(
            kind="forge_inspect_repository",
            parent_artifact_ids=["artifact-parent"],
            **common,
        )


def test_five_agents_publish_inspect_and_reuse_public_culture() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)

    events = asyncio.run(
        advance_forge_action_runtime_ticks(
            world,
            manifest,
            DeterministicFakeProvider(),
            DeterministicMemoryForgeProvider(),
            1050,
        )
    )

    counts = Counter(event.event_type for event in events)
    assert counts["agent.goal.created"] == 5
    assert counts["agent.action.proposed"] == 20
    assert counts["resource.action_proposal.consumed"] == 20
    assert counts["resource.forge_operation.consumed"] == 20
    assert counts["forge.repository.created"] == 5
    assert counts["forge.artifact.committed"] == 10
    assert counts["forge.action.completed"] == 20
    assert counts["forge.action.rejected"] == 0
    assert counts["forge.operation.rejected"] == 0
    assert counts["agent.goal.progressed"] == 5
    assert counts["agent.goal.completed"] == 5

    assert len(world.forge.repositories) == 5
    assert len(world.forge.artifacts) == 10

    for agent_id, agent in world.agents.items():
        assert agent.resources.action_proposals_remaining == 0
        assert agent.resources.forge_operations_remaining == 0
        assert len(agent.actions.proposals) == 4
        assert len(agent.actions.forge_results) == 4
        assert all(
            action.status == ActionStatus.EXECUTED
            for action in agent.actions.proposals
        )
        assert agent.goals.active_goal_id is None
        assert agent.goals.goals[0].status.value == "completed"

        own_repo = next(
            repository
            for repository in world.forge.repositories.values()
            if repository.creator_agent_id == agent_id
        )
        assert own_repo.artifact_count == 2
        assert set(own_repo.path_heads) == {
            "notes/hypothesis.md",
            "notes/derived.md",
        }

        initial = world.forge.artifacts[
            own_repo.path_heads["notes/hypothesis.md"]
        ]
        derived = world.forge.artifacts[
            own_repo.path_heads["notes/derived.md"]
        ]
        assert initial.creator_agent_id == agent_id
        assert derived.creator_agent_id == agent_id
        assert len(derived.parent_artifact_ids) == 1

        parent = world.forge.artifacts[derived.parent_artifact_ids[0]]
        assert parent.creator_agent_id != agent_id
        assert parent.repo_id != own_repo.repo_id

        derived_action = next(
            action
            for action in agent.actions.proposals
            if action.kind == "forge_publish_artifact"
            and action.target == "notes/derived.md"
        )
        assert derived.source_action_id == derived_action.action_id
        assert derived_action.parent_artifact_ids == [
            parent.artifact_id
        ]


def test_inspection_evidence_enters_later_cognition_context() -> None:
    manifest = load_manifest(MANIFEST)
    provider = CapturingProvider()
    world = create_world(manifest)

    asyncio.run(
        advance_forge_action_runtime_ticks(
            world,
            manifest,
            provider,
            DeterministicMemoryForgeProvider(),
            750,
        )
    )

    at_750 = [
        request
        for request in provider.requests
        if request.world_tick == 750
    ]
    assert len(at_750) == 5

    for request in at_750:
        assert request.response_schema == "CognitionDecision/v0.5"
        assert len(request.context["forge_results"]) == 3
        inspection = request.context["forge_results"][-1]
        assert inspection["operation"] == "inspect_repository"
        assert inspection["status"] == "completed"
        files = inspection["result_data"]["files"]
        assert files
        assert files[0]["content"]
        assert files[0]["creator_agent_id"] != request.agent_id
        assert len(request.context["forge_world"]["repositories"]) == 5


def test_tool_broker_does_not_consume_forge_actions() -> None:
    manifest = load_manifest(MANIFEST)
    tool_broker = manifest.agents.tool_broker.model_copy(
        update={
            "enabled": True,
            "executions_per_cycle": 2,
            "executor": "deterministic-fake",
        }
    )
    agents = manifest.agents.model_copy(
        update={"tool_broker": tool_broker}
    )
    tool_manifest = manifest.model_copy(update={"agents": agents})
    world = create_world(tool_manifest)

    asyncio.run(
        advance_structured_cognition_ticks(
            world,
            tool_manifest,
            DeterministicFakeProvider(),
            300,
        )
    )

    assert all(
        agent.actions.proposals[0].kind == "forge_create_repository"
        and agent.actions.proposals[0].status == ActionStatus.PROPOSED
        for agent in world.agents.values()
    )

    events = asyncio.run(
        process_tool_broker(
            world,
            tool_manifest,
            DeterministicFakeExecutor(),
        )
    )
    assert events == []
    assert all(
        agent.actions.proposals[0].status == ActionStatus.PROPOSED
        for agent in world.agents.values()
    )


def test_zero_forge_budget_rejects_proposed_forge_action() -> None:
    manifest = load_manifest(MANIFEST)
    forge = manifest.runtime.forge.model_copy(
        update={"operations_per_cycle": 0}
    )
    runtime = manifest.runtime.model_copy(update={"forge": forge})
    zero_manifest = manifest.model_copy(update={"runtime": runtime})
    world = create_world(zero_manifest)

    asyncio.run(
        advance_structured_cognition_ticks(
            world,
            zero_manifest,
            DeterministicFakeProvider(),
            150,
        )
    )

    agent = world.agents["agent-0001"]
    action_id = "agent-0001:manual-forge-action"
    event = make_event(
        experiment_id=world.experiment_id,
        sequence_number=world.last_sequence_number + 1,
        world_tick=world.tick,
        event_type="agent.action.proposed",
        actor_agent_id="agent-0001",
        payload={
            "action_id": action_id,
            "created_tick": world.tick,
            "source_event_id": agent.goals.goals[0].source_event_id,
            "goal_id": agent.goals.active_goal_id,
            "kind": "forge_create_repository",
            "summary": "Create a public repository.",
            "target": "manual-culture",
            "rationale": "Test Forge operation budget rejection.",
            "expected_value": 0.8,
            "estimated_cost": 0.2,
            "draft_content": None,
            "repo_id": None,
            "parent_artifact_ids": [],
            "expected_parent_commit_id": None,
            "status": "proposed",
        },
    )
    apply_event(world, event)

    events = asyncio.run(
        process_forge_actions(
            world,
            zero_manifest,
            DeterministicMemoryForgeProvider(),
        )
    )

    assert [item.event_type for item in events] == [
        "forge.action.rejected"
    ]
    result = world.agents["agent-0001"].actions.forge_results[-1]
    assert result.reason == "forge_operation_budget_exhausted"
    assert agent.actions.proposals[-1].status == ActionStatus.REJECTED
    assert world.forge.repositories == {}


def test_forge_action_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)
    expected = create_world(manifest)

    events = asyncio.run(
        advance_forge_action_runtime_ticks(
            expected,
            manifest,
            DeterministicFakeProvider(),
            DeterministicMemoryForgeProvider(),
            1050,
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


def test_forge_actions_roundtrip_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)
    expected = create_world(manifest)

    events = asyncio.run(
        advance_forge_action_runtime_ticks(
            expected,
            manifest,
            DeterministicFakeProvider(),
            DeterministicMemoryForgeProvider(),
            750,
        )
    )

    PostgresEventStore(postgres_engine).append_many(events)
    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert len(restored.forge.repositories) == 5
    assert len(restored.forge.artifacts) == 10
    assert all(
        len(agent.actions.forge_results) == 4
        for agent in restored.agents.values()
    )
