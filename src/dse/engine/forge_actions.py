from dse.contracts.agent import ActionStatus, ForgeActionResultRecord
from dse.contracts.event import WorldEvent, make_event
from dse.contracts.experiment import ExperimentManifest
from dse.engine.forge_world import (
    commit_forge_artifact,
    create_forge_repository,
    list_repository_files,
)
from dse.engine.hashing import state_hash
from dse.engine.reducer import apply_event
from dse.engine.structured_cognition import advance_structured_cognition_tick
from dse.engine.world import WorldState
from dse.forge.base import ForgeProvider
from dse.providers.base import ModelProvider


FORGE_ACTION_KINDS = {
    "forge_create_repository",
    "forge_inspect_repository",
    "forge_publish_artifact",
}


async def process_forge_actions(
    world: WorldState,
    manifest: ExperimentManifest,
    provider: ForgeProvider,
) -> list[WorldEvent]:
    events: list[WorldEvent] = []

    for agent_id in sorted(world.agents):
        agent = world.agents[agent_id]
        pending = [
            action
            for action in agent.actions.proposals
            if (
                action.status == ActionStatus.PROPOSED
                and action.kind in FORGE_ACTION_KINDS
            )
        ]

        for action in pending:
            if agent.resources.forge_operations_remaining <= 0:
                events.append(
                    _terminal_event(
                        world,
                        actor_agent_id=agent_id,
                        action_id=action.action_id,
                        operation=_operation_name(action.kind),
                        status="rejected",
                        reason="forge_operation_budget_exhausted",
                        result_data={},
                    )
                )
                continue

            events.append(
                _emit(
                    world,
                    event_type="resource.forge_operation.consumed",
                    actor_agent_id=agent_id,
                    payload={"amount": 1},
                )
            )

            if action.kind == "forge_create_repository":
                outcome, operation_events = await create_forge_repository(
                    world,
                    manifest,
                    provider,
                    actor_agent_id=agent_id,
                    name=action.target,
                )
                events.extend(operation_events)
                result_data = {
                    "repo_id": outcome.repo_id,
                    "name": action.target,
                }
                events.append(
                    _terminal_event(
                        world,
                        actor_agent_id=agent_id,
                        action_id=action.action_id,
                        operation="create_repository",
                        status="completed" if outcome.accepted else "rejected",
                        reason=outcome.reason,
                        result_data=result_data,
                    )
                )
                continue

            if action.kind == "forge_inspect_repository":
                repository = world.forge.repositories.get(action.target)
                if repository is None:
                    events.append(
                        _emit(
                            world,
                            event_type="forge.operation.rejected",
                            actor_agent_id=agent_id,
                            payload={
                                "operation": "inspect_repository",
                                "reason": "repository_not_found",
                                "details": {"repo_id": action.target},
                            },
                        )
                    )
                    events.append(
                        _terminal_event(
                            world,
                            actor_agent_id=agent_id,
                            action_id=action.action_id,
                            operation="inspect_repository",
                            status="rejected",
                            reason="repository_not_found",
                            result_data={"repo_id": action.target},
                        )
                    )
                    continue

                artifacts = list_repository_files(world, action.target)
                limit = manifest.runtime.forge.inspection_artifact_limit
                content_chars = manifest.runtime.forge.inspection_content_chars
                selected = artifacts[:limit]
                result_data = {
                    "repo_id": repository.repo_id,
                    "name": repository.name,
                    "head_commit_id": repository.head_commit_id,
                    "artifact_count": repository.artifact_count,
                    "files": [
                        {
                            "artifact_id": artifact.artifact_id,
                            "path": artifact.path,
                            "content": artifact.content[:content_chars],
                            "content_truncated": (
                                len(artifact.content) > content_chars
                            ),
                            "content_sha256": artifact.content_sha256,
                            "creator_agent_id": artifact.creator_agent_id,
                            "creator_generation": artifact.creator_generation,
                            "previous_artifact_id": artifact.previous_artifact_id,
                            "parent_artifact_ids": list(
                                artifact.parent_artifact_ids
                            ),
                        }
                        for artifact in selected
                    ],
                    "inspection_truncated": len(artifacts) > limit,
                }
                events.append(
                    _terminal_event(
                        world,
                        actor_agent_id=agent_id,
                        action_id=action.action_id,
                        operation="inspect_repository",
                        status="completed",
                        reason="repository_inspected",
                        result_data=result_data,
                    )
                )
                continue

            if action.kind == "forge_publish_artifact":
                if action.repo_id is None or action.draft_content is None:
                    raise ValueError(
                        "Validated forge_publish_artifact is missing required data"
                    )
                outcome, operation_events = await commit_forge_artifact(
                    world,
                    manifest,
                    provider,
                    actor_agent_id=agent_id,
                    repo_id=action.repo_id,
                    path=action.target,
                    content=action.draft_content,
                    message=action.summary,
                    source_action_id=action.action_id,
                    parent_artifact_ids=action.parent_artifact_ids,
                    expected_parent_commit_id=action.expected_parent_commit_id,
                )
                events.extend(operation_events)
                result_data = {
                    "repo_id": outcome.repo_id,
                    "commit_id": outcome.commit_id,
                    "artifact_id": outcome.artifact_id,
                    "path": action.target,
                    "parent_artifact_ids": list(action.parent_artifact_ids),
                }
                if outcome.accepted and outcome.artifact_id is not None:
                    artifact = world.forge.artifacts[outcome.artifact_id]
                    result_data["parent_artifact_ids"] = list(
                        artifact.parent_artifact_ids
                    )
                    result_data["previous_artifact_id"] = (
                        artifact.previous_artifact_id
                    )
                    result_data["content_sha256"] = artifact.content_sha256

                events.append(
                    _terminal_event(
                        world,
                        actor_agent_id=agent_id,
                        action_id=action.action_id,
                        operation="publish_artifact",
                        status="completed" if outcome.accepted else "rejected",
                        reason=outcome.reason,
                        result_data=result_data,
                    )
                )

    return events


