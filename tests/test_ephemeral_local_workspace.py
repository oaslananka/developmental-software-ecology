import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dse.contracts.agent import ActionIntentRecord, ActionStatus
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
from dse.tools.workspace import EphemeralLocalWorkspaceExecutor


MANIFEST = Path("experiments/v0_1/ephemeral-local-workspace.yaml")
M7_MANIFEST = Path("experiments/v0_1/tool-broker-fake.yaml")


class CapturingProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return await self.inner.generate(request)


class SymlinkEscapeProvider:
    def __init__(self) -> None:
        self.inner = DeterministicFakeProvider()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.context.get("active_goal") is None:
            return await self.inner.generate(request)

        request_hash = state_hash(request.model_dump(mode="json"))
        decision = CognitionDecision(
            decision="propose_action",
            reason_summary="Attempt a write through a symlinked workspace path.",
            confidence=0.9,
            focus="artifact",
            action=ActionProposal(
                kind="draft_artifact",
                summary="Draft through a symlink.",
                target="link/escape.md",
                rationale="This must be rejected by resolved-path containment.",
                expected_value=0.5,
                estimated_cost=0.2,
                draft_content="# Escape attempt\n",
            ),
        )
        material = {
            "provider": "symlink-escape-fake",
            "decision": decision.model_dump(mode="json"),
            "request_hash": request_hash,
        }
        return ModelResponse(
            call_id=request.call_id,
            provider="symlink-escape-fake",
            model="symlink-escape-v0.1",
            model_version="1",
            decision=decision,
            usage=ModelUsage(input_tokens=0, output_tokens=0),
            request_hash=request_hash,
            response_hash=state_hash(material),
        )


def _action(
    *,
    action_id: str,
    kind: str,
    target: str,
    draft_content: str | None = None,
) -> ActionIntentRecord:
    return ActionIntentRecord(
        action_id=action_id,
        created_tick=0,
        source_event_id=f"{action_id}:event",
        goal_id="goal-1",
        kind=kind,
        summary=f"{kind} {target}",
        target=target,
        rationale="Exercise the scoped local workspace boundary.",
        expected_value=0.8,
        estimated_cost=0.2,
        draft_content=draft_content,
    )


def test_m7_manifest_defaults_to_deterministic_fake_executor() -> None:
    manifest = load_manifest(M7_MANIFEST)

    assert manifest.agents.tool_broker.enabled is True
    assert manifest.agents.tool_broker.executor == "deterministic-fake"


def test_m8_manifest_pins_ephemeral_local_workspace_executor() -> None:
    manifest = load_manifest(MANIFEST)

    assert manifest.agents.tool_broker.enabled is True
    assert (
        manifest.agents.tool_broker.executor
        == "ephemeral-local-workspace"
    )
    assert manifest.runtime.forge_enabled is False
    assert manifest.runtime.sandbox_enabled is False
    assert manifest.runtime.web_enabled is False


def test_local_workspace_writes_real_bounded_artifact(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST)
    workspace = tmp_path / "workspace"
    executor = EphemeralLocalWorkspaceExecutor(workspace)
    world = create_world(manifest)

    events = asyncio.run(
        advance_tool_runtime_ticks(
            world,
            manifest,
            DeterministicFakeProvider(),
            executor,
            450,
        )
    )

    counts = Counter(event.event_type for event in events)
    assert counts["agent.action.proposed"] == 10
    assert counts["resource.tool_execution.consumed"] == 10
    assert counts["tool.execution.completed"] == 10
    assert counts["tool.execution.rejected"] == 0

    artifact = workspace / "notes" / "recurring-patterns.md"
    assert artifact.is_file()
    assert artifact.read_text(encoding="utf-8").startswith(
        "# Recurring Pattern Hypothesis"
    )

    executions = [
        execution
        for agent in world.agents.values()
        for execution in agent.actions.executions
    ]
    assert len(executions) == 10
    assert all(
        execution.executor == "ephemeral-local-workspace"
        and execution.result_data["simulated"] is False
        for execution in executions
    )
    assert sum(
        execution.result_type == "artifact_written"
        for execution in executions
    ) == 5


