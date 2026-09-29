import hashlib

from dse.contracts.event import WorldEvent, deterministic_event_id, make_event
from dse.contracts.experiment import ExperimentManifest
from dse.contracts.forge import (
    ForgeArtifactRecord,
    ForgeCommitArtifactRequest,
    ForgeCommitRecord,
    ForgeCreateRepositoryRequest,
    ForgeOperationOutcome,
    ForgeRepositoryRecord,
    normalize_forge_path,
)
from dse.engine.reducer import apply_event
from dse.engine.world import WorldState
from dse.forge.base import ForgeProvider


_UNSET = object()


async def create_forge_repository(
    world: WorldState,
    manifest: ExperimentManifest,
    provider: ForgeProvider,
    *,
    actor_agent_id: str,
    name: str,
) -> tuple[ForgeOperationOutcome, list[WorldEvent]]:
    agent = _agent(world, actor_agent_id)

    rejection = _forge_rejection_reason(manifest, provider)
    if rejection is not None:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="create_repository",
            reason=rejection,
            details={"name": name},
        )

    if len(world.forge.repositories) >= manifest.runtime.forge.max_repositories:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="create_repository",
            reason="repository_limit_reached",
            details={"name": name},
        )

    if any(repo.name == name for repo in world.forge.repositories.values()):
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="create_repository",
            reason="repository_name_exists",
            details={"name": name},
        )

    request = ForgeCreateRepositoryRequest(
        experiment_id=world.experiment_id,
        operation_sequence=world.last_sequence_number + 1,
        world_tick=world.tick,
        actor_agent_id=actor_agent_id,
        actor_generation=agent.generation,
        name=name,
        default_branch=manifest.runtime.forge.default_branch,
    )
    result = await provider.create_repository(request)

    if result.provider != manifest.runtime.forge.provider:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="create_repository",
            reason="provider_result_mismatch",
            details={"provider": result.provider},
        )
    if result.repo_id in world.forge.repositories:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="create_repository",
            reason="repository_id_exists",
            details={"repo_id": result.repo_id},
        )

    repository = ForgeRepositoryRecord(
        repo_id=result.repo_id,
        name=name,
        created_tick=world.tick,
        creator_agent_id=actor_agent_id,
        creator_generation=agent.generation,
        default_branch=manifest.runtime.forge.default_branch,
    )
    event = _emit(
        world,
        event_type="forge.repository.created",
        actor_agent_id=actor_agent_id,
        payload={
            "provider": result.provider,
            "repository": repository.model_dump(mode="json"),
        },
    )
    return (
        ForgeOperationOutcome(
            accepted=True,
            reason="repository_created",
            repo_id=repository.repo_id,
        ),
        [event],
    )


async def commit_forge_artifact(
    world: WorldState,
    manifest: ExperimentManifest,
    provider: ForgeProvider,
    *,
    actor_agent_id: str,
    repo_id: str,
    path: str,
    content: str,
    message: str,
    media_type: str = "text/plain",
    source_action_id: str | None = None,
    parent_artifact_ids: list[str] | None = None,
    expected_parent_commit_id: str | None | object = _UNSET,
) -> tuple[ForgeOperationOutcome, list[WorldEvent]]:
    agent = _agent(world, actor_agent_id)

    rejection = _forge_rejection_reason(manifest, provider)
    if rejection is not None:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason=rejection,
            details={"repo_id": repo_id, "path": path},
        )

    repository = world.forge.repositories.get(repo_id)
    if repository is None:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="repository_not_found",
            details={"repo_id": repo_id, "path": path},
        )

    if (
        expected_parent_commit_id is not _UNSET
        and expected_parent_commit_id != repository.head_commit_id
    ):
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="stale_repository_head",
            details={
                "repo_id": repo_id,
                "expected_parent_commit_id": expected_parent_commit_id,
                "actual_head_commit_id": repository.head_commit_id,
            },
        )

    try:
        normalized_path = normalize_forge_path(path)
    except ValueError:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="unsafe_artifact_path",
            details={"repo_id": repo_id, "path": path},
        )

    encoded = content.encode("utf-8")
    if len(encoded) > manifest.runtime.forge.max_artifact_bytes:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="artifact_too_large",
            details={
                "repo_id": repo_id,
                "path": normalized_path,
                "content_bytes": len(encoded),
            },
        )

    if (
        repository.artifact_count
        >= manifest.runtime.forge.max_artifacts_per_repository
    ):
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="artifact_limit_reached",
            details={"repo_id": repo_id},
        )

    explicit_parents = list(parent_artifact_ids or [])
    if len(explicit_parents) != len(set(explicit_parents)):
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="duplicate_lineage_parent",
            details={"repo_id": repo_id, "path": normalized_path},
        )
    for parent_artifact_id in explicit_parents:
        if parent_artifact_id not in world.forge.artifacts:
            return _reject(
                world,
                actor_agent_id=actor_agent_id,
                operation="commit_artifact",
                reason="parent_artifact_not_found",
                details={"parent_artifact_id": parent_artifact_id},
            )

    previous_artifact_id = repository.path_heads.get(normalized_path)
    lineage: list[str] = []
    if previous_artifact_id is not None:
        lineage.append(previous_artifact_id)
    for parent_artifact_id in explicit_parents:
        if parent_artifact_id not in lineage:
            lineage.append(parent_artifact_id)

    content_sha256 = hashlib.sha256(encoded).hexdigest()
    request = ForgeCommitArtifactRequest(
        experiment_id=world.experiment_id,
        operation_sequence=world.last_sequence_number + 1,
        world_tick=world.tick,
        actor_agent_id=actor_agent_id,
        actor_generation=agent.generation,
        repo_id=repo_id,
        parent_commit_id=repository.head_commit_id,
        path=normalized_path,
        content_sha256=content_sha256,
        content_bytes=len(encoded),
        message=message,
        previous_artifact_id=previous_artifact_id,
        parent_artifact_ids=lineage,
    )
    result = await provider.commit_artifact(request)

    if result.provider != manifest.runtime.forge.provider:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="provider_result_mismatch",
            details={"provider": result.provider},
        )
    if result.commit_id in world.forge.commits:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="commit_id_exists",
            details={"commit_id": result.commit_id},
        )
    if result.artifact_id in world.forge.artifacts:
        return _reject(
            world,
            actor_agent_id=actor_agent_id,
            operation="commit_artifact",
            reason="artifact_id_exists",
            details={"artifact_id": result.artifact_id},
        )

    event_sequence = world.last_sequence_number + 1
    event_id = deterministic_event_id(world.experiment_id, event_sequence)
    artifact = ForgeArtifactRecord(
        artifact_id=result.artifact_id,
        repo_id=repo_id,
        path=normalized_path,
        created_tick=world.tick,
        creator_agent_id=actor_agent_id,
        creator_generation=agent.generation,
        source_event_id=event_id,
        source_action_id=source_action_id,
        commit_id=result.commit_id,
        content=content,
        content_sha256=content_sha256,
        content_bytes=len(encoded),
        media_type=media_type,
        previous_artifact_id=previous_artifact_id,
        parent_artifact_ids=lineage,
    )
    commit = ForgeCommitRecord(
        commit_id=result.commit_id,
        repo_id=repo_id,
        parent_commit_id=repository.head_commit_id,
        created_tick=world.tick,
        author_agent_id=actor_agent_id,
        author_generation=agent.generation,
        message=message,
        artifact_ids=[artifact.artifact_id],
    )
    event = _emit(
        world,
        event_type="forge.artifact.committed",
        actor_agent_id=actor_agent_id,
        payload={
            "provider": result.provider,
            "commit": commit.model_dump(mode="json"),
            "artifact": artifact.model_dump(mode="json"),
        },
    )
    return (
        ForgeOperationOutcome(
            accepted=True,
            reason="artifact_committed",
            repo_id=repo_id,
            commit_id=commit.commit_id,
            artifact_id=artifact.artifact_id,
        ),
        [event],
    )


