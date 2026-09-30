import asyncio
import os
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dse.contracts.experiment import load_manifest
from dse.engine.cultural_runtime import advance_cultural_runtime_ticks
from dse.engine.hashing import state_hash
from dse.engine.reducer import apply_event
from dse.engine.snapshot import world_state_hash
from dse.engine.text_social import (
    visible_social_messages,
    visible_social_threads,
)
from dse.engine.world import create_world
from dse.experiments.conditions import assess_condition_support
from dse.forge.deterministic import DeterministicMemoryForgeProvider
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world
from dse.providers.fake import DeterministicFakeProvider


T_MANIFEST = Path("experiments/v0_1/conditions/t-text-culture.yaml")
ES_MANIFEST = Path("experiments/v0_1/conditions/es-executable-social.yaml")


def _advance_t(world, manifest, count: int):
    return asyncio.run(
        advance_cultural_runtime_ticks(
            world,
            manifest,
            DeterministicFakeProvider(),
            count,
        )
    )


def _advance_es(world, manifest, count: int):
    return asyncio.run(
        advance_cultural_runtime_ticks(
            world,
            manifest,
            DeterministicFakeProvider(),
            count,
            DeterministicMemoryForgeProvider(),
        )
    )


def test_t_treatment_surfaces_exist_but_common_evaluator_gate_remains() -> None:
    manifest = load_manifest(T_MANIFEST)
    report = assess_condition_support(manifest)

    assert manifest.runtime.text_culture.enabled is True
    assert manifest.runtime.social.enabled is True
    assert manifest.runtime.social.mode == "direct"
    assert report.research_runtime_ready is False
    assert report.missing_surfaces == [
        "attested_hardened_evaluator_runtime",
    ]


def test_t_turnover_preserves_text_but_does_not_inherit_direct_inbox() -> None:
    manifest = load_manifest(T_MANIFEST)
    world = create_world(manifest)

    before = _advance_t(world, manifest, 140)
    before_counts = Counter(event.event_type for event in before)
    assert before_counts["culture.text.published"] == 10
    assert before_counts["social.message.sent"] == 10
    assert before_counts["agent.goal.completed"] == 5
    assert before_counts["functional.submission.published"] == 5

    text_hash = state_hash(world.text_culture.model_dump(mode="json"))
    social_hash = state_hash(world.social.model_dump(mode="json"))
    old_text_ids = set(world.text_culture.entries)
    old_message_ids = set(world.social.messages)

    turnover_events = _advance_t(world, manifest, 10)
    counts = Counter(event.event_type for event in turnover_events)
    assert counts["agent.lifecycle.turned_over"] == 5
    assert counts["agent.lifecycle.replaced"] == 5

    assert state_hash(world.text_culture.model_dump(mode="json")) == text_hash
    assert state_hash(world.social.model_dump(mode="json")) == social_hash
    assert set(world.text_culture.entries) == old_text_ids
    assert set(world.social.messages) == old_message_ids

    for agent_id, agent in world.agents.items():
        assert agent.generation == 1
        assert agent.actions.culture_results == []
        assert agent.functional_submission.current is None
        assert visible_social_messages(
            world,
            manifest,
            agent_id=agent_id,
        ) == []


def test_t_generation_one_reuses_text_lineage_and_only_sees_current_direct_messages() -> None:
    manifest = load_manifest(T_MANIFEST)
    world = create_world(manifest)

    events = _advance_t(world, manifest, 280)
    counts = Counter(event.event_type for event in events)

    assert counts["agent.goal.created"] == 10
    assert counts["agent.goal.completed"] == 10
    assert counts["culture.text.published"] == 20
    assert counts["social.message.sent"] == 20
    assert counts["resource.text_operation.consumed"] == 20
    assert counts["resource.social_operation.consumed"] == 20
    assert counts["culture.action.completed"] == 40
    assert counts["culture.action.rejected"] == 0

    assert len(world.text_culture.entries) == 20
    assert len(world.social.messages) == 20
    assert world.social.threads == {}

    for agent_id, agent in world.agents.items():
        assert agent.generation == 1
        assert len(agent.actions.proposals) == 5
        assert len(agent.actions.culture_results) == 4
        assert agent.functional_submission.current is not None
        assert agent.goals.active_goal_id is None
        assert agent.goals.goals[0].status.value == "completed"

        generation_one_entries = [
            entry
            for entry in world.text_culture.entries.values()
            if (
                entry.creator_agent_id == agent_id
                and entry.creator_generation == 1
            )
        ]
        assert len(generation_one_entries) == 2

        inherited = [
            entry
            for entry in generation_one_entries
            if any(
                world.text_culture.entries[parent_id].creator_agent_id == agent_id
                and world.text_culture.entries[parent_id].creator_generation == 0
                for parent_id in entry.parent_entry_ids
            )
        ]
        assert inherited

        visible = visible_social_messages(
            world,
            manifest,
            agent_id=agent_id,
        )
        assert visible
        assert all(message["created_tick"] > 150 for message in visible)
        assert all(message["sender_generation"] == 1 for message in visible)
        assert all(message["recipient_generation"] == 1 for message in visible)


