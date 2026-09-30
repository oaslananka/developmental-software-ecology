import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from dse.contracts.experiment import TurnoverConfig, load_manifest
from dse.engine.forge_actions import advance_forge_action_runtime_ticks
from dse.engine.hashing import state_hash
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.world import create_world
from dse.forge.deterministic import DeterministicMemoryForgeProvider
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider


MANIFEST = Path("experiments/v0_1/controlled-turnover-fake.yaml")
M12_MANIFEST = Path("experiments/v0_1/agent-forge-actions-fake.yaml")


def _providers():
    return DeterministicFakeProvider(), DeterministicMemoryForgeProvider()


def _advance(world, manifest, count: int):
    model_provider, forge_provider = _providers()
    return asyncio.run(
        advance_forge_action_runtime_ticks(
            world,
            manifest,
            model_provider,
            forge_provider,
            count,
        )
    )


def test_m12_manifest_keeps_turnover_disabled() -> None:
    manifest = load_manifest(M12_MANIFEST)

    assert manifest.agents.turnover.enabled is False
    assert manifest.agents.turnover.ticks == []
    assert manifest.agents.turnover.agent_ids == []


def test_turnover_config_requires_explicit_unique_schedule() -> None:
    with pytest.raises(ValidationError):
        TurnoverConfig(
            enabled=True,
            ticks=[150, 150],
            agent_ids=["agent-0001"],
        )

    with pytest.raises(ValidationError):
        TurnoverConfig(
            enabled=True,
            ticks=[150],
            agent_ids=[],
        )

    with pytest.raises(ValidationError):
        TurnoverConfig(
            enabled=True,
            ticks=[150],
            agent_ids=["bad-agent-id"],
        )


def test_turnover_deletes_private_state_but_preserves_public_culture() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)

    before_events = _advance(world, manifest, 140)
    assert Counter(event.event_type for event in before_events)[
        "agent.goal.completed"
    ] == 5

    public_hash_before = state_hash(world.forge.model_dump(mode="json"))
    old_artifact_ids = set(world.forge.artifacts)
    old_action_ids = {
        agent_id: {
            action.action_id
            for action in agent.actions.proposals
        }
        for agent_id, agent in world.agents.items()
    }

    for agent in world.agents.values():
        assert agent.generation == 0
        assert agent.cognition.calls_completed == 7
        assert len(agent.goals.goals) == 1
        assert len(agent.actions.proposals) == 4
        assert len(agent.actions.forge_results) == 4
        assert len(agent.memory.episodes) == 7

    turnover_events = _advance(world, manifest, 10)
    counts = Counter(event.event_type for event in turnover_events)
    assert counts["agent.lifecycle.turned_over"] == 5
    assert counts["agent.lifecycle.replaced"] == 5
    assert counts["agent.lifecycle.wake"] == 5

    assert state_hash(world.forge.model_dump(mode="json")) == public_hash_before
    assert set(world.forge.artifacts) == old_artifact_ids

    for agent_id, agent in world.agents.items():
        assert agent.generation == 1
        assert agent.birth_tick == 150
        assert agent.lifecycle_state.value == "idle"
        assert agent.resources.activity_units_remaining == 399
        assert agent.resources.model_calls_remaining == 7
        assert agent.resources.action_proposals_remaining == 4
        assert agent.resources.forge_operations_remaining == 4
        assert agent.cognition.calls_completed == 0
        assert agent.goals.goals == []
        assert agent.actions.proposals == []
        assert agent.actions.executions == []
        assert agent.actions.forge_results == []
        assert agent.functional_submission.current is None
        assert agent.memory.episodes == []
        assert agent.resources.model_calls_remaining == 7
        assert agent.resources.action_proposals_remaining == 4
        assert agent.resources.forge_operations_remaining == 4
        assert old_action_ids[agent_id]

    for artifact_id in old_artifact_ids:
        assert world.forge.artifacts[artifact_id].creator_generation == 0


