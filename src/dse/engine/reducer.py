from dse.contracts.agent import (
    ActionIntentRecord,
    ActionStatus,
    EpisodicMemory,
    ForgeActionResultRecord,
    GoalRecord,
    GoalStatus,
    LifecycleState,
    ToolExecutionRecord,
)
from dse.contracts.event import WorldEvent
from dse.contracts.forge import (
    ForgeArtifactRecord,
    ForgeCommitRecord,
    ForgeRepositoryRecord,
)
from dse.engine.world import WorldState


def apply_event(world: WorldState, event: WorldEvent) -> None:
    expected_sequence = world.last_sequence_number + 1
    if event.sequence_number != expected_sequence:
        raise ValueError(
            f"Non-contiguous event sequence: expected {expected_sequence}, "
            f"received {event.sequence_number}"
        )

    if event.experiment_id != world.experiment_id:
        raise ValueError("Event belongs to a different experiment")

    if event.world_tick < world.tick:
        raise ValueError("World tick cannot move backwards")

    match event.event_type:
        case "world.tick.advanced":
            expected_tick = world.tick + 1
            if event.world_tick != expected_tick:
                raise ValueError(
                    f"Tick event must advance exactly one step: expected {expected_tick}, "
                    f"received {event.world_tick}"
                )
            world.tick = event.world_tick

        case "agent.lifecycle.wake":
            agent = _agent_for_event(world, event)
            agent.lifecycle_state = LifecycleState.AWAKE
            agent.resources.activity_units_remaining = int(event.payload["activity_units"])
            agent.resources.sleep_ticks_remaining = 0
            agent.resources.model_calls_remaining = int(event.payload.get("model_calls", 0))
            agent.resources.action_proposals_remaining = int(
                event.payload.get("action_proposals", 0)
            )
            agent.resources.tool_executions_remaining = int(
                event.payload.get("tool_executions", 0)
            )
            agent.resources.forge_operations_remaining = int(
                event.payload.get("forge_operations", 0)
            )
            agent.resources.cycles_completed += int(
                event.payload.get("cycles_completed_delta", 0)
            )
            agent.state_version += 1

        case "resource.activity.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Activity consumption must be positive")
            if agent.resources.activity_units_remaining < amount:
                raise ValueError("Activity budget cannot become negative")
            agent.resources.activity_units_remaining -= amount
            agent.state_version += 1

        case "resource.model_call.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Model-call consumption must be positive")
            if agent.resources.model_calls_remaining < amount:
                raise ValueError("Model-call budget cannot become negative")
            agent.resources.model_calls_remaining -= amount
            agent.state_version += 1

        case "resource.action_proposal.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Action-proposal consumption must be positive")
            if agent.resources.action_proposals_remaining < amount:
                raise ValueError("Action-proposal budget cannot become negative")
            agent.resources.action_proposals_remaining -= amount
            agent.state_version += 1

        case "resource.tool_execution.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Tool-execution consumption must be positive")
            if agent.resources.tool_executions_remaining < amount:
                raise ValueError("Tool-execution budget cannot become negative")
            agent.resources.tool_executions_remaining -= amount
            agent.state_version += 1

        case "resource.forge_operation.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Forge-operation consumption must be positive")
            if agent.resources.forge_operations_remaining < amount:
                raise ValueError("Forge-operation budget cannot become negative")
            agent.resources.forge_operations_remaining -= amount
            agent.state_version += 1

        case "agent.activity.idle":
            agent = _agent_for_event(world, event)
            agent.lifecycle_state = LifecycleState.IDLE
            agent.last_active_tick = event.world_tick
            agent.state_version += 1

        case "agent.lifecycle.sleep":
            agent = _agent_for_event(world, event)
            sleep_ticks = int(event.payload["sleep_ticks"])
            if sleep_ticks <= 0:
                raise ValueError("Sleep duration must be positive")
            agent.lifecycle_state = LifecycleState.SLEEPING
            agent.resources.sleep_ticks_remaining = sleep_ticks
            agent.state_version += 1

        case "resource.sleep.elapsed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if agent.lifecycle_state != LifecycleState.SLEEPING:
                raise ValueError("Sleep progress requires a sleeping agent")
            if amount <= 0:
                raise ValueError("Sleep progress must be positive")
            if agent.resources.sleep_ticks_remaining < amount:
                raise ValueError("Sleep ticks cannot become negative")
            agent.resources.sleep_ticks_remaining -= amount
            agent.state_version += 1

        case "model.call.completed":
            pass

        case "agent.cognition.decided":
            agent = _agent_for_event(world, event)
            agent.cognition.calls_completed += 1
            agent.cognition.last_cognition_tick = event.world_tick
            agent.cognition.last_decision = str(event.payload["decision"])
            agent.cognition.last_reason_summary = str(event.payload["reason_summary"])
            agent.cognition.last_confidence = float(event.payload["confidence"])
            agent.state_version += 1

        case "agent.goal.created":
            agent = _agent_for_event(world, event)
            goal = GoalRecord.model_validate(event.payload)

            if goal.status != GoalStatus.ACTIVE:
                raise ValueError("New goals must start active")
            if agent.goals.active_goal_id is not None:
                raise ValueError("Agent already has an active goal")
            if any(existing.goal_id == goal.goal_id for existing in agent.goals.goals):
                raise ValueError(f"Duplicate goal ID: {goal.goal_id}")

            agent.goals.goals.append(goal)
            agent.goals.active_goal_id = goal.goal_id
            agent.state_version += 1

        case "agent.goal.progressed":
            agent = _agent_for_event(world, event)
            goal = _active_goal(agent, str(event.payload["goal_id"]))

            old_progress = float(event.payload["old_progress"])
            new_progress = float(event.payload["new_progress"])
            if abs(goal.progress - old_progress) > 1e-9:
                raise ValueError(
                    f"Goal progress mismatch for {goal.goal_id}: "
                    f"state={goal.progress}, event={old_progress}"
                )
            if not old_progress < new_progress < 1.0:
                raise ValueError(
                    "Progress event must increase progress and remain below completion"
                )

            goal.progress = new_progress
            goal.last_progress_tick = event.world_tick
            goal.last_progress_summary = str(event.payload["progress_summary"])
            agent.state_version += 1

        case "agent.goal.completed":
            agent = _agent_for_event(world, event)
            goal = _active_goal(agent, str(event.payload["goal_id"]))

            goal.progress = 1.0
            goal.last_progress_tick = event.world_tick
            goal.last_progress_summary = str(event.payload["summary"])
            goal.completion_tick = event.world_tick
            goal.completion_summary = str(event.payload["summary"])
            goal.status = GoalStatus.COMPLETED
            agent.goals.active_goal_id = None
            agent.state_version += 1

        case "agent.goal.abandoned":
            agent = _agent_for_event(world, event)
            goal = _active_goal(agent, str(event.payload["goal_id"]))

            goal.abandonment_tick = event.world_tick
            goal.abandonment_summary = str(event.payload["summary"])
            goal.status = GoalStatus.ABANDONED
            agent.goals.active_goal_id = None
            agent.state_version += 1

        case "agent.goal.proposal_rejected":
            pass

        case "agent.action.proposed":
            agent = _agent_for_event(world, event)
            action = ActionIntentRecord.model_validate(event.payload)

            if action.status != ActionStatus.PROPOSED:
                raise ValueError("New action intents must start proposed")
            if agent.goals.active_goal_id != action.goal_id:
                raise ValueError(
                    "Action intent must reference the currently active goal"
                )
            if any(
                existing.action_id == action.action_id
                for existing in agent.actions.proposals
            ):
                raise ValueError(f"Duplicate action ID: {action.action_id}")

            agent.actions.proposals.append(action)
            agent.state_version += 1

        case "agent.action.rejected":
            pass

        case "tool.execution.started":
            pass

        case "tool.execution.completed":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            execution = ToolExecutionRecord.model_validate(event.payload["execution"])

            if execution.action_id != action.action_id:
                raise ValueError("Execution action_id does not match proposal")
            if any(
                existing.execution_id == execution.execution_id
                for existing in agent.actions.executions
            ):
                raise ValueError(
                    f"Duplicate execution ID: {execution.execution_id}"
                )

            action.status = ActionStatus.EXECUTED
            agent.actions.executions.append(execution)
            agent.state_version += 1

        case "tool.execution.rejected":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            action.status = ActionStatus.REJECTED
            agent.state_version += 1

        case "sandbox.admission.evaluated":
            pass

        case "sandbox.execution.dispatched":
            pass

        case "sandbox.execution.completed":
            pass

        case "sandbox.execution.result_rejected":
            pass

        case "forge.repository.created":
            agent = _agent_for_event(world, event)
            repository = ForgeRepositoryRecord.model_validate(
                event.payload["repository"]
            )

            if repository.creator_agent_id != agent.agent_id:
                raise ValueError("Forge repository creator does not match actor")
            if repository.creator_generation != agent.generation:
                raise ValueError("Forge repository creator generation mismatch")
            if repository.created_tick != event.world_tick:
                raise ValueError("Forge repository created_tick mismatch")
            if repository.head_commit_id is not None:
                raise ValueError("New forge repository must have no head commit")
            if repository.commit_count != 0 or repository.artifact_count != 0:
                raise ValueError("New forge repository counters must start at zero")
            if repository.path_heads:
                raise ValueError("New forge repository must have no path heads")
            if repository.repo_id in world.forge.repositories:
                raise ValueError(f"Duplicate forge repository ID: {repository.repo_id}")
            if any(
                existing.name == repository.name
                for existing in world.forge.repositories.values()
            ):
                raise ValueError(f"Duplicate forge repository name: {repository.name}")

            world.forge.repositories[repository.repo_id] = repository

        case "forge.artifact.committed":
            agent = _agent_for_event(world, event)
            commit = ForgeCommitRecord.model_validate(event.payload["commit"])
            artifact = ForgeArtifactRecord.model_validate(event.payload["artifact"])

            try:
                repository = world.forge.repositories[commit.repo_id]
            except KeyError as error:
                raise ValueError(
                    f"Unknown forge repository: {commit.repo_id}"
                ) from error

            if artifact.repo_id != repository.repo_id:
                raise ValueError("Artifact repository does not match commit repository")
            if artifact.commit_id != commit.commit_id:
                raise ValueError("Artifact commit_id does not match commit")
            if commit.artifact_ids != [artifact.artifact_id]:
                raise ValueError("M11 commits must contain exactly the committed artifact")
            if commit.parent_commit_id != repository.head_commit_id:
                raise ValueError(
                    "Forge commit parent does not match current repository head"
                )
            if commit.commit_id in world.forge.commits:
                raise ValueError(f"Duplicate forge commit ID: {commit.commit_id}")
            if artifact.artifact_id in world.forge.artifacts:
                raise ValueError(f"Duplicate forge artifact ID: {artifact.artifact_id}")

            if commit.author_agent_id != agent.agent_id:
                raise ValueError("Forge commit author does not match actor")
            if artifact.creator_agent_id != agent.agent_id:
                raise ValueError("Forge artifact creator does not match actor")
            if commit.author_generation != agent.generation:
                raise ValueError("Forge commit author generation mismatch")
            if artifact.creator_generation != agent.generation:
                raise ValueError("Forge artifact creator generation mismatch")
            if commit.created_tick != event.world_tick:
                raise ValueError("Forge commit created_tick mismatch")
            if artifact.created_tick != event.world_tick:
                raise ValueError("Forge artifact created_tick mismatch")
            if artifact.source_event_id != event.event_id:
                raise ValueError("Forge artifact source_event_id mismatch")

            encoded = artifact.content.encode("utf-8")
            if artifact.content_bytes != len(encoded):
                raise ValueError("Forge artifact content byte count mismatch")
            if artifact.content_sha256 != state_hash_bytes(encoded):
                raise ValueError("Forge artifact content hash mismatch")

            previous = repository.path_heads.get(artifact.path)
            if artifact.previous_artifact_id != previous:
                raise ValueError(
                    "Forge artifact previous revision does not match path head"
                )
            if previous is not None and previous not in artifact.parent_artifact_ids:
                raise ValueError(
                    "Forge revision lineage must include the previous artifact"
                )

            if len(artifact.parent_artifact_ids) != len(
                set(artifact.parent_artifact_ids)
            ):
                raise ValueError("Forge artifact lineage contains duplicate parents")
            for parent_artifact_id in artifact.parent_artifact_ids:
                if parent_artifact_id not in world.forge.artifacts:
                    raise ValueError(
                        f"Unknown parent forge artifact: {parent_artifact_id}"
                    )

            world.forge.commits[commit.commit_id] = commit
            world.forge.artifacts[artifact.artifact_id] = artifact
            repository.head_commit_id = commit.commit_id
            repository.commit_count += 1
            repository.artifact_count += 1
            repository.path_heads[artifact.path] = artifact.artifact_id

        case "forge.operation.rejected":
            _agent_for_event(world, event)

        case "forge.action.completed":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            result = ForgeActionResultRecord.model_validate(
                event.payload["result"]
            )
            if not action.kind.startswith("forge_"):
                raise ValueError("Forge action result requires forge_* action")
            if result.action_id != action.action_id:
                raise ValueError("Forge result action_id mismatch")
            if result.status != "completed":
                raise ValueError("Completed forge action requires completed result")
            if any(
                existing.result_id == result.result_id
                for existing in agent.actions.forge_results
            ):
                raise ValueError(f"Duplicate forge result ID: {result.result_id}")

            action.status = ActionStatus.EXECUTED
            agent.actions.forge_results.append(result)
            agent.state_version += 1

        case "forge.action.rejected":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            result = ForgeActionResultRecord.model_validate(
                event.payload["result"]
            )
            if not action.kind.startswith("forge_"):
                raise ValueError("Forge action rejection requires forge_* action")
            if result.action_id != action.action_id:
                raise ValueError("Forge rejection action_id mismatch")
            if result.status != "rejected":
                raise ValueError("Rejected forge action requires rejected result")
            if any(
                existing.result_id == result.result_id
                for existing in agent.actions.forge_results
            ):
                raise ValueError(f"Duplicate forge result ID: {result.result_id}")

            action.status = ActionStatus.REJECTED
            agent.actions.forge_results.append(result)
            agent.state_version += 1

        case "memory.episode.recorded":
            agent = _agent_for_event(world, event)
            memory = EpisodicMemory.model_validate(event.payload)
            if any(
                episode.memory_id == memory.memory_id
                for episode in agent.memory.episodes
            ):
                raise ValueError(f"Duplicate memory ID: {memory.memory_id}")
            agent.memory.episodes.append(memory)
            agent.state_version += 1

        case "memory.episode.evicted":
            agent = _agent_for_event(world, event)
            _remove_memory(agent, str(event.payload["memory_id"]))
            agent.state_version += 1

        case "memory.episode.consolidated":
            agent = _agent_for_event(world, event)
            memory_id = str(event.payload["memory_id"])
            episode = _find_memory(agent, memory_id)

            old_salience = float(event.payload["old_salience"])
            new_salience = float(event.payload["new_salience"])
            if abs(episode.salience - old_salience) > 1e-9:
                raise ValueError(
                    f"Consolidation old_salience mismatch for {memory_id}: "
                    f"state={episode.salience}, event={old_salience}"
                )
            if not 0.0 <= new_salience <= 1.0:
                raise ValueError("Consolidated salience must remain within 0..1")

            episode.salience = new_salience
            agent.state_version += 1

        case "memory.episode.forgotten":
            agent = _agent_for_event(world, event)
            _remove_memory(agent, str(event.payload["memory_id"]))
            agent.state_version += 1

        case _:
            raise ValueError(f"Unknown event type: {event.event_type}")

    world.last_sequence_number = event.sequence_number


