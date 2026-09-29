import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
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
from dse.engine.tool_broker import advance_tool_runtime_ticks
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider
from dse.tools.fake import DeterministicFakeExecutor


MANIFEST = Path("experiments/v0_1/tool-broker-fake.yaml")
M6_MANIFEST = Path("experiments/v0_1/action-intent-fake.yaml")


class CapturingProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return await self.inner.generate(request)


class UnsafeTargetProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.context.get("active_goal") is None:
            return await self.inner.generate(request)

        request_hash = state_hash(request.model_dump(mode="json"))
        decision = CognitionDecision(
            decision="propose_action",
            reason_summary="Propose an intentionally unsafe target for policy testing.",
            confidence=0.9,
            focus="artifact",
            action=ActionProposal(
                kind="draft_artifact",
                summary="Draft outside the allowed workspace.",
                target="../escape.md",
                rationale="This proposal should be rejected by broker policy.",
                expected_value=0.5,
                estimated_cost=0.2,
                draft_content="# Unsafe target test\n",
            ),
        )
        material = {
            "provider": "unsafe-target-fake",
            "decision": decision.model_dump(mode="json"),
            "request_hash": request_hash,
        }
        return ModelResponse(
            call_id=request.call_id,
            provider="unsafe-target-fake",
            model="unsafe-target-v0.1",
            model_version="1",
            decision=decision,
            usage=ModelUsage(input_tokens=0, output_tokens=0),
            request_hash=request_hash,
            response_hash=state_hash(material),
        )


def test_m6_manifest_keeps_tool_broker_disabled() -> None:
    manifest = load_manifest(M6_MANIFEST)

    assert manifest.agents.actions.enabled is True
    assert manifest.agents.tool_broker.enabled is False
    assert manifest.agents.tool_broker.executions_per_cycle == 0


def test_fake_tool_broker_executes_only_typed_intents() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicFakeProvider()
    executor = DeterministicFakeExecutor()
    world = create_world(manifest)

    events = asyncio.run(
        advance_tool_runtime_ticks(
            world,
            manifest,
            provider,
            executor,
            900,
        )
    )

    counts = Counter(event.event_type for event in events)

    assert counts["agent.goal.created"] == 5
    assert counts["agent.action.proposed"] == 10
    assert counts["resource.tool_execution.consumed"] == 10
    assert counts["tool.execution.started"] == 10
    assert counts["tool.execution.completed"] == 10
    assert counts["tool.execution.rejected"] == 0
    assert counts["agent.goal.progressed"] == 10
    assert counts["agent.goal.completed"] == 5

    assert manifest.runtime.forge_enabled is False
    assert manifest.runtime.sandbox_enabled is False
    assert manifest.runtime.web_enabled is False

    for agent in world.agents.values():
        assert agent.resources.tool_executions_remaining == 0
        assert len(agent.actions.proposals) == 2
        assert len(agent.actions.executions) == 2
        assert all(
            action.status == ActionStatus.EXECUTED
            for action in agent.actions.proposals
        )
        assert all(
            execution.executor == "deterministic-fake"
            and execution.success
            and execution.result_data["simulated"] is True
            for execution in agent.actions.executions
        )


def test_fake_tool_results_enter_later_cognition_context() -> None:
    manifest = load_manifest(MANIFEST)
    provider = CapturingProvider()
    executor = DeterministicFakeExecutor()
    world = create_world(manifest)

    asyncio.run(
        advance_tool_runtime_ticks(
            world,
            manifest,
            provider,
            executor,
            450,
        )
    )

    at_300 = [
        request
        for request in provider.requests
        if request.world_tick == 300
    ]
    at_450 = [
        request
        for request in provider.requests
        if request.world_tick == 450
    ]

    assert len(at_300) == 5
    assert len(at_450) == 5
    assert all(request.context["tool_results"] == [] for request in at_300)
    assert all(len(request.context["tool_results"]) == 1 for request in at_450)
    assert all(
        request.context["tool_results"][0]["result_type"]
        == "workspace_inspection"
        for request in at_450
    )


def test_tool_execution_budget_resets_on_wake() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)

    asyncio.run(
        advance_tool_runtime_ticks(
            world,
            manifest,
            DeterministicFakeProvider(),
            DeterministicFakeExecutor(),
            1440,
        )
    )

    for agent in world.agents.values():
        assert agent.resources.cycles_completed == 1
        assert (
            agent.resources.tool_executions_remaining
            == manifest.agents.tool_broker.executions_per_cycle
        )


def test_execution_budget_rejects_extra_pending_action() -> None:
    manifest = load_manifest(MANIFEST)
    limited_broker = manifest.agents.tool_broker.model_copy(
        update={"executions_per_cycle": 1}
    )
    agents = manifest.agents.model_copy(
        update={"tool_broker": limited_broker}
    )
    limited_manifest = manifest.model_copy(update={"agents": agents})

    world = create_world(limited_manifest)
    events = asyncio.run(
        advance_tool_runtime_ticks(
            world,
            limited_manifest,
            DeterministicFakeProvider(),
            DeterministicFakeExecutor(),
            450,
        )
    )

    counts = Counter(event.event_type for event in events)
    assert counts["tool.execution.completed"] == 5
    assert counts["tool.execution.rejected"] == 5

    rejected = [
        event
        for event in events
        if event.event_type == "tool.execution.rejected"
    ]
    assert all(
        event.payload["reason"] == "execution_budget_exhausted"
        for event in rejected
    )

    for agent in world.agents.values():
        assert agent.resources.tool_executions_remaining == 0
        assert [action.status for action in agent.actions.proposals] == [
            ActionStatus.EXECUTED,
            ActionStatus.REJECTED,
        ]


def test_tool_policy_rejects_parent_traversal_without_consuming_budget() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)

    events = asyncio.run(
        advance_tool_runtime_ticks(
            world,
            manifest,
            UnsafeTargetProvider(),
            DeterministicFakeExecutor(),
            300,
        )
    )

    counts = Counter(event.event_type for event in events)
    assert counts["agent.action.proposed"] == 5
    assert counts["tool.execution.rejected"] == 5
    assert counts["resource.tool_execution.consumed"] == 0

    rejected = [
        event
        for event in events
        if event.event_type == "tool.execution.rejected"
    ]
    assert all(
        event.payload["reason"] == "unsafe_parent_traversal"
        for event in rejected
    )

    for agent in world.agents.values():
        assert (
            agent.resources.tool_executions_remaining
            == manifest.agents.tool_broker.executions_per_cycle
        )
        assert agent.actions.proposals[0].status == ActionStatus.REJECTED


def test_tool_broker_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)

    expected = create_world(manifest)
    events = asyncio.run(
        advance_tool_runtime_ticks(
            expected,
            manifest,
            DeterministicFakeProvider(),
            DeterministicFakeExecutor(),
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


def test_tool_broker_roundtrips_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)

    expected = create_world(manifest)
    events = asyncio.run(
        advance_tool_runtime_ticks(
            expected,
            manifest,
            DeterministicFakeProvider(),
            DeterministicFakeExecutor(),
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
        len(agent.actions.executions) == 2
        and all(
            action.status == ActionStatus.EXECUTED
            for action in agent.actions.proposals
        )
        for agent in restored.agents.values()
    )