def test_replacement_generation_reuses_pre_turnover_public_artifact() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)

    events = _advance(world, manifest, 280)
    counts = Counter(event.event_type for event in events)

    assert counts["agent.lifecycle.turned_over"] == 5
    assert counts["agent.lifecycle.replaced"] == 5
    assert counts["agent.goal.created"] == 10
    assert counts["agent.goal.progressed"] == 10
    assert counts["agent.goal.completed"] == 10
    assert counts["forge.repository.created"] == 10
    assert counts["forge.artifact.committed"] == 20
    assert counts["resource.forge_operation.consumed"] == 40
    assert counts["forge.action.completed"] == 40
    assert counts["forge.action.rejected"] == 0
    assert counts["forge.operation.rejected"] == 0

    assert len(world.forge.repositories) == 10
    assert len(world.forge.artifacts) == 20

    for agent_id, agent in world.agents.items():
        assert agent.generation == 1
        assert agent.goals.active_goal_id is None
        assert len(agent.goals.goals) == 1
        assert agent.goals.goals[0].status.value == "completed"
        assert len(agent.actions.proposals) == 4
        assert len(agent.actions.forge_results) == 4
        assert len(agent.memory.episodes) == 7

        current_repo = next(
            repo
            for repo in world.forge.repositories.values()
            if (
                repo.creator_agent_id == agent_id
                and repo.creator_generation == 1
            )
        )
        previous_repo = next(
            repo
            for repo in world.forge.repositories.values()
            if (
                repo.creator_agent_id == agent_id
                and repo.creator_generation == 0
            )
        )

        derived = world.forge.artifacts[
            current_repo.path_heads["notes/derived.md"]
        ]
        assert derived.creator_generation == 1
        assert len(derived.parent_artifact_ids) == 1

        parent = world.forge.artifacts[derived.parent_artifact_ids[0]]
        assert parent.creator_agent_id == agent_id
        assert parent.creator_generation == 0
        assert parent.repo_id == previous_repo.repo_id

        inspection = next(
            result
            for result in agent.actions.forge_results
            if result.operation == "inspect_repository"
        )
        inspected_generations = {
            file_data["creator_generation"]
            for file_data in inspection.result_data["files"]
        }
        assert inspected_generations == {0}

        current_action_ids = {
            action.action_id
            for action in agent.actions.proposals
        }
        for artifact_id in previous_repo.path_heads.values():
            old_artifact = world.forge.artifacts[artifact_id]
            assert old_artifact.source_action_id not in current_action_ids


def test_turnover_events_report_private_counts_without_private_content() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)

    events = _advance(world, manifest, 150)
    turnover_events = [
        event
        for event in events
        if event.event_type == "agent.lifecycle.turned_over"
    ]

    assert len(turnover_events) == 5
    for event in turnover_events:
        counts = event.payload["private_state_counts"]
        assert counts == {
            "cognition_calls": 7,
            "goals": 1,
            "actions": 4,
            "tool_executions": 0,
            "forge_results": 4,
            "culture_results": 0,
            "functional_submission": 0,
            "episodic_memories": 7,
        }
        assert "memory" not in event.payload
        assert "goals" not in event.payload
        assert len(event.payload["public_forge_hash"]) == 64
        assert len(event.payload["text_culture_hash"]) == 64
        assert len(event.payload["social_world_hash"]) == 64


def test_turnover_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)
    expected = create_world(manifest)

    events = _advance(expected, manifest, 280)

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


def test_turnover_roundtrips_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)
    expected = create_world(manifest)

    events = _advance(expected, manifest, 280)
    PostgresEventStore(postgres_engine).append_many(events)

    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert len(restored.forge.repositories) == 10
    assert len(restored.forge.artifacts) == 20
    assert all(
        agent.generation == 1
        for agent in restored.agents.values()
    )