def list_repository_files(
    world: WorldState,
    repo_id: str,
) -> list[ForgeArtifactRecord]:
    repository = _repository(world, repo_id)
    return [
        world.forge.artifacts[artifact_id]
        for path, artifact_id in sorted(repository.path_heads.items())
    ]


def read_repository_file(
    world: WorldState,
    repo_id: str,
    path: str,
) -> ForgeArtifactRecord:
    repository = _repository(world, repo_id)
    normalized_path = normalize_forge_path(path)
    try:
        artifact_id = repository.path_heads[normalized_path]
    except KeyError as error:
        raise KeyError(
            f"Unknown forge path in {repo_id}: {normalized_path}"
        ) from error
    return world.forge.artifacts[artifact_id]


def read_forge_artifact(
    world: WorldState,
    artifact_id: str,
) -> ForgeArtifactRecord:
    try:
        return world.forge.artifacts[artifact_id]
    except KeyError as error:
        raise KeyError(f"Unknown forge artifact: {artifact_id}") from error


def _forge_rejection_reason(
    manifest: ExperimentManifest,
    provider: ForgeProvider,
) -> str | None:
    if not manifest.runtime.forge_enabled or not manifest.runtime.forge.enabled:
        return "forge_disabled"
    if provider.provider_name != manifest.runtime.forge.provider:
        return "provider_mismatch"
    return None


def _agent(world: WorldState, actor_agent_id: str):
    try:
        return world.agents[actor_agent_id]
    except KeyError as error:
        raise ValueError(f"Unknown agent: {actor_agent_id}") from error


def _repository(world: WorldState, repo_id: str) -> ForgeRepositoryRecord:
    try:
        return world.forge.repositories[repo_id]
    except KeyError as error:
        raise KeyError(f"Unknown forge repository: {repo_id}") from error


def _reject(
    world: WorldState,
    *,
    actor_agent_id: str,
    operation: str,
    reason: str,
    details: dict,
) -> tuple[ForgeOperationOutcome, list[WorldEvent]]:
    event = _emit(
        world,
        event_type="forge.operation.rejected",
        actor_agent_id=actor_agent_id,
        payload={
            "operation": operation,
            "reason": reason,
            "details": details,
        },
    )
    return (
        ForgeOperationOutcome(
            accepted=False,
            reason=reason,
            repo_id=details.get("repo_id"),
        ),
        [event],
    )


def _emit(
    world: WorldState,
    *,
    event_type: str,
    actor_agent_id: str,
    payload: dict,
) -> WorldEvent:
    event = make_event(
        experiment_id=world.experiment_id,
        sequence_number=world.last_sequence_number + 1,
        world_tick=world.tick,
        event_type=event_type,
        actor_agent_id=actor_agent_id,
        payload=payload,
    )
    apply_event(world, event)
    return event