def test_es_persists_public_text_and_issue_threads_across_turnover() -> None:
    manifest = load_manifest(ES_MANIFEST)
    world = create_world(manifest)

    before = _advance_es(world, manifest, 140)
    before_counts = Counter(event.event_type for event in before)
    assert before_counts["forge.repository.created"] == 5
    assert before_counts["forge.artifact.committed"] == 5
    assert before_counts["culture.text.published"] == 5
    assert before_counts["social.thread.opened"] == 5
    assert before_counts["agent.goal.completed"] == 5

    text_ids = set(world.text_culture.entries)
    thread_ids = set(world.social.threads)
    artifact_ids = set(world.forge.artifacts)

    _advance_es(world, manifest, 10)

    assert set(world.text_culture.entries) == text_ids
    assert set(world.social.threads) == thread_ids
    assert set(world.forge.artifacts) == artifact_ids

    for agent_id in world.agents:
        visible_threads = visible_social_threads(world, manifest)
        assert len(visible_threads) == 5
        assert all(thread["created_tick"] < 150 for thread in visible_threads)


def test_es_generation_one_uses_all_m15_treatment_surfaces() -> None:
    manifest = load_manifest(ES_MANIFEST)
    world = create_world(manifest)

    events = _advance_es(world, manifest, 280)
    counts = Counter(event.event_type for event in events)

    assert counts["forge.repository.created"] == 10
    assert counts["forge.artifact.committed"] == 10
    assert counts["culture.text.published"] == 10
    assert counts["social.thread.opened"] == 10
    assert counts["resource.forge_operation.consumed"] == 20
    assert counts["resource.text_operation.consumed"] == 10
    assert counts["resource.social_operation.consumed"] == 10
    assert counts["culture.action.completed"] == 20
    assert counts["forge.action.completed"] == 20
    assert counts["agent.goal.completed"] == 10
    assert counts["functional.submission.published"] == 10

    assert len(world.forge.repositories) == 10
    assert len(world.forge.artifacts) == 10
    assert len(world.text_culture.entries) == 10
    assert len(world.social.threads) == 10
    assert len(world.social.messages) == 10

    report = assess_condition_support(manifest)
    assert report.research_runtime_ready is False
    assert report.missing_surfaces == [
        "attested_hardened_evaluator_runtime",
    ]

    for agent_id in world.agents:
        generation_one_entries = [
            entry
            for entry in world.text_culture.entries.values()
            if (
                entry.creator_agent_id == agent_id
                and entry.creator_generation == 1
            )
        ]
        assert len(generation_one_entries) == 1
        parent_ids = generation_one_entries[0].parent_entry_ids
        assert len(parent_ids) == 1
        parent = world.text_culture.entries[parent_ids[0]]
        assert parent.creator_agent_id == agent_id
        assert parent.creator_generation == 0


def test_t_event_stream_replays_exactly_with_generation_scoped_social_state() -> None:
    manifest = load_manifest(T_MANIFEST)
    expected = create_world(manifest)

    events = _advance_t(expected, manifest, 280)

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


def test_t_text_social_state_roundtrips_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(T_MANIFEST)
    register_experiment(postgres_engine, manifest)
    expected = create_world(manifest)

    events = _advance_t(expected, manifest, 280)
    PostgresEventStore(postgres_engine).append_many(events)

    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert len(restored.text_culture.entries) == 20
    assert len(restored.social.messages) == 20
    assert all(agent.generation == 1 for agent in restored.agents.values())