def test_real_workspace_results_enter_later_cognition_context(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(MANIFEST)
    provider = CapturingProvider()
    world = create_world(manifest)

    asyncio.run(
        advance_tool_runtime_ticks(
            world,
            manifest,
            provider,
            EphemeralLocalWorkspaceExecutor(tmp_path / "workspace"),
            600,
        )
    )

    at_600 = [
        request
        for request in provider.requests
        if request.world_tick == 600
    ]

    assert len(at_600) == 5
    assert all(len(request.context["tool_results"]) == 2 for request in at_600)
    assert all(
        [
            result["result_type"]
            for result in request.context["tool_results"]
        ]
        == ["workspace_inspection", "artifact_written"]
        for request in at_600
    )
    assert all(
        all(
            result["result_data"]["simulated"] is False
            for result in request.context["tool_results"]
        )
        for request in at_600
    )


def test_static_validation_never_executes_artifact_code(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    executor = EphemeralLocalWorkspaceExecutor(workspace)

    marker = tmp_path / "should-not-exist.txt"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
    )

    draft = _action(
        action_id="draft-1",
        kind="draft_artifact",
        target="code/payload.py",
        draft_content=source,
    )
    assert executor.policy_rejection_reason(draft) is None
    draft_result = asyncio.run(executor.execute(draft))

    validation = _action(
        action_id="validate-1",
        kind="run_validation",
        target="code/payload.py",
    )
    assert executor.policy_rejection_reason(validation) is None
    validation_result = asyncio.run(executor.execute(validation))

    assert draft_result.result_type == "artifact_written"
    assert validation_result.result_type == "validation_report"
    assert validation_result.result_data["passed"] is True
    assert marker.exists() is False


def test_symlink_escape_is_rejected_before_budget_consumption(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(MANIFEST)
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)

    world = create_world(manifest)
    events = asyncio.run(
        advance_tool_runtime_ticks(
            world,
            manifest,
            SymlinkEscapeProvider(),
            EphemeralLocalWorkspaceExecutor(workspace),
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
        event.payload["reason"] == "workspace_escape"
        for event in rejected
    )
    assert (outside / "escape.md").exists() is False

    for agent in world.agents.values():
        assert (
            agent.resources.tool_executions_remaining
            == manifest.agents.tool_broker.executions_per_cycle
        )
        assert agent.actions.proposals[0].status == ActionStatus.REJECTED


def test_workspace_enforces_file_and_entry_bounds(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "existing.txt").write_text("x", encoding="utf-8")

    executor = EphemeralLocalWorkspaceExecutor(
        workspace,
        max_file_bytes=8,
        max_entries=1,
    )

    oversized = _action(
        action_id="oversized",
        kind="draft_artifact",
        target="oversized.txt",
        draft_content="123456789",
    )
    new_entry = _action(
        action_id="new-entry",
        kind="draft_artifact",
        target="new.txt",
        draft_content="ok",
    )
    inspect = _action(
        action_id="inspect",
        kind="inspect_workspace",
        target="workspace",
    )

    assert executor.policy_rejection_reason(oversized) == "content_too_large"
    assert executor.policy_rejection_reason(new_entry) == "workspace_entry_limit"

    inspection = asyncio.run(executor.execute(inspect))
    assert inspection.result_data["entry_count"] == 1
    assert inspection.result_data["truncated"] is True


def test_broker_rejects_executor_manifest_mismatch(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)

    with pytest.raises(ValueError, match="Configured executor"):
        asyncio.run(
            advance_tool_runtime_ticks(
                world,
                manifest,
                DeterministicFakeProvider(),
                DeterministicFakeExecutor(),
                1,
            )
        )


def test_local_workspace_event_stream_replays_exactly(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(MANIFEST)

    expected = create_world(manifest)
    events = asyncio.run(
        advance_tool_runtime_ticks(
            expected,
            manifest,
            DeterministicFakeProvider(),
            EphemeralLocalWorkspaceExecutor(tmp_path / "workspace"),
            450,
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


def test_local_workspace_roundtrips_through_postgres(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)

    expected = create_world(manifest)
    events = asyncio.run(
        advance_tool_runtime_ticks(
            expected,
            manifest,
            DeterministicFakeProvider(),
            EphemeralLocalWorkspaceExecutor(tmp_path / "workspace"),
            450,
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
            execution.executor == "ephemeral-local-workspace"
            for execution in agent.actions.executions
        )
        for agent in restored.agents.values()
    )