async def advance_forge_action_runtime_tick(
    world: WorldState,
    manifest: ExperimentManifest,
    model_provider: ModelProvider,
    forge_provider: ForgeProvider,
) -> list[WorldEvent]:
    events = await advance_structured_cognition_tick(
        world,
        manifest,
        model_provider,
    )
    events.extend(
        await process_forge_actions(
            world,
            manifest,
            forge_provider,
        )
    )
    return events


async def advance_forge_action_runtime_ticks(
    world: WorldState,
    manifest: ExperimentManifest,
    model_provider: ModelProvider,
    forge_provider: ForgeProvider,
    count: int,
) -> list[WorldEvent]:
    if count < 0:
        raise ValueError("count must be non-negative")

    events: list[WorldEvent] = []
    for _ in range(count):
        events.extend(
            await advance_forge_action_runtime_tick(
                world,
                manifest,
                model_provider,
                forge_provider,
            )
        )
    return events


def _operation_name(action_kind: str) -> str:
    mapping = {
        "forge_create_repository": "create_repository",
        "forge_inspect_repository": "inspect_repository",
        "forge_publish_artifact": "publish_artifact",
    }
    try:
        return mapping[action_kind]
    except KeyError as error:
        raise ValueError(f"Unsupported forge action kind: {action_kind}") from error


def _terminal_event(
    world: WorldState,
    *,
    actor_agent_id: str,
    action_id: str,
    operation: str,
    status: str,
    reason: str,
    result_data: dict,
) -> WorldEvent:
    result_id = f"{action_id}:forge-result"
    material = {
        "result_id": result_id,
        "action_id": action_id,
        "created_tick": world.tick,
        "operation": operation,
        "status": status,
        "reason": reason,
        "result_data": result_data,
    }
    result = ForgeActionResultRecord(
        **material,
        result_hash=state_hash(material),
    )
    return _emit(
        world,
        event_type=(
            "forge.action.completed"
            if status == "completed"
            else "forge.action.rejected"
        ),
        actor_agent_id=actor_agent_id,
        payload={
            "action_id": action_id,
            "result": result.model_dump(mode="json"),
        },
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
