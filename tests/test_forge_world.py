import asyncio
import hashlib
import os
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dse.contracts.experiment import load_manifest
from dse.contracts.forge import (
    ForgeCommitArtifactRequest,
    ForgeCreateRepositoryRequest,
)
from dse.engine.forge_world import (
    commit_forge_artifact,
    create_forge_repository,
    list_repository_files,
    read_forge_artifact,
    read_repository_file,
)
from dse.engine.reducer import apply_event
from dse.engine.snapshot import restore_world, serialize_world, world_state_hash
from dse.engine.world import create_world
from dse.forge.deterministic import DeterministicMemoryForgeProvider
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world


MANIFEST = Path("experiments/v0_1/forge-world-deterministic.yaml")
M10_MANIFEST = Path("experiments/v0_1/sandbox-runner-protocol.yaml")


def test_previous_manifest_keeps_forge_world_disabled() -> None:
    manifest = load_manifest(M10_MANIFEST)

    assert manifest.runtime.forge_enabled is False
    assert manifest.runtime.forge.enabled is False
    assert manifest.runtime.forge.provider == "deterministic-memory"


def test_deterministic_provider_ids_are_stable_for_identical_requests() -> None:
    provider = DeterministicMemoryForgeProvider()

    repo_request = ForgeCreateRepositoryRequest(
        experiment_id="exp-1",
        operation_sequence=1,
        world_tick=0,
        actor_agent_id="agent-0001",
        actor_generation=0,
        name="culture",
        default_branch="main",
    )
    first_repo = asyncio.run(provider.create_repository(repo_request))
    second_repo = asyncio.run(provider.create_repository(repo_request))
    assert first_repo == second_repo

    commit_request = ForgeCommitArtifactRequest(
        experiment_id="exp-1",
        operation_sequence=2,
        world_tick=0,
        actor_agent_id="agent-0001",
        actor_generation=0,
        repo_id=first_repo.repo_id,
        parent_commit_id=None,
        path="docs/idea.md",
        content_sha256="a" * 64,
        content_bytes=12,
        message="initial idea",
        previous_artifact_id=None,
        parent_artifact_ids=[],
    )
    first_commit = asyncio.run(provider.commit_artifact(commit_request))
    second_commit = asyncio.run(provider.commit_artifact(commit_request))
    assert first_commit == second_commit
    assert first_commit.commit_id.startswith("commit-")
    assert first_commit.artifact_id.startswith("artifact-")


def test_repository_and_revision_are_persistent_cultural_state() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicMemoryForgeProvider()
    world = create_world(manifest)

    created, create_events = asyncio.run(
        create_forge_repository(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            name="culture",
        )
    )
    assert created.accepted is True
    assert len(create_events) == 1
    assert create_events[0].event_type == "forge.repository.created"

    first, first_events = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            repo_id=created.repo_id,
            path="docs/idea.md",
            content="# Idea\nVersion one.\n",
            message="publish initial idea",
        )
    )
    assert first.accepted is True

    second, second_events = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0002",
            repo_id=created.repo_id,
            path="docs/idea.md",
            content="# Idea\nVersion two by another agent.\n",
            message="refine shared idea",
            expected_parent_commit_id=first.commit_id,
        )
    )
    assert second.accepted is True
    assert len(first_events) == 1
    assert len(second_events) == 1

    repository = world.forge.repositories[created.repo_id]
    assert repository.commit_count == 2
    assert repository.artifact_count == 2
    assert repository.head_commit_id == second.commit_id

    old_artifact = read_forge_artifact(world, first.artifact_id)
    current = read_repository_file(
        world,
        created.repo_id,
        "docs/idea.md",
    )

    assert current.artifact_id == second.artifact_id
    assert current.creator_agent_id == "agent-0002"
    assert current.previous_artifact_id == first.artifact_id
    assert current.parent_artifact_ids == [first.artifact_id]
    assert old_artifact.content == "# Idea\nVersion one.\n"
    assert current.content_sha256 == hashlib.sha256(
        current.content.encode("utf-8")
    ).hexdigest()

    files = list_repository_files(world, created.repo_id)
    assert [artifact.path for artifact in files] == ["docs/idea.md"]

    # Private agent mutation does not erase public cultural state.
    world.agents["agent-0001"].cognition.last_reason_summary = "private change"
    assert read_forge_artifact(world, first.artifact_id).content.startswith(
        "# Idea"
    )


def test_artifact_can_reuse_lineage_across_repositories_and_agents() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicMemoryForgeProvider()
    world = create_world(manifest)

    repo_a, _ = asyncio.run(
        create_forge_repository(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            name="source",
        )
    )
    source, _ = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            repo_id=repo_a.repo_id,
            path="lib/pattern.md",
            content="Reusable pattern\n",
            message="publish pattern",
        )
    )

    repo_b, _ = asyncio.run(
        create_forge_repository(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0003",
            name="derived",
        )
    )
    derived, _ = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0003",
            repo_id=repo_b.repo_id,
            path="docs/application.md",
            content="Applied reusable pattern\n",
            message="reuse cultural artifact",
            parent_artifact_ids=[source.artifact_id],
        )
    )

    artifact = read_forge_artifact(world, derived.artifact_id)
    assert artifact.parent_artifact_ids == [source.artifact_id]
    assert artifact.previous_artifact_id is None
    assert artifact.creator_agent_id == "agent-0003"