def _agent_for_event(world: WorldState, event: WorldEvent):
    if event.actor_agent_id is None:
        raise ValueError(f"{event.event_type} requires actor_agent_id")
    try:
        return world.agents[event.actor_agent_id]
    except KeyError as error:
        raise ValueError(f"Unknown agent: {event.actor_agent_id}") from error


def _active_goal(agent, goal_id: str) -> GoalRecord:
    if agent.goals.active_goal_id != goal_id:
        raise ValueError(
            f"Goal {goal_id} is not active; active={agent.goals.active_goal_id}"
        )

    for goal in agent.goals.goals:
        if goal.goal_id == goal_id:
            if goal.status != GoalStatus.ACTIVE:
                raise ValueError(f"Goal {goal_id} is not in active status")
            return goal

    raise ValueError(f"Unknown goal ID: {goal_id}")


def _proposed_action(agent, action_id: str) -> ActionIntentRecord:
    for action in agent.actions.proposals:
        if action.action_id == action_id:
            if action.status != ActionStatus.PROPOSED:
                raise ValueError(
                    f"Action {action_id} is not proposed: {action.status}"
                )
            return action
    raise ValueError(f"Unknown action ID: {action_id}")


def state_hash_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _find_memory(agent, memory_id: str) -> EpisodicMemory:
    for episode in agent.memory.episodes:
        if episode.memory_id == memory_id:
            return episode
    raise ValueError(f"Unknown memory ID: {memory_id}")


def _remove_memory(agent, memory_id: str) -> None:
    remaining = [
        episode
        for episode in agent.memory.episodes
        if episode.memory_id != memory_id
    ]
    if len(remaining) == len(agent.memory.episodes):
        raise ValueError(f"Unknown memory ID: {memory_id}")
    agent.memory.episodes = remaining