def test_duplicate_repo_stale_head_unsafe_path_and_size_are_rejected() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicMemoryForgeProvider()
    world = create_world(manifest)

    repo, _ = asyncio.run(
        create_forge_repository(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            name="culture",
        )
    )

    duplicate, duplicate_events = asyncio.run(
        create_forge_repository(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0002",
            name="culture",
        )
    )
    assert duplicate.accepted is False
    assert duplicate.reason == "repository_name_exists"
    assert duplicate_events[0].event_type == "forge.operation.rejected"

    first, _ = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            repo_id=repo.repo_id,
            path="docs/a.md",
            content="a",
            message="first",
        )
    )

    stale, stale_events = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0002",
            repo_id=repo.repo_id,
            path="docs/b.md",
            content="b",
            message="stale",
            expected_parent_commit_id=None,
        )
    )
    assert stale.accepted is False
    assert stale.reason == "stale_repository_head"
    assert stale_events[0].event_type == "forge.operation.rejected"

    unsafe, _ = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0002",
            repo_id=repo.repo_id,
            path="../escape.md",
            content="x",
            message="unsafe",
        )
    )
    assert unsafe.accepted is False
    assert unsafe.reason == "unsafe_artifact_path"

    oversized, _ = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0002",
            repo_id=repo.repo_id,
            path="docs/large.md",
            content="x" * (manifest.runtime.forge.max_artifact_bytes + 1),
            message="too large",
        )
    )
    assert oversized.accepted is False
    assert oversized.reason == "artifact_too_large"

    assert world.forge.repositories[repo.repo_id].head_commit_id == first.commit_id
    assert world.forge.repositories[repo.repo_id].artifact_count == 1


def test_unknown_lineage_parent_is_rejected_without_cultural_mutation() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicMemoryForgeProvider()
    world = create_world(manifest)

    repo, _ = asyncio.run(
        create_forge_repository(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            name="culture",
        )
    )
    before = world_state_hash(world)

    outcome, events = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0002",
            repo_id=repo.repo_id,
            path="docs/derived.md",
            content="derived",
            message="bad lineage",
            parent_artifact_ids=["artifact-does-not-exist"],
        )
    )

    assert outcome.accepted is False
    assert outcome.reason == "parent_artifact_not_found"
    assert events[0].event_type == "forge.operation.rejected"
    assert world.forge.repositories[repo.repo_id].artifact_count == 0
    assert before != world_state_hash(world)  # audit sequence advances
    assert world.forge.artifacts == {}


def test_snapshot_roundtrip_preserves_full_public_artifact_content() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicMemoryForgeProvider()
    world = create_world(manifest)

    repo, _ = asyncio.run(
        create_forge_repository(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            name="culture",
        )
    )
    artifact, _ = asyncio.run(
        commit_forge_artifact(
            world,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            repo_id=repo.repo_id,
            path="docs/public.md",
            content="persistent public culture\n",
            message="publish",
        )
    )

    restored = restore_world(serialize_world(world))
    assert world_state_hash(restored) == world_state_hash(world)
    assert (
        read_forge_artifact(restored, artifact.artifact_id).content
        == "persistent public culture\n"
    )


def test_forge_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)
    provider = DeterministicMemoryForgeProvider()
    expected = create_world(manifest)
    events = []

    repo, emitted = asyncio.run(
        create_forge_repository(
            expected,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            name="culture",
        )
    )
    events.extend(emitted)

    first, emitted = asyncio.run(
        commit_forge_artifact(
            expected,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            repo_id=repo.repo_id,
            path="docs/idea.md",
            content="v1",
            message="v1",
        )
    )
    events.extend(emitted)

    _second, emitted = asyncio.run(
        commit_forge_artifact(
            expected,
            manifest,
            provider,
            actor_agent_id="agent-0002",
            repo_id=repo.repo_id,
            path="docs/idea.md",
            content="v2",
            message="v2",
            expected_parent_commit_id=first.commit_id,
        )
    )
    events.extend(emitted)

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


def test_forge_world_roundtrips_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)
    provider = DeterministicMemoryForgeProvider()
    expected = create_world(manifest)
    events = []

    repo, emitted = asyncio.run(
        create_forge_repository(
            expected,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            name="culture",
        )
    )
    events.extend(emitted)

    artifact, emitted = asyncio.run(
        commit_forge_artifact(
            expected,
            manifest,
            provider,
            actor_agent_id="agent-0001",
            repo_id=repo.repo_id,
            path="docs/public.md",
            content="persistent through postgres\n",
            message="publish culture",
        )
    )
    events.extend(emitted)

    PostgresEventStore(postgres_engine).append_many(events)
    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert (
        read_forge_artifact(restored, artifact.artifact_id).content
        == "persistent through postgres\n"
    )


def test_m11_uses_no_real_git_or_network_provider() -> None:
    manifest = load_manifest(MANIFEST)
    assert manifest.runtime.forge.provider == "deterministic-memory"

    source = Path("src/dse/forge/deterministic.py").read_text(encoding="utf-8")
    forbidden = (
        "httpx",
        "requests",
        "subprocess",
        "git ",
        "github",
        "forgejo",
    )
    assert all(token not in source.lower() for token in forbidden)
